from __future__ import annotations

from pathlib import Path

import pytest

from core.conversation_scope import ConversationScope
from core.message_protocol import Message
from infrastructure.memory.context_builder import SessionContextBuilder
from infrastructure.memory.context_compactor import ContextBudget, ContextOverflow, SessionCompactor
from infrastructure.memory.event_store import LocalSessionEventStore
from infrastructure.memory.session_journal import SessionEventJournal


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
    for ordinal in range(1, 5):
        _append_completed_turn(journal, ordinal, "ETH 多头计划 " * 80)
    current = journal.append_user_message(
        text="总结最近判断",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_current",
    )

    compactor = SessionCompactor(store)
    built = compactor.prepare(
        scope=scope,
        session_id=scope.session_id,
        system_prompt="system",
        tool_schemas=[],
        budget=ContextBudget(context_window_tokens=1200, reserve_tokens=300),
    )

    events = store.read_branch(scope=scope, session_id=scope.session_id)
    checkpoints = [event for event in events if event.event_type == "context/compaction"]
    assert len(checkpoints) == 1
    assert checkpoints[0].payload["first_kept_event_id"] in built.evidence_index
    assert current.event_id in built.evidence_index
    assert built.checkpoint_event_id == checkpoints[0].event_id
    assert built.estimated_tokens <= 900


def test_compactor_aborts_when_current_turn_alone_exceeds_budget(tmp_path: Path):
    store, scope, journal = _journal(tmp_path)
    journal.append_user_message(
        text="超长输入" * 3000,
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_large",
    )

    with pytest.raises(ContextOverflow):
        SessionCompactor(store).prepare(
            scope=scope,
            session_id=scope.session_id,
            system_prompt="system",
            tool_schemas=[],
            budget=ContextBudget(context_window_tokens=1000, reserve_tokens=250),
        )
