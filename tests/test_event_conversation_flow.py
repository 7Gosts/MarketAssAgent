from __future__ import annotations

import asyncio
import re
from pathlib import Path

from application.services.conversation_service import ConversationService
from core.agent import MarketReActAgent
from core.llm_client import LLMResponse
from core.message_protocol import Message, ToolCall
from infrastructure.memory.event_store import LocalSessionEventStore
from infrastructure.memory.session_journal import SessionEventJournal


class _FinalAnswerLLM:
    model = "test-model"
    temperature = 0.2

    def __init__(self) -> None:
        self.calls = 0

    def build_request_payload(self, *, messages, tools):
        return {
            "model": self.model,
            "messages": [message.to_openai_dict() for message in messages],
            "temperature": self.temperature,
            "tools": list(tools),
        }

    async def complete(self, *, messages, tools):
        self.calls += 1
        return LLMResponse(
            message=Message(role="assistant", content="ETH 保持观察。"),
            raw={"id": f"response-{self.calls}"},
        )


class _HistoryRepairLLM(_FinalAnswerLLM):
    async def complete(self, *, messages, tools):
        self.calls += 1
        if self.calls == 1:
            content = "ETH 当前先观察。"
        elif self.calls == 2:
            content = "最近一周你一直要求找 ETH 多头机会。"
        else:
            event_ids = re.findall(r"ev_[0-9a-f]{40}", messages[-1].content)
            content = f"最近一周你多次关注 ETH 多头机会。[mem:{event_ids[0]}]"
        return LLMResponse(
            message=Message(role="assistant", content=content),
            raw={"id": f"response-{self.calls}"},
        )


class _FailedHistoryRepairLLM(_FinalAnswerLLM):
    async def complete(self, *, messages, tools):
        self.calls += 1
        if self.calls == 1:
            content = "ETH 当前先观察。"
        elif self.calls == 2:
            content = "最近一周你一直要求找 ETH 多头机会。"
        else:
            raise RuntimeError("repair provider unavailable")
        return LLMResponse(
            message=Message(role="assistant", content=content),
            raw={"id": f"response-{self.calls}"},
        )


def _meta() -> dict[str, str]:
    return {
        "transport": "feishu",
        "tenant_id": "tenant-a",
        "visibility_scope": "private",
        "visibility_scope_id": "ou_alice",
        "actor_id": "ou_alice",
        "external_message_id": "om_1",
    }


def test_event_conversation_flow_is_deduplicated_and_does_not_dual_write(tmp_path: Path):
    llm = _FinalAnswerLLM()
    agent = MarketReActAgent(llm=llm)
    store = LocalSessionEventStore(tmp_path)
    service = ConversationService(
        agent=agent,
        event_store=store,
    )

    first = asyncio.run(service.run(text="看看 ETH", session_id="ignored", extra_meta=_meta()))
    repeated = asyncio.run(service.run(text="看看 ETH", session_id="ignored", extra_meta=_meta()))

    assert first.reply_text == "ETH 保持观察。"
    assert repeated.reply_text == first.reply_text
    assert repeated.meta["deduplicated"] is True
    assert llm.calls == 1

    scope = service._event_scope(session_id="ignored", extra_meta=_meta())
    events = store.read_branch(scope=scope, session_id=scope.session_id)
    assert [event.event_type for event in events] == [
        "session/header",
        "user/message",
        "model/request",
        "assistant/message",
    ]
    assert [event.actor_id for event in events] == [None, "ou_alice", None, None]


def test_delivery_is_persisted_before_and_after_send(tmp_path: Path):
    llm = _FinalAnswerLLM()
    agent = MarketReActAgent(llm=llm)
    store = LocalSessionEventStore(tmp_path)
    service = ConversationService(
        agent=agent,
        event_store=store,
    )
    envelope = asyncio.run(service.run(text="看看 ETH", session_id="ignored", extra_meta=_meta()))

    assert service.begin_delivery(
        envelope,
        transport="feishu",
        tenant_or_app_id="tenant-a",
        destination="ou_alice",
    ) is True
    service.finish_delivery(envelope, status="delivered", provider_message_id="om_reply")

    scope = service._event_scope(session_id="ignored", extra_meta=_meta())
    event_types = [event.event_type for event in store.read_branch(scope=scope, session_id=scope.session_id)]
    assert event_types[-3:] == ["delivery/planned", "delivery/started", "delivery/result"]


def test_started_delivery_is_not_sent_again_when_result_is_unknown(tmp_path: Path):
    llm = _FinalAnswerLLM()
    agent = MarketReActAgent(llm=llm)
    store = LocalSessionEventStore(tmp_path)
    service = ConversationService(
        agent=agent,
        event_store=store,
    )
    envelope = asyncio.run(service.run(text="看看 ETH", session_id="ignored", extra_meta=_meta()))
    assert service.begin_delivery(
        envelope,
        transport="feishu",
        tenant_or_app_id="tenant-a",
        destination="ou_alice",
    ) is True

    duplicate = asyncio.run(service.run(text="看看 ETH", session_id="ignored", extra_meta=_meta()))
    assert service.begin_delivery(
        duplicate,
        transport="feishu",
        tenant_or_app_id="tenant-a",
        destination="ou_alice",
    ) is False


def test_historical_claim_is_refetched_repaired_and_rendered_without_internal_citation(tmp_path: Path):
    llm = _HistoryRepairLLM()
    agent = MarketReActAgent(llm=llm)
    store = LocalSessionEventStore(tmp_path)
    service = ConversationService(agent=agent, event_store=store)

    first_meta = _meta()
    asyncio.run(service.run(text="ETH 多头机会", session_id="ignored", extra_meta=first_meta))
    second_meta = {**_meta(), "external_message_id": "om_2"}
    response = asyncio.run(
        service.run(text="总结最近一周 ETH", session_id="ignored", extra_meta=second_meta)
    )

    assert llm.calls == 3
    assert response.reply_text == "最近一周你多次关注 ETH 多头机会。"
    assert "[mem:" not in response.reply_text
    scope = service._event_scope(session_id="ignored", extra_meta=second_meta)
    events = store.read_branch(scope=scope, session_id=scope.session_id)
    event_types = [event.event_type for event in events]
    assert event_types.count("assistant/attempt") == 1
    assert event_types.count("model/request") == 3
    rejected = next(event for event in events if event.event_type == "assistant/attempt")
    assert "一直要求" in str((rejected.payload or {}).get("content") or "")
    assert (rejected.payload or {}).get("details", {}).get("reasons")
    assert "[mem:" in str((events[-1].payload or {}).get("provider_content") or "")


def test_failed_historical_claim_repair_is_persisted_as_local_reply(tmp_path: Path):
    llm = _FailedHistoryRepairLLM()
    agent = MarketReActAgent(llm=llm)
    store = LocalSessionEventStore(tmp_path)
    service = ConversationService(agent=agent, event_store=store)

    asyncio.run(service.run(text="ETH 多头机会", session_id="ignored", extra_meta=_meta()))
    second_meta = {**_meta(), "external_message_id": "om_2"}
    response = asyncio.run(
        service.run(text="总结最近一周 ETH", session_id="ignored", extra_meta=second_meta)
    )

    assert llm.calls == 3
    assert response.reply_text == "当前可见记录不足以确认。"
    assert response.pending_delivery
    scope = service._event_scope(session_id="ignored", extra_meta=second_meta)
    events = store.read_branch(scope=scope, session_id=scope.session_id)
    assert events[-1].event_type == "assistant/local_message"
    assert (events[-1].payload or {}).get("reason") == "historical_claim_repair_failed"
    attempts = [event for event in events if event.event_type == "assistant/attempt"]
    assert [(event.payload or {}).get("status") for event in attempts] == ["rejected", "failed"]
    assert response.pending_delivery["assistant_event_id"] == events[-1].event_id


def test_context_overflow_reply_is_persisted_and_deliverable(tmp_path: Path, monkeypatch):
    llm = _FinalAnswerLLM()
    agent = MarketReActAgent(llm=llm)
    store = LocalSessionEventStore(tmp_path)
    service = ConversationService(agent=agent, event_store=store)
    monkeypatch.setattr(
        "application.services.conversation_service.get_agent_context_limits",
        lambda: {
            "context_window_tokens": 64,
            "reserve_tokens": 32,
            "recent_fraction": 0.3,
            "estimator_margin": 0.08,
        },
    )

    envelope = asyncio.run(service.run(text="x" * 1000, session_id="ignored", extra_meta=_meta()))

    assert envelope.meta["request_id"] == "om_1"
    assert envelope.pending_delivery
    assert llm.calls == 0
    assert service.begin_delivery(
        envelope,
        transport="feishu",
        tenant_or_app_id="tenant-a",
        destination="ou_alice",
    ) is True
    scope = service._event_scope(session_id="ignored", extra_meta=_meta())
    event_types = [event.event_type for event in store.read_branch(scope=scope, session_id=scope.session_id)]
    assert event_types == [
        "session/header",
        "user/message",
        "turn/aborted",
        "assistant/local_message",
        "delivery/planned",
        "delivery/started",
    ]


def test_duplicate_inbound_does_not_retry_an_indeterminate_tool(tmp_path: Path):
    llm = _FinalAnswerLLM()
    agent = MarketReActAgent(llm=llm)
    store = LocalSessionEventStore(tmp_path)
    service = ConversationService(agent=agent, event_store=store)
    scope = service._event_scope(session_id="ignored", extra_meta=_meta())
    journal = SessionEventJournal(store=store, scope=scope, session_id=scope.session_id)
    journal.ensure_session()
    user = journal.append_user_message(
        text="开一个 ETH 模拟多单",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_1",
    )
    request = journal.append_model_request(
        request_payload={"model": "test-model", "messages": []},
        prompt_version="test-v1",
    )
    assistant = journal.append_assistant_message(
        model_request_event_id=request.event_id,
        message=Message(
            role="assistant",
            tool_calls=(ToolCall(id="call_order", name="simulate_open_position", arguments={}),),
        ),
    )
    planned = journal.append_tool_plan(
        turn_user_event_id=user.event_id,
        assistant_event_id=assistant.event_id,
        call=ToolCall(
            id="call_order",
            name="simulate_open_position",
            arguments={},
        ),
        tool_version="1",
        effect_class="idempotent_write",
    )
    journal.append_tool_started(operation_id=planned.operation_id, attempt_no=1)

    envelope = asyncio.run(
        service.run(text="开一个 ETH 模拟多单", session_id="ignored", extra_meta=_meta())
    )

    assert llm.calls == 0
    assert envelope.meta["deduplicated"] is True
    assert "结果不确定" in envelope.reply_text
    events = store.read_branch(scope=scope, session_id=scope.session_id)
    assert any(
        event.event_type == "tool/result" and (event.payload or {}).get("status") == "unknown"
        for event in events
    )


def test_duplicate_inbound_does_not_restart_an_aborted_turn(tmp_path: Path):
    llm = _FinalAnswerLLM()
    agent = MarketReActAgent(llm=llm)
    store = LocalSessionEventStore(tmp_path)
    service = ConversationService(agent=agent, event_store=store)
    scope = service._event_scope(session_id="ignored", extra_meta=_meta())
    journal = SessionEventJournal(store=store, scope=scope, session_id=scope.session_id)
    journal.ensure_session()
    user = journal.append_user_message(
        text="看看 ETH",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_1",
    )
    journal.append_turn_aborted(
        turn_user_event_id=user.event_id,
        abort_nonce="om_1",
        reason="provider_error",
    )

    envelope = asyncio.run(service.run(text="看看 ETH", session_id="ignored", extra_meta=_meta()))

    assert llm.calls == 0
    assert envelope.meta["deduplicated"] is True
    assert "没有自动重放" in envelope.reply_text
