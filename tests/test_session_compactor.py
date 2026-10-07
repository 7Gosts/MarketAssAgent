from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.conversation_scope import ConversationScope
from core.llm_client import LLMResponse
from core.message_protocol import Message
from infrastructure.memory.context_builder import SessionContextBuilder
from infrastructure.memory.context_compactor import ContextBudget, ContextOverflow, SessionCompactor
from infrastructure.memory.event_schema import MAX_INLINE_PAYLOAD_BYTES, canonical_json
from infrastructure.memory.event_store import LocalSessionEventStore
from infrastructure.memory.session_journal import SessionEventJournal


class _SummaryLLM:
    def __init__(self, content="用户要求亏损后暂停加仓。", finish_reason="stop"):
        self.content = content
        self.finish_reason = finish_reason
        self.requests = []

    async def complete(self, *, messages, tools, max_tokens):
        self.requests.append((messages, max_tokens))
        return LLMResponse(message=Message(role="assistant", content=self.content), finish_reason=self.finish_reason)


def _journal(tmp_path: Path) -> tuple[LocalSessionEventStore, ConversationScope, SessionEventJournal]:
    store = LocalSessionEventStore(tmp_path)
    scope = ConversationScope(
        transport="feishu",
        tenant_id="tenant-a",
        visibility_scope="private",
        visibility_scope_id="ou_alice",
        actor_id="ou_alice",
    )
    journal = SessionEventJournal(store=store, scope=scope, session_id=scope.session_id)
    journal.ensure_session()
    return store, scope, journal


def _append_completed_turn(journal: SessionEventJournal, ordinal: int, text: str) -> None:
    journal.append_user_message(
        text=f"问题 {ordinal}: {text}",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id=f"om_{ordinal}",
    )
    request = journal.append_model_request(
        request_payload={"model": "test", "messages": []},
        prompt_version="v1",
    )
    journal.append_assistant_message(
        model_request_event_id=request.event_id,
        message=Message(role="assistant", content=f"回答 {ordinal}: {text}"),
        provider_response_id=f"response_{ordinal}",
    )


def test_compactor_keeps_complete_recent_turns_and_persists_checkpoint(tmp_path: Path):
    store, scope, journal = _journal(tmp_path)
    for ordinal in range(1, 9):
        _append_completed_turn(journal, ordinal, "ETH 多头计划 " * 80)
    current = journal.append_user_message(
        text="总结最近判断",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_current",
    )

    compactor = SessionCompactor(store)
    llm = _SummaryLLM()
    budget = ContextBudget(context_window_tokens=5000, reserve_tokens=1000)
    built = asyncio.run(compactor.prepare(
        scope=scope,
        session_id=scope.session_id,
        system_prompt="system",
        tool_schemas=[],
        budget=budget,
        llm=llm,
    ))

    events = store.read_branch(scope=scope, session_id=scope.session_id)
    checkpoints = [event for event in events if event.event_type == "context/compaction"]
    assert len(checkpoints) > 1
    assert checkpoints[-1].payload["first_kept_event_id"] in built.evidence_index
    assert current.event_id in built.evidence_index
    assert built.checkpoint_event_id == checkpoints[-1].event_id
    assert built.estimated_tokens <= budget.prompt_tokens
    for messages, max_tokens in llm.requests:
        assert compactor._request_tokens(messages, [], budget) <= budget.prompt_tokens
        assert max_tokens <= budget.reserve_tokens


def test_compactor_aborts_when_current_turn_alone_exceeds_budget(tmp_path: Path):
    store, scope, journal = _journal(tmp_path)
    journal.append_user_message(
        text="超长输入" * 3000,
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_large",
    )

    with pytest.raises(ContextOverflow):
        asyncio.run(SessionCompactor(store).prepare(
            scope=scope,
            session_id=scope.session_id,
            system_prompt="system",
            tool_schemas=[],
            budget=ContextBudget(context_window_tokens=1000, reserve_tokens=250),
            llm=_SummaryLLM(),
        ))


def test_large_checkpoints_survive_reload_and_repeated_compaction(tmp_path: Path):
    store, scope, journal = _journal(tmp_path)
    _append_completed_turn(journal, 0, "原始约束")
    older = store.read_branch(scope=scope, session_id=scope.session_id)
    _append_completed_turn(journal, 1, "新增约束：亏损后暂停加仓。")
    current = journal.append_user_message(
        text="继续复盘", transport="feishu", tenant_or_app_id="tenant-a", external_message_id="om_current",
    )
    events = store.read_branch(scope=scope, session_id=scope.session_id)
    first_kept = events[len(older)]
    large_summary = "持仓" * 12000
    checkpoint = journal.append_compaction(
        summary=large_summary, covered_from_event_id=older[1].event_id,
        covered_to_event_id=older[-1].event_id, first_kept_event_id=first_kept.event_id,
        input_tokens=30000, source_event_ids=[older[1].event_id], lower_checkpoint_event_ids=[],
    )
    assert checkpoint.payload_blob_ref is not None
    assert len(canonical_json(checkpoint.payload)) <= MAX_INLINE_PAYLOAD_BYTES
    reloaded_store = LocalSessionEventStore(tmp_path)
    rebuilt = SessionContextBuilder(reloaded_store).build(
        scope=scope, session_id=scope.session_id, system_prompt="system",
    )
    assert rebuilt.messages[1].content.endswith(large_summary)

    llm = _SummaryLLM()
    built = asyncio.run(SessionCompactor(reloaded_store).prepare(
        scope=scope, session_id=scope.session_id, system_prompt="system", tool_schemas=[],
        budget=ContextBudget(), llm=llm, force=True,
    ))
    source = json.loads(llm.requests[0][0][-1].content)
    assert source["previous_summary"] == large_summary
    assert any("亏损后暂停加仓" in item["message"]["content"] for item in source["new_history"])
    assert built.messages[1].content.endswith(llm.content)
    assert current.event_id in built.evidence_index
    assert built.checkpoint_event_id != checkpoint.event_id


@pytest.mark.parametrize("content, finish_reason", [
    ("", "stop"), ("摘要未完成", "length"), ("重复原文" * 3000, "stop"),
])
def test_invalid_summary_does_not_replace_history(tmp_path: Path, content, finish_reason):
    store, scope, journal = _journal(tmp_path)
    _append_completed_turn(journal, 0, "历史" * 500)
    journal.append_user_message(text="继续", transport="feishu", tenant_or_app_id="tenant-a", external_message_id="om_current")
    with pytest.raises(ContextOverflow):
        asyncio.run(SessionCompactor(store).prepare(
            scope=scope, session_id=scope.session_id, system_prompt="system", tool_schemas=[],
            budget=ContextBudget(), llm=_SummaryLLM(content, finish_reason), force=True,
        ))
    assert not any(event.event_type == "context/compaction" for event in store.read_branch(scope=scope, session_id=scope.session_id))
