from __future__ import annotations

import hashlib

import pytest

from core.conversation_scope import stable_id
from infrastructure.memory.event_schema import (
    EventSchemaError,
    canonical_json,
    derive_event_id,
    derive_idempotency_key,
    validate_event_envelope,
)


def _envelope() -> dict:
    session_id = stable_id("sess", "schema")
    branch_id = stable_id("br", session_id, "main")
    key = derive_idempotency_key("session_header", {"session_id": session_id})
    value = {
        "format_version": 1,
        "event_id": derive_event_id(
            session_id=session_id,
            idempotency_scope="session_header",
            idempotency_key=key,
        ),
        "session_id": session_id,
        "seq": 1,
        "tenant_id": "tenant-a",
        "visibility_scope": "private",
        "visibility_scope_id": "ou_alice",
        "actor_id": "ou_alice",
        "branch_id": branch_id,
        "parent_event_id": None,
        "event_type": "session/header",
        "event_class": "core",
        "occurred_at": "2026-09-20T00:00:00.000Z",
        "recorded_at": "2026-09-20T00:00:00.000Z",
        "idempotency_scope": "session_header",
        "idempotency_key": key,
        "schema_version": 1,
        "payload": {"main_branch_id": branch_id},
        "payload_blob_ref": None,
        "producer": {"component": "test", "version": "1", "host_id": "host", "pid": 1},
    }
    value["record_checksum"] = "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()
    return value


def test_strict_writer_rejects_unknown_envelope_field():
    value = _envelope()
    value["legacy_scope"] = "private"

    with pytest.raises(EventSchemaError, match="unknown envelope fields"):
        validate_event_envelope(value, writing=True)


def test_idempotency_key_requires_exact_scope_fields():
    with pytest.raises(EventSchemaError, match="idempotency key fields"):
        derive_idempotency_key("inbound", {"external_message_id": "om_1"})


def test_canonical_json_rejects_unsafe_integer_and_expands_float_exponent():
    with pytest.raises(EventSchemaError):
        canonical_json({"unsafe": 2**60})
    assert canonical_json({"number": 1e-10}) == b'{"number":0.0000000001}'
