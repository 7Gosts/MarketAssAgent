from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from core.canonical_json import CanonicalJsonError, canonical_json


FORMAT_VERSION = 1
MAX_INLINE_PAYLOAD_BYTES = 64 * 1024

EVENT_CLASSES: dict[str, str] = {
    "session/header": "core",
    "user/message": "core",
    "model/request": "core",
    "assistant/message": "core",
    "assistant/local_message": "core",
    "assistant/attempt": "aux",
    "tool/call_planned": "core",
    "tool/call_started": "core",
    "tool/result": "core",
    "tool/reconciliation": "core",
    "delivery/planned": "core",
    "delivery/started": "core",
    "delivery/result": "core",
    "delivery/reconciliation": "core",
    "context/compaction": "core",
    "branch/created": "core",
    "branch/activated": "core",
    "turn/aborted": "core",
    "session/label": "aux",
}

EVENT_SCHEMA_VERSIONS = {event_type: frozenset({1}) for event_type in EVENT_CLASSES}

IDEMPOTENCY_KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "session_header": ("session_id",),
    "inbound": ("transport", "tenant_or_app_id", "external_message_id"),
    "model_request": ("session_id", "branch_id", "parent_event_id", "request_content_hash"),
    "assistant_message": ("session_id", "model_request_event_id", "response_identity"),
    "assistant_local_message": ("session_id", "turn_user_event_id", "reason"),
    "assistant_attempt": ("session_id", "model_request_event_id", "attempt_ordinal"),
    "tool_call": ("operation_id",),
    "tool_call_started": ("operation_id", "attempt_no"),
    "tool_result": ("operation_id",),
    "tool_reconciliation": ("operation_id", "reconcile_nonce"),
    "delivery_plan": ("transport", "tenant_or_app_id", "destination", "assistant_event_id"),
    "delivery_started": ("delivery_planned_event_id", "attempt_no"),
    "delivery_result": ("delivery_planned_event_id",),
    "delivery_reconciliation": ("delivery_planned_event_id", "reconcile_nonce"),
    "branch_created": ("session_id", "fork_parent_event_id", "new_branch_id"),
    "branch_activated": ("session_id", "branch_id", "activation_nonce"),
    "compaction": ("session_id", "branch_id", "covered_to_event_id", "checkpoint_content_hash"),
    "turn_aborted": ("session_id", "turn_user_event_id", "abort_nonce"),
    "session_label": ("session_id", "label_nonce"),
}

EFFECT_CLASSES = frozenset({"pure_query", "idempotent_write", "queryable_effect", "opaque_effect"})

ENVELOPE_FIELDS = frozenset({
    "format_version",
    "event_id",
    "session_id",
    "seq",
    "tenant_id",
    "visibility_scope",
    "visibility_scope_id",
    "actor_id",
    "branch_id",
    "parent_event_id",
    "event_type",
    "event_class",
    "occurred_at",
    "recorded_at",
    "idempotency_scope",
    "idempotency_key",
    "schema_version",
    "payload",
    "payload_blob_ref",
    "producer",
    "record_checksum",
})

_EVENT_ID_RE = re.compile(r"ev_[0-9a-f]{40}\Z")
_SESSION_ID_RE = re.compile(r"sess_[0-9A-HJKMNP-TV-Z]{26}\Z")
_BRANCH_ID_RE = re.compile(r"br_[0-9A-HJKMNP-TV-Z]{26}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_CHECKSUM_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


EventSchemaError = CanonicalJsonError


class UnsupportedCoreEvent(EventSchemaError):
    pass


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def derive_idempotency_key(scope: str, key_object: Mapping[str, Any]) -> str:
    expected = IDEMPOTENCY_KEY_FIELDS.get(scope)
    if expected is None:
        raise EventSchemaError(f"unknown idempotency scope: {scope}")
    actual = tuple(key_object.keys())
    if set(actual) != set(expected):
        raise EventSchemaError(
            f"idempotency key fields for {scope} must be {list(expected)}, got {sorted(actual)}"
        )
    return sha256_hex(canonical_json({name: key_object[name] for name in expected}))


def derive_event_id(*, session_id: str, idempotency_scope: str, idempotency_key: str) -> str:
    return "ev_" + sha256_hex(canonical_json({
        "session_id": session_id,
        "scope": idempotency_scope,
        "key": idempotency_key,
    }))[:40]


def validate_event_envelope(value: Mapping[str, Any], *, writing: bool) -> None:
    fields = set(value)
    missing = ENVELOPE_FIELDS - fields
    if missing:
        raise EventSchemaError(f"missing envelope fields: {sorted(missing)}")
    if writing:
        unknown = fields - ENVELOPE_FIELDS
        if unknown:
            raise EventSchemaError(f"unknown envelope fields: {sorted(unknown)}")

    if value["format_version"] != FORMAT_VERSION:
        raise UnsupportedCoreEvent(f"unsupported format_version: {value['format_version']!r}")
    if not _EVENT_ID_RE.fullmatch(str(value["event_id"])):
        raise EventSchemaError("invalid event_id")
    if not _SESSION_ID_RE.fullmatch(str(value["session_id"])):
        raise EventSchemaError("invalid session_id")
    if not _BRANCH_ID_RE.fullmatch(str(value["branch_id"])):
        raise EventSchemaError("invalid branch_id")
    parent = value["parent_event_id"]
    if parent is not None and not _EVENT_ID_RE.fullmatch(str(parent)):
        raise EventSchemaError("invalid parent_event_id")
    if not isinstance(value["seq"], int) or isinstance(value["seq"], bool) or value["seq"] < 1:
        raise EventSchemaError("seq must be a positive integer")
    if value["visibility_scope"] not in {"private", "group", "web"}:
        raise EventSchemaError("invalid visibility_scope")
    actor_id = value["actor_id"]
    if actor_id is not None and not isinstance(actor_id, str):
        raise EventSchemaError("actor_id must be a string or null")

    event_type = str(value["event_type"])
    event_class = value["event_class"]
    if event_class not in {"core", "aux"}:
        raise EventSchemaError("invalid event_class")
    known_class = EVENT_CLASSES.get(event_type)
    if known_class is not None and known_class != event_class:
        raise EventSchemaError(f"event_class mismatch for {event_type}")
    schema_version = value["schema_version"]
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
        raise EventSchemaError("schema_version must be a positive integer")
    if schema_version not in EVENT_SCHEMA_VERSIONS.get(event_type, ()) and event_class == "core":
        raise UnsupportedCoreEvent(f"unsupported core event schema: {event_type}@{schema_version}")

    if value["idempotency_scope"] not in IDEMPOTENCY_KEY_FIELDS:
        raise EventSchemaError("invalid idempotency_scope")
    if not _HASH_RE.fullmatch(str(value["idempotency_key"])):
        raise EventSchemaError("invalid idempotency_key")
    if not _CHECKSUM_RE.fullmatch(str(value["record_checksum"])):
        raise EventSchemaError("invalid record_checksum")
    if value["payload"] is None and value["payload_blob_ref"] is None:
        raise EventSchemaError("payload or payload_blob_ref is required")
    if value["payload"] is not None and not isinstance(value["payload"], dict):
        raise EventSchemaError("payload must be an object or null")
    if value["payload_blob_ref"] is not None:
        ref = value["payload_blob_ref"]
        if not isinstance(ref, dict) or ref.get("algo") != "sha256" or not _HASH_RE.fullmatch(str(ref.get("hash") or "")):
            raise EventSchemaError("invalid payload_blob_ref")

    producer = value["producer"]
    if not isinstance(producer, dict) or set(producer) != {"component", "version", "host_id", "pid"}:
        raise EventSchemaError("invalid producer")
    canonical_json(value)
