from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.conversation_scope import ConversationScope, stable_id
from infrastructure.memory.event_schema import canonical_json
from infrastructure.memory.event_store import (
    CorruptSessionLog,
    LocalSessionEventStore,
    StoreBusy,
    StoreUnhealthy,
)


def _scope(scope_id: str = "ou_alice") -> ConversationScope:
    return ConversationScope(
        transport="feishu",
        tenant_id="tenant-a",
        visibility_scope="private",
        visibility_scope_id=scope_id,
        actor_id=scope_id,
    )


def test_append_is_durable_and_idempotent(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    session_id = stable_id("sess", "test")
    header = store.create_session(scope=scope, session_id=session_id)
    branch_id = stable_id("br", session_id, "main")

    first = store.append(
        scope=scope,
        session_id=session_id,
        branch_id=branch_id,
        parent_event_id=header.event_id,
        event_type="user/message",
        payload={"text": "看看 ETH"},
        idempotency_scope="inbound",
        idempotency_key_object={
            "transport": "feishu",
            "tenant_or_app_id": "tenant-a",
            "external_message_id": "message-001",
        },
        expected_parent_event_id=header.event_id,
    )
    repeated = store.append(
        scope=scope,
        session_id=session_id,
        branch_id=branch_id,
        parent_event_id=header.event_id,
        event_type="user/message",
        payload={"text": "看看 ETH"},
        idempotency_scope="inbound",
        idempotency_key_object={
            "transport": "feishu",
            "tenant_or_app_id": "tenant-a",
            "external_message_id": "message-001",
        },
        expected_parent_event_id=header.event_id,
    )

    assert repeated.deduplicated is True
    assert repeated.event.event_id == first.event.event_id
    assert [event.seq for event in store.read_events(scope=scope, session_id=session_id)] == [1, 2]


def test_visibility_scopes_do_not_share_events(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    alice = _scope("ou_alice")
    bob = _scope("ou_bob")

    session_id = stable_id("sess", "shared-name")
    store.create_session(scope=alice, session_id=session_id)
    store.create_session(scope=bob, session_id=session_id)
    alice_events = store.read_events(scope=alice, session_id=session_id)
    bob_events = store.read_events(scope=bob, session_id=session_id)

    assert alice_events[0].visibility_scope_id == "ou_alice"
    assert bob_events[0].visibility_scope_id == "ou_bob"
    assert store.event_path(scope=alice, session_id=session_id) != store.event_path(
        scope=bob,
        session_id=session_id,
    )


def test_concurrent_duplicate_append_writes_one_event(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    session_id = stable_id("sess", "concurrent")
    header = store.create_session(scope=scope, session_id=session_id)
    branch_id = stable_id("br", session_id, "main")

    def append_once() -> str:
        return store.append(
            scope=scope,
            session_id=session_id,
            branch_id=branch_id,
            parent_event_id=header.event_id,
            event_type="user/message",
            payload={"text": "same message"},
            idempotency_scope="inbound",
            idempotency_key_object={
                "transport": "feishu",
                "tenant_or_app_id": "tenant-a",
                "external_message_id": "message-concurrent",
            },
            expected_parent_event_id=header.event_id,
        ).event.event_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        event_ids = list(pool.map(lambda _: append_once(), range(32)))

    assert len(set(event_ids)) == 1
    assert len(store.read_events(scope=scope, session_id=session_id)) == 2


def test_truncated_tail_is_removed_before_next_append(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    session_id = stable_id("sess", "recovery")
    header = store.create_session(scope=scope, session_id=session_id)
    branch_id = stable_id("br", session_id, "main")
    path = store.event_path(scope=scope, session_id=session_id)
    with path.open("ab") as handle:
        handle.write(b'{"partial":')

    result = store.append(
        scope=scope,
        session_id=session_id,
        branch_id=branch_id,
        parent_event_id=header.event_id,
        event_type="user/message",
        payload={"text": "after recovery"},
        idempotency_scope="inbound",
        idempotency_key_object={
            "transport": "feishu",
            "tenant_or_app_id": "tenant-a",
            "external_message_id": "message-after-recovery",
        },
        expected_parent_event_id=header.event_id,
    )

    assert result.event.seq == 2
    assert len(store.read_events(scope=scope, session_id=session_id)) == 2
    for line in path.read_text(encoding="utf-8").splitlines():
        json.loads(line)
    audits = list((tmp_path / "audit").glob("*/recovery.jsonl"))
    assert len(audits) == 1
    assert json.loads(audits[0].read_text(encoding="utf-8").splitlines()[0])["action"] == "tail_truncated"


def test_middle_corruption_fails_closed(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    session_id = stable_id("sess", "corrupt")
    header = store.create_session(scope=scope, session_id=session_id)
    branch_id = stable_id("br", session_id, "main")
    store.append(
        scope=scope,
        session_id=session_id,
        branch_id=branch_id,
        parent_event_id=header.event_id,
        event_type="user/message",
        payload={"text": "hello"},
        idempotency_scope="inbound",
        idempotency_key_object={
            "transport": "feishu",
            "tenant_or_app_id": "tenant-a",
            "external_message_id": "message-corrupt",
        },
        expected_parent_event_id=header.event_id,
    )
    path = store.event_path(scope=scope, session_id=session_id)
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["payload"]["main_branch_id"] = "tampered"
    lines[0] = json.dumps(first, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(CorruptSessionLog):
        store.read_events(scope=scope, session_id=session_id)
    assert list((tmp_path / "quarantine").rglob(f"{session_id}.json"))


def test_branch_head_is_independent_from_file_tail(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    session_id = stable_id("sess", "branch")
    main_branch_id = stable_id("br", session_id, "main")
    retry_branch_id = stable_id("br", session_id, "retry")
    header = store.create_session(scope=scope, session_id=session_id, branch_id=main_branch_id)
    user = store.append(
        scope=scope,
        session_id=session_id,
        branch_id=main_branch_id,
        parent_event_id=header.event_id,
        event_type="user/message",
        payload={"text": "original"},
        idempotency_scope="inbound",
        idempotency_key_object={
            "transport": "feishu",
            "tenant_or_app_id": "tenant-a",
            "external_message_id": "message-original",
        },
        expected_parent_event_id=header.event_id,
    ).event
    branch_created = store.append(
        scope=scope,
        session_id=session_id,
        branch_id=main_branch_id,
        parent_event_id=user.event_id,
        event_type="branch/created",
        payload={"new_branch_id": retry_branch_id, "fork_parent_event_id": user.event_id},
        idempotency_scope="branch_created",
        idempotency_key_object={
            "session_id": session_id,
            "fork_parent_event_id": user.event_id,
            "new_branch_id": retry_branch_id,
        },
        expected_parent_event_id=user.event_id,
    ).event
    request_body = canonical_json({"model": "test", "messages": []})
    request = store.append(
        scope=scope,
        session_id=session_id,
        branch_id=retry_branch_id,
        parent_event_id=user.event_id,
        event_type="model/request",
        payload={
            "request_content_hash": hashlib.sha256(request_body).hexdigest(),
            "model": "test",
            "parameters": {},
            "prompt_version": "test",
            "checkpoint_event_id": None,
            "request": {"model": "test", "messages": []},
        },
        idempotency_scope="model_request",
        idempotency_key_object={
            "session_id": session_id,
            "branch_id": retry_branch_id,
            "parent_event_id": user.event_id,
            "request_content_hash": hashlib.sha256(request_body).hexdigest(),
        },
        expected_parent_event_id=user.event_id,
    ).event
    retry = store.append(
        scope=scope,
        session_id=session_id,
        branch_id=retry_branch_id,
        parent_event_id=request.event_id,
        event_type="assistant/message",
        payload={
            "model_request_event_id": request.event_id,
            "content": "revised",
            "tool_calls": [],
        },
        idempotency_scope="assistant_message",
        idempotency_key_object={
            "session_id": session_id,
            "model_request_event_id": request.event_id,
            "response_identity": "assistant-retry",
        },
        expected_parent_event_id=request.event_id,
    ).event

    assert store.branch_head(scope=scope, session_id=session_id, branch_id=main_branch_id) == branch_created.event_id
    assert store.branch_head(scope=scope, session_id=session_id, branch_id=retry_branch_id) == retry.event_id


def test_branch_activation_controls_default_replay(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    session_id = stable_id("sess", "activation")
    main_branch_id = stable_id("br", session_id, "main")
    retry_branch_id = stable_id("br", session_id, "retry")
    header = store.create_session(scope=scope, session_id=session_id, branch_id=main_branch_id)
    user = store.append(
        scope=scope,
        session_id=session_id,
        branch_id=main_branch_id,
        parent_event_id=header.event_id,
        expected_parent_event_id=header.event_id,
        event_type="user/message",
        payload={"text": "原始问题"},
        idempotency_scope="inbound",
        idempotency_key_object={
            "transport": "feishu",
            "tenant_or_app_id": "tenant-a",
            "external_message_id": "message-activation",
        },
    ).event
    store.create_branch(
        scope=scope,
        session_id=session_id,
        source_branch_id=main_branch_id,
        fork_parent_event_id=user.event_id,
        new_branch_id=retry_branch_id,
    )
    request_body = canonical_json({"model": "test", "messages": []})
    request = store.append(
        scope=scope,
        session_id=session_id,
        branch_id=retry_branch_id,
        parent_event_id=user.event_id,
        expected_parent_event_id=user.event_id,
        event_type="model/request",
        payload={
            "request_content_hash": hashlib.sha256(request_body).hexdigest(),
            "model": "test",
            "parameters": {},
            "prompt_version": "test",
            "checkpoint_event_id": None,
            "request": {"model": "test", "messages": []},
        },
        idempotency_scope="model_request",
        idempotency_key_object={
            "session_id": session_id,
            "branch_id": retry_branch_id,
            "parent_event_id": user.event_id,
            "request_content_hash": hashlib.sha256(request_body).hexdigest(),
        },
    ).event
    retry = store.append(
        scope=scope,
        session_id=session_id,
        branch_id=retry_branch_id,
        parent_event_id=request.event_id,
        expected_parent_event_id=request.event_id,
        event_type="assistant/message",
        payload={
            "model_request_event_id": request.event_id,
            "content": "重试答案",
            "tool_calls": [],
        },
        idempotency_scope="assistant_message",
        idempotency_key_object={
            "session_id": session_id,
            "model_request_event_id": request.event_id,
            "response_identity": "retry-response",
        },
    ).event
    activated = store.activate_branch(
        scope=scope,
        session_id=session_id,
        branch_id=retry_branch_id,
        activation_nonce="activate-retry",
    )

    assert [event.event_id for event in store.read_branch(scope=scope, session_id=session_id)] == [
        header.event_id,
        user.event_id,
        request.event_id,
        retry.event_id,
        activated.event_id,
    ]


def test_projection_files_can_be_deleted_and_rebuilt(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    session_id = stable_id("sess", "projection")
    store.create_session(scope=scope, session_id=session_id)
    before = store.projection(scope=scope, session_id=session_id)

    for path in tmp_path.rglob(f"{session_id}.*.json"):
        path.unlink()
    rebuilt = store.rebuild_projections(scope=scope, session_id=session_id)

    assert rebuilt == before
    assert len(list(tmp_path.rglob(f"{session_id}.head.json"))) == 1
    assert len(list(tmp_path.rglob(f"{session_id}.idx.json"))) == 1


def test_blob_is_committed_and_verified_with_referencing_event(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    session_id = stable_id("sess", "blob")
    branch_id = stable_id("br", session_id, "main")
    header = store.create_session(scope=scope, session_id=session_id, branch_id=branch_id)
    body = b'{"messages":[{"role":"user","content":"large request"}]}'
    content_hash = hashlib.sha256(body).hexdigest()
    request = store.append(
        scope=scope,
        session_id=session_id,
        branch_id=branch_id,
        parent_event_id=header.event_id,
        expected_parent_event_id=header.event_id,
        event_type="model/request",
        payload={
            "request_content_hash": content_hash,
            "model": "test-model",
            "parameters": {},
            "prompt_version": "test",
            "checkpoint_event_id": None,
        },
        blob_content=body,
        blob_media_type="application/json",
        idempotency_scope="model_request",
        idempotency_key_object={
            "session_id": session_id,
            "branch_id": branch_id,
            "parent_event_id": header.event_id,
            "request_content_hash": content_hash,
        },
    ).event

    assert store.read_blob(scope=scope, blob_ref=request.payload_blob_ref or {}) == body


def test_unknown_core_schema_is_not_truncated_as_bad_tail(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    session_id = stable_id("sess", "future-schema")
    store.create_session(scope=scope, session_id=session_id)
    path = store.event_path(scope=scope, session_id=session_id)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["schema_version"] = 2
    unsigned = {key: item for key, item in value.items() if key != "record_checksum"}
    value["record_checksum"] = "sha256:" + hashlib.sha256(canonical_json(unsigned)).hexdigest()
    path.write_bytes(canonical_json(value) + b"\n")
    size_before = path.stat().st_size

    with pytest.raises(CorruptSessionLog, match="unsupported core event"):
        store.create_session(scope=scope, session_id=session_id)

    assert path.stat().st_size == size_before


def test_fsync_failure_marks_store_unhealthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    session_id = stable_id("sess", "fsync")

    def fail_fsync(_fd: int) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr("infrastructure.memory.event_store.os.fsync", fail_fsync)
    with pytest.raises(StoreUnhealthy):
        store.create_session(scope=scope, session_id=session_id)
    assert store.healthy is False
    with pytest.raises(StoreUnhealthy):
        store.create_session(scope=scope, session_id=session_id)


def test_turn_guard_rejects_concurrent_turn_without_blocking(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    session_id = stable_id("sess", "turn-lock")

    with store.turn_guard(scope=scope, session_id=session_id):
        with pytest.raises(StoreBusy):
            with store.turn_guard(scope=scope, session_id=session_id):
                pass


def test_partition_rotation_preserves_contiguous_sequence_and_rebuilds_manifest(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path, partition_max_events=2)
    scope = _scope()
    session_id = stable_id("sess", "partitioned")
    header = store.create_session(scope=scope, session_id=session_id)
    branch_id = stable_id("br", session_id, "main")
    parent = header.event_id
    for ordinal in range(3):
        event = store.append(
            scope=scope,
            session_id=session_id,
            branch_id=branch_id,
            parent_event_id=parent,
            expected_parent_event_id=parent,
            event_type="user/message",
            payload={"text": f"message {ordinal}"},
            idempotency_scope="inbound",
            idempotency_key_object={
                "transport": "feishu",
                "tenant_or_app_id": "tenant-a",
                "external_message_id": f"partition-{ordinal}",
            },
        ).event
        parent = event.event_id

    assert [event.seq for event in store.read_events(scope=scope, session_id=session_id)] == [1, 2, 3, 4]
    assert len(store.partition_paths(scope=scope, session_id=session_id)) == 2
    manifest = store.event_path(scope=scope, session_id=session_id).with_suffix(".parts.json")
    manifest.unlink()
    store.rebuild_projections(scope=scope, session_id=session_id)
    assert manifest.exists()
