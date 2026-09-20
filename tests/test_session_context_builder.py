from __future__ import annotations

import json
from pathlib import Path

from core.conversation_scope import ConversationScope, stable_id
from core.message_protocol import Message, ToolCall
from infrastructure.memory.context_builder import SessionContextBuilder, request_payload_from_bytes
from infrastructure.memory.event_schema import canonical_json
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


def test_context_builder_replays_complete_turn_and_excludes_execution_events(tmp_path: Path):
    store, scope, journal = _journal(tmp_path)
    first_user = journal.append_user_message(
        text="看看 ETH",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_1",
    )
    first_request = journal.append_model_request(
        request_payload={
            "model": "test-model",
            "messages": [{"role": "system", "content": "system"}, {"role": "user", "content": "看看 ETH"}],
            "temperature": 0.2,
            "tools": [],
        },
        prompt_version="v1",
    )
    assistant_call = journal.append_assistant_message(
        model_request_event_id=first_request.event_id,
        message=Message(
            role="assistant",
            tool_calls=(ToolCall(id="call_1", name="fetch_market_data", arguments={"symbol": "ETHUSDT"}),),
        ),
        provider_response_id="response-1",
    )
    planned = journal.append_tool_plan(
        turn_user_event_id=first_user.event_id,
        assistant_event_id=assistant_call.event_id,
        call=ToolCall(id="call_1", name="fetch_market_data", arguments={"symbol": "ETHUSDT"}),
        tool_version="1",
        effect_class="pure_query",
    )
    journal.append_tool_started(operation_id=planned.operation_id, attempt_no=1)
    journal.append_tool_result(
        operation_id=planned.operation_id,
        provider_tool_call_id="call_1",
        tool_name="fetch_market_data",
        status="succeeded",
        result={"price": 2593.42},
    )
    second_request = journal.append_model_request(
        request_payload={"model": "test-model", "messages": [], "temperature": 0.2, "tools": []},
        prompt_version="v1",
    )
    journal.append_assistant_message(
        model_request_event_id=second_request.event_id,
        message=Message(role="assistant", content="ETH 当前 2593.42。"),
        provider_response_id="response-2",
    )
    journal.append_user_message(
        text="继续",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_2",
    )

    built = SessionContextBuilder(store).build(
        scope=scope,
        session_id=scope.session_id,
        system_prompt="system",
        recent_turns=8,
    )

    assert [message.role for message in built.messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert json.loads(built.messages[3].content) == {"price": 2593.42}
    assert not any(
        event.event_type in {"tool/call_started", "model/request"}
        for event in store.read_branch(scope=scope, session_id=scope.session_id)
        if event.event_id in built.evidence_event_ids
    )


def test_model_request_can_be_reproduced_byte_for_byte_inline_and_blob(tmp_path: Path):
    _, _, journal = _journal(tmp_path)
    small_payload = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0.2,
    }
    small = journal.append_model_request(request_payload=small_payload, prompt_version="v1")
    assert journal.load_model_request_bytes(small) == canonical_json(small_payload)

    large_payload = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "x" * (70 * 1024)}],
        "temperature": 0.2,
    }
    large = journal.append_model_request(request_payload=large_payload, prompt_version="v1")
    restored = journal.load_model_request_bytes(large)

    assert large.payload_blob_ref is not None
    assert restored == canonical_json(large_payload)
    assert request_payload_from_bytes(restored) == large_payload


def test_orphaned_tool_call_is_closed_without_replaying_effect(tmp_path: Path):
    store, scope, journal = _journal(tmp_path)
    user = journal.append_user_message(
        text="执行操作",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_orphan",
    )
    request = journal.append_model_request(
        request_payload={"model": "test", "messages": []},
        prompt_version="v1",
    )
    assistant = journal.append_assistant_message(
        model_request_event_id=request.event_id,
        message=Message(
            role="assistant",
            tool_calls=(ToolCall(id="call_orphan", name="write_tool", arguments={}),),
        ),
    )

    class _Spec:
        version = "1"
        effect_class = "opaque_effect"

    class _Registry:
        def get(self, _name: str):
            return _Spec()

    recovered = journal.recover_incomplete_tool_calls(_Registry())

    assert [event.event_type for event in recovered] == ["tool/call_planned", "tool/result"]
    assert recovered[-1].payload["status"] == "interrupted"
    built = SessionContextBuilder(store).build(
        scope=scope,
        session_id=scope.session_id,
        system_prompt="system",
    )
    assert [message.role for message in built.messages] == ["system", "user", "assistant", "tool"]
    assert user.event_id in built.evidence_event_ids
    assert assistant.event_id in built.evidence_event_ids


def test_context_omits_aborted_turn_without_a_user_visible_reply(tmp_path: Path):
    store, scope, journal = _journal(tmp_path)
    failed_user = journal.append_user_message(
        text="这个问题没有得到回答",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_failed",
    )
    request = journal.append_model_request(
        request_payload={"model": "test", "messages": []},
        prompt_version="v1",
    )
    journal.append_assistant_attempt(
        model_request_event_id=request.event_id,
        attempt_ordinal=1,
        status="failed",
        error_type="ProviderError",
    )
    journal.append_turn_aborted(
        turn_user_event_id=failed_user.event_id,
        abort_nonce="om_failed",
        reason="provider_error",
    )
    current = journal.append_user_message(
        text="新问题",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_current",
    )

    built = SessionContextBuilder(store).build(
        scope=scope,
        session_id=scope.session_id,
        system_prompt="system",
    )

    assert [message.content for message in built.messages] == ["system", "新问题"]
    assert current.event_id in built.evidence_event_ids
    assert failed_user.event_id not in built.evidence_event_ids


def test_context_keeps_aborted_turn_when_local_reply_was_delivered(tmp_path: Path):
    store, scope, journal = _journal(tmp_path)
    user = journal.append_user_message(
        text="超长问题",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_large",
    )
    journal.append_turn_aborted(
        turn_user_event_id=user.event_id,
        abort_nonce="om_large",
        reason="context_overflow",
    )
    local = journal.append_local_assistant_message(
        turn_user_event_id=user.event_id,
        reason="context_overflow",
        content="请缩短后重试。",
    )

    built = SessionContextBuilder(store).build(
        scope=scope,
        session_id=scope.session_id,
        system_prompt="system",
    )

    assert [message.content for message in built.messages] == ["system", "超长问题", "请缩短后重试。"]
    assert user.event_id in built.evidence_event_ids
    assert local.event_id in built.evidence_event_ids
