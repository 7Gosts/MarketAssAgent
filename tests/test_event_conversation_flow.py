from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

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
        self.seen_messages: list[str] = []

    def build_request_payload(self, *, messages, tools, max_tokens=None):
        return {
            "model": self.model,
            "messages": [message.to_openai_dict() for message in messages],
            "temperature": self.temperature,
            "tools": list(tools),
            "max_tokens": max_tokens,
        }

    async def complete(self, *, messages, tools, max_tokens=None):
        self.calls += 1
        self.seen_messages.append("\n".join(message.content for message in messages))
        return LLMResponse(
            message=Message(role="assistant", content="ETH 保持观察。"),
            raw={"id": f"response-{self.calls}"},
        )


class _HistorySearchLLM(_FinalAnswerLLM):
    async def complete(self, *, messages, tools, max_tokens=None):
        self.calls += 1
        self.seen_messages.append("\n".join(message.content for message in messages))
        if self.calls == 1:
            content = "ETH 当前先观察。"
        elif self.calls == 2:
            return LLMResponse(
                message=Message(
                    role="assistant",
                    tool_calls=(ToolCall(
                        id="history_1",
                        name="search_session_history",
                        arguments={"keyword": "ETH 多头机会", "days": 7, "limit": 20},
                    ),),
                ),
                raw={"id": f"response-{self.calls}"},
            )
        else:
            content = "最近一周你多次关注 ETH 多头机会。"
        return LLMResponse(
            message=Message(role="assistant", content=content),
            raw={"id": f"response-{self.calls}"},
        )


class _HistorySearchFailureLLM(_FinalAnswerLLM):
    async def complete(self, *, messages, tools, max_tokens=None):
        self.calls += 1
        self.seen_messages.append("\n".join(message.content for message in messages))
        if self.calls == 1:
            return LLMResponse(
                message=Message(
                    role="assistant",
                    tool_calls=(ToolCall(
                        id="history_error_1",
                        name="search_session_history",
                        arguments={"keyword": "ETH", "days": 7, "limit": 20},
                    ),),
                ),
                raw={"id": f"response-{self.calls}"},
            )
        content = "历史检索失败，本轮无法核对之前的对话。"
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


def test_background_plan_event_stays_in_context_for_the_next_user_turn(tmp_path: Path):
    llm = _FinalAnswerLLM()
    service = ConversationService(
        agent=MarketReActAgent(llm=llm),
        event_store=LocalSessionEventStore(tmp_path),
    )
    event_text = "【后台计划监控事件，非用户原话】ETH 15m 入场条件已触发。"
    monitor_meta = {**_meta(), "external_message_id": "plan_1_trigger_1"}

    asyncio.run(service.run(text=event_text, session_id="ignored", extra_meta=monitor_meta))
    asyncio.run(service.run(
        text="刚才的提醒是什么意思？",
        session_id="ignored",
        extra_meta={**_meta(), "external_message_id": "om_followup"},
    ))

    assert event_text in llm.seen_messages[0]
    assert event_text in llm.seen_messages[1]


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

    duplicate = asyncio.run(service.run(text="看看 ETH", session_id="ignored", extra_meta=_meta()))
    assert service.begin_delivery(
        duplicate,
        transport="feishu",
        tenant_or_app_id="tenant-a",
        destination="ou_alice",
    ) is False

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


def test_history_search_results_are_plain_messages_without_ids(tmp_path: Path):
    llm = _HistorySearchLLM()
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
    assert all("ev_" not in request and "evt_" not in request and "sess_" not in request
               for request in llm.seen_messages)
    scope = service._event_scope(session_id="ignored", extra_meta=second_meta)
    events = store.read_branch(scope=scope, session_id=scope.session_id)
    event_types = [event.event_type for event in events]
    assert event_types.count("assistant/attempt") == 0
    assert event_types.count("model/request") == 3
    result = next(event for event in events if event.event_type == "tool/result")
    assert result.payload["tool_name"] == "search_session_history"
    assert "ev_" not in str(result.payload["result"])
    assert "seq" not in str(result.payload["result"])


def test_history_lookup_error_is_returned_without_guard_retry(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "tools.session_history.query_session_history",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("history store unavailable")),
    )
    llm = _HistorySearchFailureLLM()
    agent = MarketReActAgent(llm=llm)
    store = LocalSessionEventStore(tmp_path)
    service = ConversationService(agent=agent, event_store=store)

    response = asyncio.run(service.run(text="之前 ETH 的建议是什么", session_id="ignored", extra_meta=_meta()))

    assert llm.calls == 2
    assert response.reply_text == "历史检索失败，本轮无法核对之前的对话。"
    assert response.pending_delivery
    scope = service._event_scope(session_id="ignored", extra_meta=_meta())
    events = store.read_branch(scope=scope, session_id=scope.session_id)
    assert events[-1].event_type == "assistant/message"
    assert not any(event.event_type == "assistant/attempt" for event in events)
    tool_result = next(event for event in events if event.event_type == "tool/result")
    assert tool_result.payload["status"] == "failed"
    assert response.pending_delivery["assistant_seq"] == events[-1].seq


def test_context_overflow_reply_is_persisted_and_deliverable(tmp_path: Path, monkeypatch):
    llm = _FinalAnswerLLM()
    agent = MarketReActAgent(llm=llm)
    store = LocalSessionEventStore(tmp_path)
    service = ConversationService(agent=agent, event_store=store)
    monkeypatch.setattr(
        "core.agent_loop.get_agent_context_limits",
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
        model_request_seq=request.seq,
        message=Message(
            role="assistant",
            tool_calls=(ToolCall(id="call_order", name="simulate_open_position", arguments={}),),
        ),
    )
    planned = journal.append_tool_plan(
        turn_user_seq=user.seq,
        assistant_seq=assistant.seq,
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
        turn_user_seq=user.seq,
        abort_nonce="om_1",
        reason="provider_error",
    )

    envelope = asyncio.run(service.run(text="看看 ETH", session_id="ignored", extra_meta=_meta()))

    assert llm.calls == 0
    assert envelope.meta["deduplicated"] is True
    assert envelope.meta["aborted"] is True
    assert "没有自动重放" in envelope.reply_text
