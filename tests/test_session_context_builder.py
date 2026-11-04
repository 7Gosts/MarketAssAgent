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
        model_request_seq=first_request.seq,
        message=Message(
            role="assistant",
            tool_calls=(ToolCall(id="call_1", name="search_session_history", arguments={"keyword": "ETH"}),),
        ),
        provider_response_id="response-1",
    )
    planned = journal.append_tool_plan(
        turn_user_seq=first_user.seq,
        assistant_seq=assistant_call.seq,
        call=ToolCall(id="call_1", name="search_session_history", arguments={"keyword": "ETH"}),
        tool_version="1",
        effect_class="pure_query",
    )
    journal.append_tool_started(operation_id=planned.operation_id, attempt_no=1)
    tool_result_event = journal.append_tool_result(
        operation_id=planned.operation_id,
        provider_tool_call_id="call_1",
        tool_name="search_session_history",
        status="succeeded",
        result={
            "status": "success",
            "keyword": "ETH",
            "time_range": {"days": 7},
            "truncated": False,
            "items": [{
                "occurred_at": first_user.occurred_at,
                "role": "user",
                "content": "ETH 当时是什么情况？",
            }, {
                "occurred_at": "2026-11-03T17:13:51Z",
                "role": "assistant",
                "content": "历史回复原文。",
            }],
        },
    )
    second_request = journal.append_model_request(
        request_payload={"model": "test-model", "messages": [], "temperature": 0.2, "tools": []},
        prompt_version="v1",
    )
    journal.append_assistant_message(
        model_request_seq=second_request.seq,
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
    tool_payload = json.loads(built.messages[3].content)
    assert tool_payload == tool_result_event.payload["result"]
    assert built.messages[3].content == json.dumps(tool_payload, ensure_ascii=False, separators=(",", ":"))
    assert built.messages[4].content == "ETH 当前 2593.42。"


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


def test_responses_input_uses_blob_when_model_request_is_large(tmp_path: Path):
    _, _, journal = _journal(tmp_path)
    payload = {
        "model": "gpt-5.6-luna",
        "input": [{"role": "user", "content": "x" * (70 * 1024)}],
        "tools": [],
        "store": False,
    }

    event = journal.append_model_request(request_payload=payload, prompt_version="responses-v1")

    assert event.payload_blob_ref is not None
    assert journal.load_model_request_bytes(event) == canonical_json(payload)


def test_missing_checkpoint_blob_falls_back_without_hiding_history(tmp_path: Path):
    store, scope, journal = _journal(tmp_path)
    users = [journal.append_user_message(text=f"约束 {i}", transport="feishu", tenant_or_app_id="tenant-a",
                                         external_message_id=f"om_{i}") for i in range(3)]
    older = journal.append_compaction(
        summary="旧约束", covered_from_seq=users[0].seq, covered_to_seq=users[0].seq,
        first_kept_seq=users[1].seq, input_tokens=100, lower_checkpoint_seqs=[],
    )
    newer = journal.append_compaction(
        summary="大摘要" * 10000, covered_from_seq=users[0].seq, covered_to_seq=users[1].seq,
        first_kept_seq=users[2].seq, input_tokens=40000,
        lower_checkpoint_seqs=[older.seq],
    )
    assert newer.payload_blob_ref
    for path in store.blobs_dir.rglob(newer.payload_blob_ref["hash"]):
        path.unlink()
    built = SessionContextBuilder(store).build(scope=scope, session_id=scope.session_id, system_prompt="system")
    assert built.checkpoint_seq == older.seq
    assert built.messages[1].content.endswith("旧约束")
    assert [message.content for message in built.messages[2:]] == ["约束 1", "约束 2"]


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
        model_request_seq=request.seq,
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
    assert built.messages[1].content == "执行操作"


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
        model_request_seq=request.seq,
        attempt_ordinal=1,
        status="failed",
        error_type="ProviderError",
    )
    journal.append_turn_aborted(
        turn_user_seq=failed_user.seq,
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


def test_context_keeps_aborted_turn_when_local_reply_was_delivered(tmp_path: Path):
    store, scope, journal = _journal(tmp_path)
    user = journal.append_user_message(
        text="超长问题",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_large",
    )
    journal.append_turn_aborted(
        turn_user_seq=user.seq,
        abort_nonce="om_large",
        reason="context_overflow",
    )
    journal.append_local_assistant_message(
        turn_user_seq=user.seq,
        reason="context_overflow",
        content="请缩短后重试。",
    )

    built = SessionContextBuilder(store).build(
        scope=scope,
        session_id=scope.session_id,
        system_prompt="system",
    )

    assert [message.content for message in built.messages] == ["system", "超长问题", "请缩短后重试。"]
