from __future__ import annotations

from pathlib import Path

from core.conversation_scope import ConversationScope, stable_id
from core.message_protocol import Message
from core.tool_protocol import ToolContext
from infrastructure.memory.event_store import LocalSessionEventStore
from infrastructure.memory.session_journal import SessionEventJournal
from tools.session_history import search_session_history


def _scope(scope_type: str, scope_id: str) -> ConversationScope:
    return ConversationScope(
        transport="feishu",
        tenant_id="tenant-a",
        visibility_scope=scope_type,  # type: ignore[arg-type]
        visibility_scope_id=scope_id,
        actor_id="ou_alice",
    )


def _append_user(store: LocalSessionEventStore, scope: ConversationScope, session_name: str, text: str):
    session_id = stable_id("sess", session_name)
    journal = SessionEventJournal(store=store, scope=scope, session_id=session_id)
    journal.ensure_session()
    return journal.append_user_message(
        text=text,
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id=f"om_{session_name}",
    )


def _append_assistant(store: LocalSessionEventStore, scope: ConversationScope, session_id: str, text: str):
    journal = SessionEventJournal(store=store, scope=scope, session_id=session_id)
    request = journal.append_model_request(request_payload={"model": "test", "messages": []}, prompt_version="v1")
    return journal.append_assistant_message(
        model_request_event_id=request.event_id,
        message=Message(role="assistant", content=text),
    )


def test_history_search_is_cross_session_but_never_cross_scope(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    private = _scope("private", "ou_alice")
    group = _scope("group", "oc_team")
    first = _append_user(store, private, "private-one", "ETH 我继续看多")
    assistant = _append_assistant(store, private, first.session_id, "先等反弹出现受阻再考虑。")
    _append_user(store, private, "private-two", "ETH 找一个回踩多头机会")
    _append_user(store, group, "group-one", "群里只讨论半导体")

    private_result = search_session_history(
        "ETH",
        context=ToolContext(
            session_id=private.session_id,
            request_id="request",
            conversation_scope=private,
            event_store=store,
        ),
    )
    group_result = search_session_history(
        "ETH",
        context=ToolContext(
            session_id=group.session_id,
            request_id="request",
            conversation_scope=group,
            event_store=store,
        ),
    )

    assert {item["content"] for item in private_result["items"]} == {
        "ETH 我继续看多",
        "先等反弹出现受阻再考虑。",
        "ETH 找一个回踩多头机会",
    }
    assert all(set(item) == {"occurred_at", "role", "content"} for item in private_result["items"])
    assert {item["role"] for item in private_result["items"]} == {"user", "assistant"}
    assert any(
        item["role"] == "assistant" and item["occurred_at"] == assistant.occurred_at
        for item in private_result["items"]
    )
    assert private_result["truncated"] is False
    assert group_result["items"] == []


def test_history_search_marks_truncated_results_in_receipt(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope("private", "ou_alice")
    _append_user(store, scope, "one", "ETH 第一次看多")
    _append_user(store, scope, "two", "ETH 第二次看多")

    result = search_session_history(
        "ETH",
        limit=1,
        context=ToolContext(
            session_id=scope.session_id,
            request_id="request",
            conversation_scope=scope,
            event_store=store,
        ),
    )

    assert len(result["items"]) == 1
    assert result["truncated"] is True


def test_history_search_excludes_the_current_question(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope("private", "ou_alice")
    previous = _append_user(store, scope, "previous", "ETH 多头机会")
    current = _append_user(store, scope, "current", "最近一周我是不是一直找 ETH 多头机会")

    result = search_session_history(
        keyword="ETH 多头机会",
        days=7,
        limit=20,
        context=ToolContext(
            session_id=scope.session_id,
            request_id="req-current",
            turn_user_event_id=current.event_id,
            conversation_scope=scope,
            event_store=store,
        ),
    )

    assert len(result["items"]) == 1
    assert result["items"][0]["content"] == "ETH 多头机会"
    assert previous.event_id not in str(result)
