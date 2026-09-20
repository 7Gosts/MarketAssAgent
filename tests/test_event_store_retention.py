from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.conversation_scope import ConversationScope
from infrastructure.memory.event_store import LocalSessionEventStore, SessionErased
from infrastructure.memory.session_journal import SessionEventJournal


def _scope() -> ConversationScope:
    return ConversationScope(
        transport="feishu",
        tenant_id="tenant-a",
        visibility_scope="private",
        visibility_scope_id="ou_alice",
        actor_id="ou_alice",
    )


def test_scope_erase_isolates_old_session_and_rotates_identity(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    old_session_id = store.resolve_session_id(scope=scope, preferred_session_id=scope.session_id)
    journal = SessionEventJournal(store=store, scope=scope, session_id=old_session_id)
    journal.ensure_session()
    journal.append_user_message(
        text="private position",
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_1",
    )

    report = store.erase_scope(scope=scope, policy_id="user_request")

    assert report["sessions_erased"] == 1
    with pytest.raises(SessionErased):
        store.read_events(scope=scope, session_id=old_session_id)
    new_session_id = store.resolve_session_id(scope=scope, preferred_session_id=scope.session_id)
    assert new_session_id != old_session_id
    SessionEventJournal(store=store, scope=scope, session_id=new_session_id).ensure_session()


def test_blob_gc_keeps_referenced_blob_and_removes_old_orphan(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    journal = SessionEventJournal(store=store, scope=scope, session_id=scope.session_id)
    journal.ensure_session()
    event = journal.append_user_message(
        text="x" * (70 * 1024),
        transport="feishu",
        tenant_or_app_id="tenant-a",
        external_message_id="om_large",
    )
    referenced_hash = str((event.payload_blob_ref or {})["hash"])
    tenant_hash, _ = store.scope_hashes(scope)
    orphan = store.blobs_dir / tenant_hash / "ff" / "ff" / ("f" * 64)
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"orphan")
    old = time.time() - 7200
    os.utime(orphan, (old, old))

    report = store.collect_blobs(grace_seconds=60, trash_retention_seconds=0)

    assert report["moved_to_trash"] == 1
    assert not orphan.exists()
    referenced = store.blobs_dir / tenant_hash / referenced_hash[:2] / referenced_hash[2:4] / referenced_hash
    assert referenced.exists()


def test_scope_erase_removes_quarantine_copies(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    session_id = scope.session_id
    journal = SessionEventJournal(store=store, scope=scope, session_id=session_id)
    journal.ensure_session()
    quarantine_scope = store.quarantine_dir.joinpath(*store.scope_hashes(scope))
    quarantine_scope.mkdir(parents=True)
    (quarantine_scope / f"{session_id}.jsonl").write_text("private position\n", encoding="utf-8")

    store.erase_scope(scope=scope, policy_id="user_request")

    assert not quarantine_scope.exists()


def test_scope_erase_recovers_isolated_directory_from_prior_crash(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path)
    scope = _scope()
    session_id = scope.session_id
    journal = SessionEventJournal(store=store, scope=scope, session_id=session_id)
    journal.ensure_session()
    tenant_hash, scope_hash = store.scope_hashes(scope)
    source = store.sessions_dir / tenant_hash / scope_hash
    isolated = store.trash_dir / "scopes" / tenant_hash / f"{scope_hash}.crashed"
    isolated.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, isolated)

    report = store.erase_scope(scope=scope, policy_id="retry_after_crash")

    assert report["sessions_erased"] == 1
    assert not isolated.exists()
    assert store.resolve_session_id(scope=scope, preferred_session_id=session_id) != session_id


def test_stale_erase_intent_finishes_post_rename_recovery(tmp_path: Path):
    store = LocalSessionEventStore(tmp_path, erase_intent_timeout_seconds=1)
    scope = _scope()
    session_id = scope.session_id
    SessionEventJournal(store=store, scope=scope, session_id=session_id).ensure_session()
    tenant_hash, scope_hash = store.scope_hashes(scope)
    source = store.sessions_dir / tenant_hash / scope_hash
    isolated = store.trash_dir / "scopes" / tenant_hash / f"{scope_hash}.crashed"
    isolated.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, isolated)
    intent = store._gate_intent_path(scope)
    intent.parent.mkdir(parents=True, exist_ok=True)
    intent.write_text('{"policy_id":"user_request"}\n', encoding="utf-8")
    old = time.time() - 10
    os.utime(intent, (old, old))

    resolved = store.resolve_session_id(scope=scope, preferred_session_id=session_id)

    assert resolved != session_id
    assert not isolated.exists()
    assert not intent.exists()


def test_scope_erase_keeps_intent_when_failure_happens_after_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = LocalSessionEventStore(tmp_path, erase_intent_timeout_seconds=1)
    scope = _scope()
    session_id = scope.session_id
    SessionEventJournal(store=store, scope=scope, session_id=session_id).ensure_session()
    original_write = store._write_erased_sessions

    def fail_tombstone(_scope, _hashes):
        raise OSError("disk error")

    monkeypatch.setattr(store, "_write_erased_sessions", fail_tombstone)
    with pytest.raises(OSError, match="disk error"):
        store.erase_scope(scope=scope, policy_id="user_request")

    intent = store._gate_intent_path(scope)
    assert intent.exists()
    assert not store.event_path(scope=scope, session_id=session_id).parent.exists()

    monkeypatch.setattr(store, "_write_erased_sessions", original_write)
    old = time.time() - 10
    os.utime(intent, (old, old))
    assert store.resolve_session_id(scope=scope, preferred_session_id=session_id) != session_id
    assert not intent.exists()
