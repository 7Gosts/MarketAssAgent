from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from core.conversation_scope import ConversationScope, stable_id
from infrastructure.memory.event_schema import (
    EFFECT_CLASSES,
    EVENT_CLASSES,
    FORMAT_VERSION,
    MAX_INLINE_PAYLOAD_BYTES,
    EventSchemaError,
    UnsupportedCoreEvent,
    canonical_json,
    derive_event_id,
    derive_idempotency_key,
    validate_event_envelope,
)
from utils.logging_utils import get_logger
from utils.crash_injection import crash_if_requested


logger = get_logger(__name__)
_DEFAULT_ACTOR = object()


class EventStoreError(RuntimeError):
    pass


class CorruptSessionLog(EventStoreError):
    pass


class ParentConflict(EventStoreError):
    pass


class InvariantViolation(EventStoreError):
    pass


class StoreUnhealthy(EventStoreError):
    pass


class StoreBusy(EventStoreError):
    pass


class SessionErased(EventStoreError):
    pass


class StorageCapacityError(EventStoreError):
    pass


@dataclass(frozen=True)
class SessionEvent:
    format_version: int
    event_id: str
    session_id: str
    seq: int
    tenant_id: str
    visibility_scope: str
    visibility_scope_id: str
    actor_id: str | None
    branch_id: str
    parent_event_id: str | None
    event_type: str
    event_class: str
    occurred_at: str
    recorded_at: str
    idempotency_scope: str
    idempotency_key: str
    schema_version: int
    payload: dict[str, Any] | None
    payload_blob_ref: dict[str, Any] | None
    producer: dict[str, Any]
    record_checksum: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SessionEvent":
        validate_event_envelope(value, writing=False)
        return cls(**{name: value[name] for name in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class AppendResult:
    event: SessionEvent
    deduplicated: bool = False


@dataclass(frozen=True)
class SessionProjection:
    session_id: str
    active_branch_id: str
    branch_heads: dict[str, str]
    last_seq: int
    last_valid_offset: int
    source_fingerprint: str
    idempotency_index: dict[str, str]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_load_no_duplicates(line: bytes) -> dict[str, Any]:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate key: {key}")
            value[key] = item
        return value

    loaded = json.loads(line, object_pairs_hook=pairs_hook)
    if not isinstance(loaded, dict):
        raise ValueError("event line must be an object")
    return loaded


class LocalSessionEventStore:
    """Single-host append-only JSONL store. Session events are the only authority."""

    FORMAT_VERSION = FORMAT_VERSION
    _SALT_BYTES = 32

    def __init__(
        self,
        root: Path,
        *,
        lock_timeout_seconds: float = 5.0,
        min_free_bytes: int = 128 * 1024 * 1024,
        partition_max_bytes: int = 256 * 1024 * 1024,
        partition_max_events: int = 200_000,
        erase_intent_timeout_seconds: int = 10 * 60,
    ) -> None:
        self.root = Path(root)
        self.sessions_dir = self.root / "sessions"
        self.indexes_dir = self.root / "indexes"
        self.session_locks_dir = self.root / "locks" / "sessions"
        self.turn_locks_dir = self.root / "locks" / "turns"
        self.scope_locks_dir = self.root / "locks" / "scopes"
        self.blobs_dir = self.root / "blobs"
        self.audit_dir = self.root / "audit"
        self.quarantine_dir = self.root / "quarantine"
        self.trash_dir = self.root / "_trash"
        self.lock_timeout_seconds = max(0.05, float(lock_timeout_seconds))
        self.min_free_bytes = max(0, int(min_free_bytes))
        self.partition_max_bytes = max(1024, int(partition_max_bytes))
        self.partition_max_events = max(2, int(partition_max_events))
        self.erase_intent_timeout_seconds = max(1, int(erase_intent_timeout_seconds))
        self._unhealthy_reason = ""
        for path in (
            self.root,
            self.sessions_dir,
            self.indexes_dir,
            self.session_locks_dir,
            self.turn_locks_dir,
            self.scope_locks_dir,
            self.blobs_dir,
            self.audit_dir,
            self.quarantine_dir,
            self.trash_dir,
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)
        self._salt = self._load_or_create_salt()

    @property
    def healthy(self) -> bool:
        return not self._unhealthy_reason

    @property
    def unhealthy_reason(self) -> str:
        return self._unhealthy_reason

    def event_path(self, *, scope: ConversationScope, session_id: str) -> Path:
        clean_session = self._validate_session_id(session_id)
        tenant_hash, scope_hash = self._scope_hashes(scope)
        return self.sessions_dir / tenant_hash / scope_hash / f"{clean_session}.jsonl"

    def partition_paths(self, *, scope: ConversationScope, session_id: str) -> list[Path]:
        base = self.event_path(scope=scope, session_id=session_id)
        paths = [base] if base.exists() else []
        numbered: list[tuple[int, Path]] = []
        for path in base.parent.glob(f"{session_id}.p*.jsonl") if base.parent.exists() else []:
            match = re.fullmatch(re.escape(session_id) + r"\.p(\d+)\.jsonl", path.name)
            if match:
                numbered.append((int(match.group(1)), path))
        paths.extend(path for _, path in sorted(numbered))
        return paths

    def scope_hashes(self, scope: ConversationScope) -> tuple[str, str]:
        return self._scope_hashes(scope)

    def resolve_session_id(self, *, scope: ConversationScope, preferred_session_id: str) -> str:
        preferred = self._validate_session_id(preferred_session_id)
        self._clear_stale_erase_intent(scope, self._gate_intent_path(scope))
        erased = self._erased_session_hashes(scope)
        if _sha256_text(preferred) not in erased:
            return preferred
        generation = 1
        while True:
            candidate = stable_id("sess", preferred, "generation", str(generation))
            if _sha256_text(candidate) not in erased:
                return candidate
            generation += 1

    @contextmanager
    def turn_guard(self, *, scope: ConversationScope, session_id: str) -> Iterator[None]:
        lock_path = self._turn_lock_path(scope, self._validate_session_id(session_id))
        lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StoreBusy("another turn is already running for this session") from exc
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def create_session(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        branch_id: str | None = None,
    ) -> SessionEvent:
        main_branch_id = branch_id or stable_id("br", session_id, "main")
        result = self.append(
            scope=scope,
            session_id=session_id,
            branch_id=main_branch_id,
            parent_event_id=None,
            event_type="session/header",
            payload={
                "main_branch_id": main_branch_id,
                "transport": scope.transport,
                "created_at": _utc_now(),
            },
            idempotency_scope="session_header",
            idempotency_key_object={"session_id": session_id},
            expected_parent_event_id=None,
            actor_id=None,
        )
        return result.event

    def append(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        branch_id: str,
        parent_event_id: str | None,
        event_type: str,
        payload: dict[str, Any] | None,
        idempotency_scope: str,
        idempotency_key_object: Mapping[str, Any],
        expected_parent_event_id: str | None,
        schema_version: int = 1,
        occurred_at: str | None = None,
        event_class: str | None = None,
        actor_id: str | None | object = _DEFAULT_ACTOR,
        blob_content: bytes | None = None,
        blob_media_type: str = "application/octet-stream",
    ) -> AppendResult:
        self._assert_healthy()
        session_id = self._validate_session_id(session_id)
        self._assert_not_quarantined(scope, session_id)
        branch_id = self._validate_branch_id(branch_id)
        resolved_class = EVENT_CLASSES.get(event_type)
        if resolved_class is None:
            if event_class not in {"core", "aux"}:
                raise EventSchemaError("unknown event_type requires an explicit event_class")
            resolved_class = event_class
        elif event_class is not None and event_class != resolved_class:
            raise EventSchemaError(f"event_class mismatch for {event_type}")
        if payload is not None and len(canonical_json(payload)) > MAX_INLINE_PAYLOAD_BYTES:
            raise EventSchemaError("inline payload exceeds 64 KiB")
        if payload is None and blob_content is None:
            raise EventSchemaError("payload or blob_content is required")

        idempotency_key = derive_idempotency_key(idempotency_scope, idempotency_key_object)
        event_id = derive_event_id(
            session_id=session_id,
            idempotency_scope=idempotency_scope,
            idempotency_key=idempotency_key,
        )
        path = self.event_path(scope=scope, session_id=session_id)
        lock_path = self._session_lock_path(scope, session_id)
        gate_path = self._gate_path(scope)
        for directory in (lock_path.parent, gate_path.parent):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)

        self._assert_capacity(len(canonical_json(payload or {})) + len(blob_content or b""))

        gc_guard = self._file_lock(self._gc_lock_path(), shared=True) if blob_content is not None else nullcontext()
        with gc_guard:
            blob_ref = None
            if blob_content is not None:
                blob_ref = self._put_blob_locked(
                    scope=scope,
                    content=blob_content,
                    media_type=blob_media_type,
                    projection=payload,
                )
            with self._scope_guard(scope):
                erased = self._erased_session_hashes(scope)
                if _sha256_text(session_id) in erased:
                    raise SessionErased(f"session was erased: {session_id}")
                if event_type == "session/header":
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                elif not path.parent.exists():
                    raise SessionErased(f"session scope is not available: {session_id}")
                with self._file_lock(lock_path, shared=False):
                    events, total_bytes = self._read_session(
                        scope=scope,
                        session_id=session_id,
                        repair_tail=True,
                    )
                    existing = next((item for item in events if item.event_id == event_id), None)
                    if existing is not None:
                        return AppendResult(event=existing, deduplicated=True)

                    projection = self._rebuild_projection(events, total_bytes)
                    if event_type == "session/header":
                        if events or parent_event_id is not None or expected_parent_event_id is not None:
                            raise InvariantViolation("session/header must be the first event")
                    else:
                        if not events:
                            raise InvariantViolation("session/header is required before other events")
                        actual_parent = projection.branch_heads.get(branch_id)
                        if actual_parent is None:
                            raise InvariantViolation(f"unknown branch: {branch_id}")
                        if parent_event_id != expected_parent_event_id or actual_parent != expected_parent_event_id:
                            raise ParentConflict(
                                f"expected parent {expected_parent_event_id!r}, actual head {actual_parent!r}"
                            )

                    envelope = {
                        "format_version": self.FORMAT_VERSION,
                        "event_id": event_id,
                        "session_id": session_id,
                        "seq": len(events) + 1,
                        "tenant_id": scope.tenant_id,
                        "visibility_scope": scope.visibility_scope,
                        "visibility_scope_id": scope.visibility_scope_id,
                        "actor_id": scope.actor_id if actor_id is _DEFAULT_ACTOR else actor_id,
                        "branch_id": branch_id,
                        "parent_event_id": parent_event_id,
                        "event_type": event_type,
                        "event_class": resolved_class,
                        "occurred_at": occurred_at or _utc_now(),
                        "recorded_at": _utc_now(),
                        "idempotency_scope": idempotency_scope,
                        "idempotency_key": idempotency_key,
                        "schema_version": int(schema_version),
                        "payload": dict(payload) if payload is not None else None,
                        "payload_blob_ref": blob_ref,
                        "producer": {
                            "component": "local_session_event_store",
                            "version": "1",
                            "host_id": _sha256_text(socket.gethostname())[:16],
                            "pid": os.getpid(),
                        },
                    }
                    checksum = "sha256:" + hashlib.sha256(canonical_json(envelope)).hexdigest()
                    complete = {**envelope, "record_checksum": checksum}
                    validate_event_envelope(complete, writing=True)
                    event = SessionEvent.from_dict(complete)
                    self._validate_candidate(events, event)
                    line = canonical_json(complete) + b"\n"
                    active_path = self._active_partition_path(
                        scope=scope,
                        session_id=session_id,
                        events=events,
                        incoming_bytes=len(line),
                    )
                    self._append_line(active_path, line)
                    crash_if_requested("after_fsync_before_index")
                    updated_events = [*events, event]
                    self._refresh_projections(scope=scope, path=path, events=updated_events)
                    self._write_parts_manifest(scope=scope, session_id=session_id, events=updated_events)
                    return AppendResult(event=event)

    def read_events(self, *, scope: ConversationScope, session_id: str) -> list[SessionEvent]:
        self._assert_healthy()
        self._assert_not_quarantined(scope, session_id)
        path = self.event_path(scope=scope, session_id=session_id)
        with self._scope_guard(scope):
            if not path.exists() and _sha256_text(session_id) in self._erased_session_hashes(scope):
                raise SessionErased(f"session was erased: {session_id}")
            events, _ = self._read_session(
                scope=scope,
                session_id=session_id,
                repair_tail=False,
            )
            for event in events:
                if (
                    event.tenant_id != scope.tenant_id
                    or event.visibility_scope != scope.visibility_scope
                    or event.visibility_scope_id != scope.visibility_scope_id
                ):
                    raise CorruptSessionLog(f"scope mismatch in {path}")
        return events

    def read_branch(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        branch_id: str | None = None,
    ) -> list[SessionEvent]:
        events = self.read_events(scope=scope, session_id=session_id)
        if not events:
            return []
        total_bytes = sum(path.stat().st_size for path in self.partition_paths(scope=scope, session_id=session_id))
        projection = self._rebuild_projection(events, total_bytes)
        selected = branch_id or projection.active_branch_id
        head = projection.branch_heads.get(selected)
        if head is None:
            raise InvariantViolation(f"unknown branch: {selected}")
        return self._select_branch_events(events, selected)

    def scan_scope(
        self,
        *,
        scope: ConversationScope,
        occurred_after: str = "",
        event_types: set[str] | None = None,
    ) -> list[SessionEvent]:
        self._assert_healthy()
        tenant_hash, scope_hash = self._scope_hashes(scope)
        directory = self.sessions_dir / tenant_hash / scope_hash
        if not directory.exists():
            return []
        selected: list[SessionEvent] = []
        with self._scope_guard(scope):
            session_ids = {
                match.group(1)
                for path in directory.glob("sess_*.jsonl")
                if (match := re.fullmatch(r"(sess_[0-9A-HJKMNP-TV-Z]{26})(?:\.p\d+)?\.jsonl", path.name))
            }
            for session_id in sorted(session_ids):
                events, _ = self._read_session(
                    repair_tail=False,
                    scope=scope,
                    session_id=session_id,
                )
                if not events:
                    continue
                if any(
                    event.tenant_id != scope.tenant_id
                    or event.visibility_scope != scope.visibility_scope
                    or event.visibility_scope_id != scope.visibility_scope_id
                    for event in events
                ):
                    raise CorruptSessionLog(f"scope mismatch in session {session_id}")
                total_bytes = sum(
                    part.stat().st_size
                    for part in self.partition_paths(scope=scope, session_id=session_id)
                )
                projection = self._rebuild_projection(events, total_bytes)
                for event in self._select_branch_events(events, projection.active_branch_id):
                    if occurred_after and event.occurred_at < occurred_after:
                        continue
                    if event_types is not None and event.event_type not in event_types:
                        continue
                    selected.append(event)
        return sorted(selected, key=lambda event: (event.occurred_at, event.session_id, event.seq))

    def _select_branch_events(
        self,
        events: list[SessionEvent],
        branch_id: str,
    ) -> list[SessionEvent]:
        projection = self._rebuild_projection(events, 0)
        head = projection.branch_heads.get(branch_id)
        if head is None:
            raise InvariantViolation(f"unknown branch: {branch_id}")
        by_id = {event.event_id: event for event in events}
        reachable: list[SessionEvent] = []
        current: str | None = head
        while current is not None:
            event = by_id.get(current)
            if event is None:
                raise CorruptSessionLog(f"missing parent event: {current}")
            reachable.append(event)
            current = event.parent_event_id
        reachable.reverse()
        return reachable

    def projection(self, *, scope: ConversationScope, session_id: str) -> SessionProjection:
        events = self.read_events(scope=scope, session_id=session_id)
        total_bytes = sum(path.stat().st_size for path in self.partition_paths(scope=scope, session_id=session_id))
        return self._rebuild_projection(events, total_bytes)

    def rebuild_projections(self, *, scope: ConversationScope, session_id: str) -> SessionProjection:
        self._assert_healthy()
        path = self.event_path(scope=scope, session_id=session_id)
        lock_path = self._session_lock_path(scope, session_id)
        with self._scope_guard(scope):
            with self._file_lock(lock_path, shared=False):
                events, valid_offset = self._read_session(
                    repair_tail=True,
                    scope=scope,
                    session_id=session_id,
                )
                projection = self._rebuild_projection(events, valid_offset)
                if events:
                    self._refresh_projections(scope=scope, path=path, events=events)
                    self._write_parts_manifest(scope=scope, session_id=session_id, events=events)
                return projection

    def branch_head(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        branch_id: str | None = None,
    ) -> str | None:
        projection = self.projection(scope=scope, session_id=session_id)
        return projection.branch_heads.get(branch_id or projection.active_branch_id)

    def create_branch(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        source_branch_id: str,
        fork_parent_event_id: str,
        new_branch_id: str,
    ) -> SessionEvent:
        source_head = self.branch_head(scope=scope, session_id=session_id, branch_id=source_branch_id)
        return self.append(
            scope=scope,
            session_id=session_id,
            branch_id=source_branch_id,
            parent_event_id=source_head,
            expected_parent_event_id=source_head,
            event_type="branch/created",
            payload={"new_branch_id": new_branch_id, "fork_parent_event_id": fork_parent_event_id},
            idempotency_scope="branch_created",
            idempotency_key_object={
                "session_id": session_id,
                "fork_parent_event_id": fork_parent_event_id,
                "new_branch_id": new_branch_id,
            },
            actor_id=None,
        ).event

    def activate_branch(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        branch_id: str,
        activation_nonce: str,
    ) -> SessionEvent:
        head = self.branch_head(scope=scope, session_id=session_id, branch_id=branch_id)
        return self.append(
            scope=scope,
            session_id=session_id,
            branch_id=branch_id,
            parent_event_id=head,
            expected_parent_event_id=head,
            event_type="branch/activated",
            payload={"branch_id": branch_id, "activation_nonce": activation_nonce},
            idempotency_scope="branch_activated",
            idempotency_key_object={
                "session_id": session_id,
                "branch_id": branch_id,
                "activation_nonce": activation_nonce,
            },
            actor_id=None,
        ).event

    def read_blob(self, *, scope: ConversationScope, blob_ref: Mapping[str, Any]) -> bytes:
        content_hash = str(blob_ref.get("hash") or "")
        if blob_ref.get("algo") != "sha256" or not re.fullmatch(r"[0-9a-f]{64}", content_hash):
            raise EventSchemaError("invalid blob reference")
        tenant_hash, _ = self._scope_hashes(scope)
        path = self.blobs_dir / tenant_hash / content_hash[:2] / content_hash[2:4] / content_hash
        try:
            content = path.read_bytes()
        except FileNotFoundError as exc:
            raise EventStoreError(f"blob not found: {content_hash}") from exc
        if hashlib.sha256(content).hexdigest() != content_hash or len(content) != int(blob_ref.get("bytes") or -1):
            raise EventStoreError(f"blob integrity check failed: {content_hash}")
        return content

    def resolve_event_payload(
        self,
        *,
        scope: ConversationScope,
        event: SessionEvent,
    ) -> dict[str, Any]:
        if event.payload_blob_ref is None:
            return dict(event.payload or {})
        try:
            value = _json_load_no_duplicates(self.read_blob(scope=scope, blob_ref=event.payload_blob_ref))
        except (EventStoreError, ValueError, json.JSONDecodeError):
            return {**(event.payload or {}), "raw_content_unavailable": True}
        return value

    def erase_scope(self, *, scope: ConversationScope, policy_id: str) -> dict[str, Any]:
        """Erase one visibility scope without exposing or deleting another scope."""
        self._assert_healthy()
        tenant_hash, scope_hash = self._scope_hashes(scope)
        intent_path = self._gate_intent_path(scope)
        intent_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._atomic_json_write(intent_path, {
            "requested_at": _utc_now(),
            "policy_id": str(policy_id or "unspecified"),
        })

        erased_session_ids: list[str] = []
        event_count = 0
        byte_count = 0
        isolated_scopes: list[Path] = []
        source = self.sessions_dir / tenant_hash / scope_hash
        release_intent = False
        try:
            with self._file_lock(self._gate_path(scope), shared=False, timeout=self.lock_timeout_seconds * 3):
                if source.exists():
                    trash_scopes = self.trash_dir / "scopes" / tenant_hash
                    trash_scopes.mkdir(parents=True, exist_ok=True, mode=0o700)
                    moved_scope = trash_scopes / f"{scope_hash}.{time.time_ns()}"
                    crash_if_requested("before_erase_rename")
                    os.replace(source, moved_scope)
                    self._fsync_directory(source.parent, mark_unhealthy=False)
                    isolated_scopes.append(moved_scope)
                    crash_if_requested("after_erase_rename_before_cleanup")

                trash_scopes = self.trash_dir / "scopes" / tenant_hash
                if trash_scopes.exists():
                    isolated_scopes.extend(
                        path
                        for path in trash_scopes.glob(f"{scope_hash}.*")
                        if path.is_dir() and path not in isolated_scopes
                    )
                for isolated in isolated_scopes:
                    for path in sorted(isolated.glob("sess_*.jsonl")):
                        if re.fullmatch(r"sess_[0-9A-HJKMNP-TV-Z]{26}\.jsonl", path.name):
                            erased_session_ids.append(path.stem)
                    for path in isolated.rglob("*.jsonl"):
                        byte_count += path.stat().st_size
                        with path.open("rb") as handle:
                            event_count += sum(1 for line in handle if line.endswith(b"\n"))

                index_scope = self.indexes_dir / tenant_hash / scope_hash
                if index_scope.exists():
                    shutil.rmtree(index_scope)
                    self._fsync_directory(index_scope.parent, mark_unhealthy=False)

                quarantine_scope = self.quarantine_dir / tenant_hash / scope_hash
                if quarantine_scope.exists():
                    shutil.rmtree(quarantine_scope)
                    self._fsync_directory(quarantine_scope.parent, mark_unhealthy=False)

                erased = self._erased_session_hashes(scope)
                erased.update(_sha256_text(session_id) for session_id in erased_session_ids)
                self._write_erased_sessions(scope, erased)
                for isolated in isolated_scopes:
                    if isolated.exists():
                        shutil.rmtree(isolated)
                if isolated_scopes and trash_scopes.exists():
                    self._fsync_directory(trash_scopes, mark_unhealthy=False)
                release_intent = True
        finally:
            if release_intent or source.exists():
                try:
                    intent_path.unlink(missing_ok=True)
                    self._fsync_directory(intent_path.parent, mark_unhealthy=False)
                except OSError:
                    logger.exception("failed to remove scope erase intent")

        erased_session_ids = sorted(set(erased_session_ids))
        gc_report = self.collect_blobs(grace_seconds=0, trash_retention_seconds=0)
        report = {
            "action": "scope_erased",
            "tenant_hash": tenant_hash,
            "scope_hash": scope_hash,
            "session_ids_hash": sorted(_sha256_text(value) for value in erased_session_ids),
            "sessions_erased": len(erased_session_ids),
            "event_count": event_count,
            "byte_count": byte_count,
            "requested_at": _utc_now(),
            "completed_at": _utc_now(),
            "policy_id": str(policy_id or "unspecified"),
            "blobs_collected": int(gc_report.get("moved_to_trash") or 0),
        }
        self._append_retention_audit(tenant_hash, report)
        return report

    def collect_blobs(
        self,
        *,
        grace_seconds: int = 24 * 60 * 60,
        trash_retention_seconds: int = 24 * 60 * 60,
    ) -> dict[str, int]:
        """Mark referenced tenant blobs and move old unreferenced blobs to trash."""
        now = time.time()
        moved = 0
        purged = 0
        with self._file_lock(self._gc_lock_path(), shared=False, timeout=self.lock_timeout_seconds * 3):
            tenant_dirs = [path for path in self.blobs_dir.iterdir() if path.is_dir() and not path.name.startswith("_")]
            for tenant_dir in tenant_dirs:
                referenced = self._referenced_blob_hashes(tenant_dir.name)
                trash = tenant_dir / "_trash"
                trash.mkdir(parents=True, exist_ok=True, mode=0o700)
                for path in tenant_dir.glob("[0-9a-f][0-9a-f]/[0-9a-f][0-9a-f]/*"):
                    if not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", path.name):
                        continue
                    if path.name in referenced or now - path.stat().st_mtime < max(0, grace_seconds):
                        continue
                    target = trash / f"{path.name}.{time.time_ns()}"
                    os.replace(path, target)
                    moved += 1
                for path in trash.iterdir():
                    if path.is_file() and now - path.stat().st_mtime >= max(0, trash_retention_seconds):
                        path.unlink(missing_ok=True)
                        purged += 1
        return {"moved_to_trash": moved, "purged": purged}

    def _referenced_blob_hashes(self, tenant_hash: str) -> set[str]:
        referenced: set[str] = set()
        roots = [self.sessions_dir / tenant_hash, self.trash_dir / "scopes" / tenant_hash]
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*.jsonl"):
                try:
                    with path.open("rb") as handle:
                        for raw_line in handle:
                            if not raw_line.endswith(b"\n"):
                                continue
                            value = _json_load_no_duplicates(raw_line[:-1])
                            ref = value.get("payload_blob_ref")
                            if isinstance(ref, dict) and re.fullmatch(r"[0-9a-f]{64}", str(ref.get("hash") or "")):
                                referenced.add(str(ref["hash"]))
                except (OSError, ValueError, json.JSONDecodeError):
                    logger.warning("blob GC skipped unreadable event file path=%s", path)
        return referenced

    def _put_blob_locked(
        self,
        *,
        scope: ConversationScope,
        content: bytes,
        media_type: str,
        projection: dict[str, Any] | None,
    ) -> dict[str, Any]:
        content_hash = hashlib.sha256(content).hexdigest()
        tenant_hash, _ = self._scope_hashes(scope)
        target = self.blobs_dir / tenant_hash / content_hash[:2] / content_hash[2:4] / content_hash
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.exists():
            temp = target.parent / f".{content_hash}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
            try:
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    self._write_all(fd, content)
                    self._fsync_fd(fd, "blob")
                finally:
                    os.close(fd)
                crash_if_requested("before_blob_rename")
                os.replace(temp, target)
                crash_if_requested("after_blob_rename")
                self._fsync_directory(target.parent)
            finally:
                if temp.exists():
                    temp.unlink()
        return {
            "algo": "sha256",
            "hash": content_hash,
            "bytes": len(content),
            "media_type": str(media_type or "application/octet-stream"),
            "projection": dict(projection or {}),
        }

    @contextmanager
    def _scope_guard(self, scope: ConversationScope) -> Iterator[None]:
        intent = self._gate_intent_path(scope)
        self._clear_stale_erase_intent(scope, intent)
        if intent.exists():
            raise StoreBusy("scope deletion is pending")
        gate = self._gate_path(scope)
        gate.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._file_lock(gate, shared=True, timeout=self.lock_timeout_seconds):
            if intent.exists():
                raise StoreBusy("scope deletion is pending")
            yield

    def _clear_stale_erase_intent(self, scope: ConversationScope, intent: Path) -> None:
        try:
            age = time.time() - intent.stat().st_mtime
        except FileNotFoundError:
            return
        if age < self.erase_intent_timeout_seconds:
            return
        try:
            with self._file_lock(self._gate_path(scope), shared=False, timeout=0):
                try:
                    age = time.time() - intent.stat().st_mtime
                except FileNotFoundError:
                    return
                if age >= self.erase_intent_timeout_seconds:
                    tenant_hash, scope_hash = self._scope_hashes(scope)
                    source = self.sessions_dir / tenant_hash / scope_hash
                    trash_scopes = self.trash_dir / "scopes" / tenant_hash
                    isolated_scopes = (
                        [path for path in trash_scopes.glob(f"{scope_hash}.*") if path.is_dir()]
                        if trash_scopes.exists()
                        else []
                    )
                    if not source.exists() and isolated_scopes:
                        erased_session_ids = {
                            path.stem
                            for isolated in isolated_scopes
                            for path in isolated.glob("sess_*.jsonl")
                            if re.fullmatch(r"sess_[0-9A-HJKMNP-TV-Z]{26}\.jsonl", path.name)
                        }
                        erased = self._erased_session_hashes(scope)
                        erased.update(_sha256_text(session_id) for session_id in erased_session_ids)
                        self._write_erased_sessions(scope, erased)
                        for isolated in isolated_scopes:
                            shutil.rmtree(isolated)
                        index_scope = self.indexes_dir / tenant_hash / scope_hash
                        if index_scope.exists():
                            shutil.rmtree(index_scope)
                        quarantine_scope = self.quarantine_dir / tenant_hash / scope_hash
                        if quarantine_scope.exists():
                            shutil.rmtree(quarantine_scope)
                    intent.unlink(missing_ok=True)
                    self._fsync_directory(intent.parent, mark_unhealthy=False)
                    logger.warning("cleared stale scope deletion intent path=%s", intent)
        except StoreBusy:
            return

    @contextmanager
    def _file_lock(
        self,
        lock_path: Path,
        *,
        shared: bool,
        timeout: float | None = None,
    ) -> Iterator[None]:
        lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            operation = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            deadline = time.monotonic() + (self.lock_timeout_seconds if timeout is None else max(0.0, timeout))
            while True:
                try:
                    fcntl.flock(fd, operation | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise StoreBusy(f"lock timeout: {lock_path.name}") from exc
                    time.sleep(0.01)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)

    def _read_path(
        self,
        path: Path,
        *,
        repair_tail: bool,
        scope: ConversationScope,
        session_id: str,
        expected_seq_start: int = 1,
    ) -> tuple[list[SessionEvent], int]:
        if not path.exists():
            return [], 0
        fd = os.open(path, os.O_RDONLY)
        try:
            snapshot_size = os.fstat(fd).st_size
            raw = bytearray()
            while len(raw) < snapshot_size:
                chunk = os.read(fd, min(1024 * 1024, snapshot_size - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
        finally:
            os.close(fd)

        complete_length = raw.rfind(b"\n") + 1
        if complete_length < len(raw):
            if repair_tail:
                self._repair_tail(
                    path=path,
                    valid_length=complete_length,
                    previous_length=len(raw),
                    scope=scope,
                    session_id=session_id,
                    reason="partial_line",
                )
            raw = raw[:complete_length]

        events: list[SessionEvent] = []
        lines = bytes(raw).splitlines(keepends=True)
        valid_offset = 0
        for index, raw_line in enumerate(lines):
            line = raw_line[:-1] if raw_line.endswith(b"\n") else raw_line
            try:
                value = _json_load_no_duplicates(line)
                checksum = str(value.get("record_checksum") or "")
                without_checksum = {key: item for key, item in value.items() if key != "record_checksum"}
                expected = "sha256:" + hashlib.sha256(canonical_json(without_checksum)).hexdigest()
                if checksum != expected:
                    raise ValueError("checksum mismatch")
                event = SessionEvent.from_dict(value)
                if event.seq != expected_seq_start + index:
                    raise ValueError("non-contiguous sequence")
                events.append(event)
                valid_offset += len(raw_line)
            except UnsupportedCoreEvent as exc:
                raise CorruptSessionLog(f"unsupported core event at line {index + 1} in {path}: {exc}") from exc
            except (EventSchemaError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                is_last = index == len(lines) - 1
                if repair_tail and is_last:
                    self._repair_tail(
                        path=path,
                        valid_length=valid_offset,
                        previous_length=len(raw),
                        scope=scope,
                        session_id=session_id,
                        reason="invalid_tail_record",
                    )
                    break
                self._quarantine_session(
                    scope=scope,
                    session_id=session_id,
                    reason="middle_corruption",
                    line=index + 1,
                )
                self._record_recovery_audit(
                    scope=scope,
                    session_id=session_id,
                    action="middle_corruption_detected",
                    details={"line": index + 1},
                )
                raise CorruptSessionLog(f"invalid event at line {index + 1} in {path}: {exc}") from exc
        return events, valid_offset

    def _read_session(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        repair_tail: bool,
    ) -> tuple[list[SessionEvent], int]:
        paths = self.partition_paths(scope=scope, session_id=session_id)
        if not paths:
            return [], 0
        events: list[SessionEvent] = []
        total_bytes = 0
        for index, path in enumerate(paths):
            part_events, valid_bytes = self._read_path(
                path,
                repair_tail=repair_tail and index == len(paths) - 1,
                scope=scope,
                session_id=session_id,
                expected_seq_start=len(events) + 1,
            )
            events.extend(part_events)
            total_bytes += valid_bytes
        self._rebuild_projection(events, total_bytes)
        return events, total_bytes

    def _active_partition_path(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        events: list[SessionEvent],
        incoming_bytes: int,
    ) -> Path:
        paths = self.partition_paths(scope=scope, session_id=session_id)
        if not paths:
            return self.event_path(scope=scope, session_id=session_id)
        active = paths[-1]
        start_seq = 1
        if active != paths[0]:
            match = re.search(r"\.p(\d+)\.jsonl\Z", active.name)
            part_index = int(match.group(1)) if match else 0
            prior_count = 0
            for path in paths[:part_index]:
                with path.open("rb") as handle:
                    prior_count += sum(1 for line in handle if line.endswith(b"\n"))
            start_seq = prior_count + 1
        active_count = max(0, len(events) - start_seq + 1)
        active_size = active.stat().st_size if active.exists() else 0
        should_rotate = (
            active_count >= self.partition_max_events
            or active_size + incoming_bytes > self.partition_max_bytes
        )
        if not should_rotate:
            return active
        next_index = len(paths)
        rotated = active.parent / f"{session_id}.p{next_index}.jsonl"
        fd = os.open(rotated, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        self._fsync_directory(rotated.parent)
        return rotated

    def _write_parts_manifest(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        events: list[SessionEvent],
    ) -> None:
        paths = self.partition_paths(scope=scope, session_id=session_id)
        if len(paths) <= 1:
            return
        cursor = 0
        parts: list[dict[str, Any]] = []
        for index, path in enumerate(paths):
            with path.open("rb") as handle:
                count = sum(1 for line in handle if line.endswith(b"\n"))
            part_events = events[cursor: cursor + count]
            cursor += count
            parts.append({
                "index": index,
                "file": path.name,
                "first_seq": part_events[0].seq if part_events else None,
                "last_seq": part_events[-1].seq if part_events else None,
                "byte_size": path.stat().st_size,
                "last_checksum": part_events[-1].record_checksum if part_events else None,
            })
        self._atomic_json_write(self._parts_path(scope, session_id), {
            "format_version": FORMAT_VERSION,
            "session_id": session_id,
            "parts": parts,
        })

    def _validate_candidate(self, events: list[SessionEvent], candidate: SessionEvent) -> None:
        try:
            self._rebuild_projection([*events, candidate], 0)
        except CorruptSessionLog as exc:
            raise InvariantViolation(str(exc)) from exc

    def _rebuild_projection(self, events: list[SessionEvent], last_valid_offset: int) -> SessionProjection:
        if not events:
            return SessionProjection("", "", {}, 0, last_valid_offset, "", {})
        header = events[0]
        if header.event_type != "session/header" or header.parent_event_id is not None:
            raise CorruptSessionLog("session must start with session/header")
        main_branch = str((header.payload or {}).get("main_branch_id") or "")
        self._validate_branch_id(main_branch)
        if header.branch_id != main_branch:
            raise CorruptSessionLog("invalid main branch declaration")

        by_id: dict[str, SessionEvent] = {header.event_id: header}
        heads = {main_branch: header.event_id}
        active_branch = main_branch
        idempotency_index = {
            f"{header.idempotency_scope}:{header.idempotency_key}": header.event_id,
        }
        for event in events[1:]:
            if event.session_id != header.session_id:
                raise CorruptSessionLog(f"session mismatch at seq {event.seq}")
            if event.parent_event_id not in by_id:
                raise CorruptSessionLog(f"unknown parent at seq {event.seq}")
            current_head = heads.get(event.branch_id)
            if current_head != event.parent_event_id:
                raise CorruptSessionLog(f"branch parent mismatch at seq {event.seq}")

            if event.event_type == "branch/created":
                new_branch = str((event.payload or {}).get("new_branch_id") or "")
                fork_parent = str((event.payload or {}).get("fork_parent_event_id") or "")
                self._validate_branch_id(new_branch)
                if new_branch in heads or fork_parent not in by_id:
                    raise CorruptSessionLog(f"invalid branch creation at seq {event.seq}")
                heads[new_branch] = fork_parent
            elif event.event_type == "branch/activated":
                activated = str((event.payload or {}).get("branch_id") or "")
                if activated not in heads or activated != event.branch_id:
                    raise CorruptSessionLog(f"invalid branch activation at seq {event.seq}")
                active_branch = activated

            self._validate_event_relationship(event, by_id, events[: event.seq - 1])
            heads[event.branch_id] = event.event_id
            by_id[event.event_id] = event
            index_key = f"{event.idempotency_scope}:{event.idempotency_key}"
            previous = idempotency_index.get(index_key)
            if previous is not None and previous != event.event_id:
                raise CorruptSessionLog(f"duplicate idempotency key at seq {event.seq}")
            idempotency_index[index_key] = event.event_id

        fingerprint = "sha256:" + hashlib.sha256(events[-1].record_checksum.encode("ascii")).hexdigest()
        return SessionProjection(
            session_id=header.session_id,
            active_branch_id=active_branch,
            branch_heads=heads,
            last_seq=events[-1].seq,
            last_valid_offset=last_valid_offset,
            source_fingerprint=fingerprint,
            idempotency_index=idempotency_index,
        )

    @staticmethod
    def _validate_event_relationship(
        event: SessionEvent,
        by_id: dict[str, SessionEvent],
        earlier: list[SessionEvent],
    ) -> None:
        payload = event.payload or {}
        if event.event_type in {"assistant/message", "assistant/attempt"}:
            request = by_id.get(str(payload.get("model_request_event_id") or ""))
            if request is None or request.event_type != "model/request":
                raise CorruptSessionLog(f"{event.event_type} references invalid model request")
        elif event.event_type == "assistant/local_message":
            user = by_id.get(str(payload.get("turn_user_event_id") or ""))
            if user is None or user.event_type != "user/message" or not str(payload.get("content") or ""):
                raise CorruptSessionLog("local assistant message references invalid user message")
        elif event.event_type == "tool/call_planned":
            assistant = by_id.get(str(payload.get("assistant_event_id") or ""))
            if assistant is None or assistant.event_type != "assistant/message":
                raise CorruptSessionLog("tool plan references invalid assistant event")
            tool_call_id = str(payload.get("provider_tool_call_id") or "")
            calls = (assistant.payload or {}).get("tool_calls") or []
            if tool_call_id not in {str(item.get("id") or "") for item in calls if isinstance(item, dict)}:
                raise CorruptSessionLog("tool plan references unknown provider tool call")
            for field in ("operation_id", "tool_name", "tool_version", "effect_class", "args_hash"):
                if not payload.get(field):
                    raise CorruptSessionLog(f"tool plan missing {field}")
            if payload.get("effect_class") not in EFFECT_CLASSES:
                raise CorruptSessionLog("tool plan has invalid effect_class")
        elif event.event_type in {"tool/call_started", "tool/result"}:
            operation_id = str(payload.get("operation_id") or "")
            plans = [
                item for item in earlier
                if item.event_type == "tool/call_planned"
                and str((item.payload or {}).get("operation_id") or "") == operation_id
            ]
            if len(plans) != 1:
                raise CorruptSessionLog(f"{event.event_type} references invalid tool plan")
            starts = [
                item for item in earlier
                if item.event_type == "tool/call_started"
                and str((item.payload or {}).get("operation_id") or "") == operation_id
            ]
            results = [
                item for item in earlier
                if item.event_type == "tool/result"
                and str((item.payload or {}).get("operation_id") or "") == operation_id
            ]
            if results:
                raise CorruptSessionLog("tool call already has a terminal result")
            if event.event_type == "tool/call_started":
                if int(payload.get("attempt_no") or 0) != len(starts) + 1:
                    raise CorruptSessionLog("tool attempt number is not contiguous")
            else:
                status = str(payload.get("status") or "")
                if status not in {"succeeded", "failed", "unknown", "interrupted"}:
                    raise CorruptSessionLog("invalid tool result status")
                if status == "interrupted" and starts:
                    raise CorruptSessionLog("interrupted tool result cannot have a started event")
                if status != "interrupted" and not starts:
                    raise CorruptSessionLog("tool result requires a started event")
        elif event.event_type == "tool/reconciliation":
            result_id = str(payload.get("tool_result_event_id") or "")
            result = by_id.get(result_id)
            if result is None or result.event_type != "tool/result" or (result.payload or {}).get("status") != "unknown":
                raise CorruptSessionLog("tool reconciliation requires an unknown result")
        elif event.event_type == "delivery/planned":
            assistant = by_id.get(str(payload.get("assistant_event_id") or ""))
            if assistant is None or assistant.event_type not in {"assistant/message", "assistant/local_message"}:
                raise CorruptSessionLog("delivery plan references invalid assistant event")
        elif event.event_type in {"delivery/started", "delivery/result"}:
            planned_id = str(payload.get("delivery_planned_event_id") or "")
            planned = by_id.get(planned_id)
            if planned is None or planned.event_type != "delivery/planned":
                raise CorruptSessionLog(f"{event.event_type} references invalid delivery plan")
            starts = [
                item for item in earlier
                if item.event_type == "delivery/started"
                and str((item.payload or {}).get("delivery_planned_event_id") or "") == planned_id
            ]
            results = [
                item for item in earlier
                if item.event_type == "delivery/result"
                and str((item.payload or {}).get("delivery_planned_event_id") or "") == planned_id
            ]
            if results:
                raise CorruptSessionLog("delivery already has a terminal result")
            if event.event_type == "delivery/started":
                if int(payload.get("attempt_no") or 0) != len(starts) + 1:
                    raise CorruptSessionLog("delivery attempt number is not contiguous")
            else:
                status = str(payload.get("status") or "")
                if status not in {"delivered", "failed", "unknown", "interrupted"}:
                    raise CorruptSessionLog("invalid delivery result status")
                if status == "interrupted" and starts:
                    raise CorruptSessionLog("interrupted delivery cannot have a started event")
                if status != "interrupted" and not starts:
                    raise CorruptSessionLog("delivery result requires a started event")
        elif event.event_type == "model/request":
            checkpoint_id = payload.get("checkpoint_event_id")
            if checkpoint_id is not None:
                checkpoint = by_id.get(str(checkpoint_id))
                if checkpoint is None or checkpoint.event_type != "context/compaction":
                    raise CorruptSessionLog("model request references invalid checkpoint")
        elif event.event_type == "context/compaction":
            first = by_id.get(str(payload.get("first_kept_event_id") or ""))
            covered_from = by_id.get(str(payload.get("covered_from_event_id") or ""))
            covered_to = by_id.get(str(payload.get("covered_to_event_id") or ""))
            if not first or not covered_from or not covered_to or not (covered_from.seq <= covered_to.seq < first.seq):
                raise CorruptSessionLog("invalid compaction range")
        elif event.event_type == "turn/aborted":
            user = by_id.get(str(payload.get("turn_user_event_id") or ""))
            if user is None or user.event_type != "user/message":
                raise CorruptSessionLog("turn abort references invalid user message")

    def _refresh_projections(
        self,
        *,
        scope: ConversationScope,
        path: Path,
        events: list[SessionEvent],
    ) -> None:
        total_bytes = sum(
            part.stat().st_size
            for part in self.partition_paths(scope=scope, session_id=events[0].session_id)
        )
        projection = self._rebuild_projection(events, total_bytes)
        head_payload = {
            "format_version": FORMAT_VERSION,
            "session_id": projection.session_id,
            "last_seq": projection.last_seq,
            "last_valid_offset": projection.last_valid_offset,
            "active_branch_id": projection.active_branch_id,
            "branch_heads": projection.branch_heads,
            "source_fingerprint": projection.source_fingerprint,
        }
        index_payload = {
            "format_version": FORMAT_VERSION,
            "session_id": projection.session_id,
            "last_seq": projection.last_seq,
            "last_valid_offset": projection.last_valid_offset,
            "source_fingerprint": projection.source_fingerprint,
            "idempotency_index": projection.idempotency_index,
        }
        try:
            self._atomic_json_write(self._head_path(scope, projection.session_id), head_payload)
            self._atomic_json_write(self._index_path(scope, projection.session_id), index_payload)
        except (OSError, StoreUnhealthy) as exc:
            try:
                self._record_recovery_audit(
                    scope=scope,
                    session_id=projection.session_id,
                    action="projection_write_failed",
                    details={"error_type": type(exc).__name__},
                )
            except Exception:
                logger.exception("event-store projection audit failed")

    def _repair_tail(
        self,
        *,
        path: Path,
        valid_length: int,
        previous_length: int,
        scope: ConversationScope,
        session_id: str,
        reason: str,
    ) -> None:
        fd = os.open(path, os.O_WRONLY)
        try:
            os.ftruncate(fd, valid_length)
            self._fsync_fd(fd, "tail repair")
        finally:
            os.close(fd)
        self._fsync_directory(path.parent)
        self._record_recovery_audit(
            scope=scope,
            session_id=session_id,
            action="tail_truncated",
            details={
                "reason": reason,
                "previous_bytes": previous_length,
                "valid_bytes": valid_length,
            },
        )

    def _record_recovery_audit(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        action: str,
        details: dict[str, Any],
    ) -> None:
        tenant_hash, scope_hash = self._scope_hashes(scope)
        path = self.audit_dir / tenant_hash / "recovery.jsonl"
        lock_path = self.root / "locks" / "audit" / f"{tenant_hash}.lock"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        record = {
            "recorded_at": _utc_now(),
            "action": action,
            "scope_hash": scope_hash,
            "session_id_hash": _sha256_text(session_id),
            "details": details,
        }
        with self._file_lock(lock_path, shared=False):
            self._append_line(path, canonical_json(record) + b"\n", mark_unhealthy=False)

    def _append_line(self, path: Path, content: bytes, *, mark_unhealthy: bool = True) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        before = os.lseek(fd, 0, os.SEEK_END)
        try:
            try:
                crash_if_requested("before_append_write")
                self._write_all(fd, content)
                crash_if_requested("after_append_write_before_fsync")
            except OSError:
                os.ftruncate(fd, before)
                self._fsync_fd(fd, "append rollback", mark_unhealthy=mark_unhealthy)
                raise
            self._fsync_fd(fd, "append commit", mark_unhealthy=mark_unhealthy)
        finally:
            os.close(fd)
        self._fsync_directory(path.parent, mark_unhealthy=mark_unhealthy)

    def _atomic_json_write(self, path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = path.parent / f".{path.name}.{os.getpid()}.tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            self._write_all(fd, canonical_json(value) + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()
        self._fsync_directory(path.parent, mark_unhealthy=False)

    def _load_or_create_salt(self) -> bytes:
        path = self.root / "identity.salt"
        lock_path = self.root / "identity.salt.lock"
        with self._file_lock(lock_path, shared=False):
            if path.exists():
                salt = path.read_bytes()
                if len(salt) != self._SALT_BYTES:
                    raise StoreUnhealthy("identity salt is invalid")
                return salt
            salt = os.urandom(self._SALT_BYTES)
            temp = self.root / f".identity.salt.{os.getpid()}.tmp"
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                self._write_all(fd, salt)
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.replace(temp, path)
            finally:
                if temp.exists():
                    temp.unlink()
            self._fsync_directory(self.root, mark_unhealthy=False)
            return salt

    @staticmethod
    def _write_all(fd: int, content: bytes) -> None:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]

    def _fsync_fd(self, fd: int, operation: str, *, mark_unhealthy: bool = True) -> None:
        try:
            os.fsync(fd)
        except OSError as exc:
            if mark_unhealthy:
                self._mark_unhealthy(f"{operation} fsync failed: {type(exc).__name__}")
            raise StoreUnhealthy(self._unhealthy_reason or f"{operation} fsync failed") from exc

    def _fsync_directory(self, path: Path, *, mark_unhealthy: bool = True) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            self._fsync_fd(fd, f"directory {path}", mark_unhealthy=mark_unhealthy)
        finally:
            os.close(fd)

    def _mark_unhealthy(self, reason: str) -> None:
        self._unhealthy_reason = self._unhealthy_reason or reason

    def _assert_healthy(self) -> None:
        if self._unhealthy_reason:
            raise StoreUnhealthy(self._unhealthy_reason)

    def _assert_capacity(self, incoming_bytes: int) -> None:
        free = shutil.disk_usage(self.root).free
        if free - max(0, int(incoming_bytes)) < self.min_free_bytes:
            raise StorageCapacityError("insufficient free space for context event")

    def _quarantine_marker_path(self, scope: ConversationScope, session_id: str) -> Path:
        tenant_hash, scope_hash = self._scope_hashes(scope)
        return self.quarantine_dir / tenant_hash / scope_hash / f"{session_id}.json"

    def _assert_not_quarantined(self, scope: ConversationScope, session_id: str) -> None:
        if self._quarantine_marker_path(scope, session_id).exists():
            raise CorruptSessionLog(f"session is quarantined: {session_id}")

    def _quarantine_session(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        reason: str,
        line: int,
    ) -> None:
        marker = self._quarantine_marker_path(scope, session_id)
        if marker.exists():
            return
        marker.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        copies: list[str] = []
        for source in self.partition_paths(scope=scope, session_id=session_id):
            target = marker.parent / source.name
            shutil.copy2(source, target)
            copies.append(target.name)
        self._atomic_json_write(marker, {
            "quarantined_at": _utc_now(),
            "reason": reason,
            "line": int(line),
            "files": copies,
        })

    def _erased_path(self, scope: ConversationScope) -> Path:
        tenant_hash, scope_hash = self._scope_hashes(scope)
        return self.audit_dir / tenant_hash / "erased" / f"{scope_hash}.json"

    def _erased_session_hashes(self, scope: ConversationScope) -> set[str]:
        path = self._erased_path(scope)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return set()
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreUnhealthy(f"invalid erased-session tombstone: {path}") from exc
        hashes = value.get("session_id_hashes") if isinstance(value, dict) else None
        if not isinstance(hashes, list) or any(not isinstance(item, str) for item in hashes):
            raise StoreUnhealthy(f"invalid erased-session tombstone: {path}")
        return set(hashes)

    def _write_erased_sessions(self, scope: ConversationScope, hashes: set[str]) -> None:
        self._atomic_json_write(self._erased_path(scope), {
            "updated_at": _utc_now(),
            "session_id_hashes": sorted(hashes),
        })

    def _append_retention_audit(self, tenant_hash: str, record: dict[str, Any]) -> None:
        path = self.audit_dir / tenant_hash / "retention.jsonl"
        lock = self.root / "locks" / "audit" / f"{tenant_hash}.lock"
        with self._file_lock(lock, shared=False):
            self._append_line(path, canonical_json(record) + b"\n", mark_unhealthy=False)

    def _scope_hashes(self, scope: ConversationScope) -> tuple[str, str]:
        tenant_hash = _sha256_text(scope.tenant_id)[:32]
        scope_hash = hashlib.sha256(
            self._salt
            + b"\x1f"
            + scope.visibility_scope.encode("utf-8")
            + b"\x1f"
            + scope.visibility_scope_id.encode("utf-8")
        ).hexdigest()[:32]
        return tenant_hash, scope_hash

    def _session_lock_path(self, scope: ConversationScope, session_id: str) -> Path:
        tenant_hash, scope_hash = self._scope_hashes(scope)
        return self.session_locks_dir / tenant_hash / scope_hash / f"{session_id}.lock"

    def _turn_lock_path(self, scope: ConversationScope, session_id: str) -> Path:
        tenant_hash, scope_hash = self._scope_hashes(scope)
        return self.turn_locks_dir / tenant_hash / scope_hash / f"{session_id}.lock"

    def _gate_path(self, scope: ConversationScope) -> Path:
        tenant_hash, scope_hash = self._scope_hashes(scope)
        return self.scope_locks_dir / tenant_hash / f"{scope_hash}.gate"

    def _gate_intent_path(self, scope: ConversationScope) -> Path:
        return self._gate_path(scope).with_suffix(".gate.intent")

    def _gc_lock_path(self) -> Path:
        return self.root / "locks" / "_gc.lock"

    def _head_path(self, scope: ConversationScope, session_id: str) -> Path:
        return self.event_path(scope=scope, session_id=session_id).with_suffix(".head.json")

    def _parts_path(self, scope: ConversationScope, session_id: str) -> Path:
        return self.event_path(scope=scope, session_id=session_id).with_suffix(".parts.json")

    def _index_path(self, scope: ConversationScope, session_id: str) -> Path:
        tenant_hash, scope_hash = self._scope_hashes(scope)
        return self.indexes_dir / tenant_hash / scope_hash / f"{session_id}.idx.json"

    @staticmethod
    def _validate_session_id(value: str) -> str:
        clean = str(value or "").strip()
        if not re.fullmatch(r"sess_[0-9A-HJKMNP-TV-Z]{26}", clean):
            raise ValueError("invalid session_id")
        return clean

    @staticmethod
    def _validate_branch_id(value: str) -> str:
        clean = str(value or "").strip()
        if not re.fullmatch(r"br_[0-9A-HJKMNP-TV-Z]{26}", clean):
            raise ValueError("invalid branch_id")
        return clean
