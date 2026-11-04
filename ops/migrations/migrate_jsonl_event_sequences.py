from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from core.conversation_scope import ConversationScope
from infrastructure.memory.event_schema import (
    FORMAT_VERSION,
    MAX_INLINE_PAYLOAD_BYTES,
    canonical_json,
    validate_event_envelope,
)
from infrastructure.memory.event_store import LocalSessionEventStore, SessionEvent
from infrastructure.memory.session_journal import SessionEventJournal


_EVENT_ID_RE = re.compile(r"ev_[0-9a-f]{40}\Z")
_LEGACY_CITATION_RE = re.compile(
    r"\s*[\[〔]?\s*mem:(?:ev_[0-9a-f]{40}|evt_[0-9a-f]{24})\s*[\]〕]?"
)
_SESSION_FILE_RE = re.compile(r"(sess_[0-9A-HJKMNP-TV-Z]{26})(?:\.p(\d+))?\.jsonl\Z")
_PAYLOAD_REFERENCE_FIELDS = {
    "model_request_event_id": "model_request_seq",
    "turn_user_event_id": "turn_user_seq",
    "assistant_event_id": "assistant_seq",
    "tool_result_event_id": "tool_result_seq",
    "delivery_planned_event_id": "delivery_planned_seq",
    "checkpoint_event_id": "checkpoint_seq",
    "covered_from_event_id": "covered_from_seq",
    "covered_to_event_id": "covered_to_seq",
    "first_kept_event_id": "first_kept_seq",
    "last_attempt_event_id": "last_attempt_seq",
}


class MigrationError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean_assistant_text(value: Any) -> str:
    return _LEGACY_CITATION_RE.sub("", str(value or "")).strip()


def _normalize_history_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"status": "error", "error": "legacy history result unavailable", "items": []}

    time_range = value.get("time_range")
    if not isinstance(time_range, dict):
        receipt = value.get("retrieval_receipt")
        time_range = receipt.get("time_range") if isinstance(receipt, dict) else None
    if not isinstance(time_range, dict) and "days" in value:
        time_range = {"days": value["days"]}

    result = {
        key: value[key]
        for key in ("status", "keyword", "truncated", "error")
        if key in value
    }
    if "keyword" not in result and "query" in value:
        result["keyword"] = value["query"]
    if isinstance(time_range, dict):
        result["time_range"] = time_range

    items: list[dict[str, Any]] = []
    for raw in value.get("items") or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("role") in {"user", "assistant"}:
            messages = [raw]
        else:
            messages = []
            if "user" in raw:
                user = {"role": "user", "content": str(raw.get("user") or "")}
                if raw.get("occurred_at") is not None:
                    user["occurred_at"] = raw["occurred_at"]
                messages.append(user)
            assistant = raw.get("assistant") or []
            if isinstance(assistant, str):
                assistant = [assistant]
            messages.extend({"role": "assistant", "content": content} for content in assistant)

        for message in messages:
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            item = {"role": role, "content": str(message.get("content") or "")}
            if role == "assistant":
                item["content"] = _clean_assistant_text(item["content"])
            if message.get("occurred_at") is not None:
                item["occurred_at"] = message["occurred_at"]
            items.append(item)
    result["items"] = items
    return result


def _normalize_journal_status(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    result = dict(value)
    result.pop("session_id", None)
    for key in ("pending_orders", "open_positions", "recent_closed", "recent_events"):
        entries = result.get(key)
        if not isinstance(entries, list):
            continue
        result[key] = [
            {field: item for field, item in entry.items() if field not in {"event_id", "idea_id"}}
            if isinstance(entry, dict)
            else entry
            for entry in entries
        ]
    return result


def _legacy_prompt_line(line: str) -> bool:
    return "必须在对应句末附工具返回的内部引用" in line and "retrieval_receipt" in line


def _clean_message_content(message: dict[str, Any], tool_name: str = "") -> None:
    role = str(message.get("role") or "")
    content = message.get("content")
    if isinstance(content, str):
        if role == "assistant":
            message["content"] = _clean_assistant_text(content)
        elif role == "system":
            message["content"] = "\n".join(
                line for line in content.splitlines() if not _legacy_prompt_line(line)
            ).strip()
        elif role == "tool" and tool_name in {"search_session_history", "get_journal_status"}:
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise MigrationError(f"invalid legacy {tool_name} tool message JSON") from exc
            if tool_name == "search_session_history":
                parsed = _normalize_history_result(parsed)
            else:
                parsed = _normalize_journal_status(parsed)
            message["content"] = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"), default=str)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                if role == "assistant":
                    part["text"] = _clean_assistant_text(part["text"])
                elif role == "system":
                    part["text"] = "\n".join(
                        line for line in part["text"].splitlines() if not _legacy_prompt_line(line)
                    ).strip()


def _normalize_request(request: Any) -> Any:
    if not isinstance(request, dict):
        return request

    messages = request.get("messages")
    if isinstance(messages, list):
        call_names: dict[str, str] = {}
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            for call in message.get("tool_calls") or []:
                if isinstance(call, dict):
                    call_names[str(call.get("id") or "")] = str(
                        (call.get("function") or {}).get("name") or ""
                    )
        for message in messages:
            if isinstance(message, dict):
                name = str(message.get("name") or call_names.get(str(message.get("tool_call_id") or "")) or "")
                _clean_message_content(message, name)
        return request

    inputs = request.get("input")
    if isinstance(inputs, list):
        call_names = {
            str(item.get("call_id") or ""): str(item.get("name") or "")
            for item in inputs
            if isinstance(item, dict) and item.get("type") == "function_call"
        }
        for item in inputs:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                message = {"role": item.get("role"), "content": item.get("content")}
                _clean_message_content(message)
                item["content"] = message.get("content")
            elif item.get("type") == "function_call_output":
                name = call_names.get(str(item.get("call_id") or ""), "")
                message = {"role": "tool", "content": item.get("output")}
                _clean_message_content(message, name)
                item["output"] = message.get("content")
    return request


def _remap_payload_references(payload: dict[str, Any], seq_by_id: dict[str, int]) -> None:
    for old_name, new_name in _PAYLOAD_REFERENCE_FIELDS.items():
        if old_name not in payload:
            continue
        old_value = payload.pop(old_name)
        if old_value in (None, ""):
            payload[new_name] = None
        else:
            try:
                payload[new_name] = seq_by_id[old_value]
            except KeyError as exc:
                raise MigrationError(f"unresolved stored reference field: {old_name}") from exc

    old_values = payload.pop("lower_checkpoint_event_ids", None)
    if old_values is not None:
        try:
            payload["lower_checkpoint_seqs"] = [seq_by_id[item] for item in old_values]
        except KeyError as exc:
            raise MigrationError("unresolved lower checkpoint reference") from exc
    payload.pop("source_event_ids", None)


def _normalize_tool_result(full_payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(full_payload.get("tool_name") or "")
    if tool_name == "search_session_history":
        full_payload["result"] = _normalize_history_result(full_payload.get("result"))
    elif tool_name == "get_journal_status":
        full_payload["result"] = _normalize_journal_status(full_payload.get("result"))
    return full_payload


def _tool_projection(full_payload: dict[str, Any]) -> dict[str, Any]:
    rendered = json.dumps(full_payload.get("result"), ensure_ascii=False, separators=(",", ":"), default=str)
    return {
        "operation_id": full_payload.get("operation_id"),
        "provider_tool_call_id": full_payload.get("provider_tool_call_id"),
        "tool_name": full_payload.get("tool_name"),
        "status": full_payload.get("status"),
        "result": {"truncated": True, "preview": rendered[:12000]},
        "result_truncated": True,
        "result_content_hash": _sha256(canonical_json(full_payload)),
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _put_blob(
    store: LocalSessionEventStore,
    scope: ConversationScope,
    content: bytes,
    projection: dict[str, Any],
) -> dict[str, Any]:
    with store._file_lock(store._gc_lock_path(), shared=True):
        return store._put_blob_locked(
            scope=scope,
            content=content,
            media_type="application/json",
            projection=projection,
        )


def _transform_record(
    record: dict[str, Any],
    seq_by_id: dict[str, int],
    scope: ConversationScope,
    store: LocalSessionEventStore,
) -> dict[str, Any]:
    record = dict(record)
    parent_id = record.pop("parent_event_id", None)
    if parent_id is None:
        record["parent_seq"] = None
    else:
        try:
            record["parent_seq"] = seq_by_id[parent_id]
        except KeyError as exc:
            raise MigrationError("unresolved parent event reference") from exc
    record.pop("event_id")
    record["format_version"] = FORMAT_VERSION

    payload = dict(record.get("payload") or {})
    _remap_payload_references(payload, seq_by_id)
    event_type = str(record.get("event_type") or "")
    if event_type in {"assistant/message", "assistant/local_message", "assistant/attempt"}:
        if isinstance(payload.get("content"), str):
            payload["content"] = _clean_assistant_text(payload["content"])
        if isinstance(payload.get("provider_content"), str):
            payload["provider_content"] = _clean_assistant_text(payload["provider_content"])
    elif event_type == "context/compaction" and isinstance(payload.get("summary"), str):
        payload["summary"] = _clean_assistant_text(payload["summary"])
    record["payload"] = payload or None

    blob_ref = record.get("payload_blob_ref")
    if blob_ref:
        original = store.read_blob(scope=scope, blob_ref=blob_ref)
        event_type = str(record.get("event_type") or "")
        if event_type == "model/request":
            request = _normalize_request(json.loads(original))
            request_bytes = canonical_json(request)
            payload["request_content_hash"] = _sha256(request_bytes)
            inline_payload = {**payload, "request": request}
            if len(canonical_json(inline_payload)) <= MAX_INLINE_PAYLOAD_BYTES:
                record["payload"] = inline_payload
                record["payload_blob_ref"] = None
            else:
                record["payload"] = payload
                record["payload_blob_ref"] = _put_blob(store, scope, request_bytes, payload)
        elif event_type == "tool/result":
            full_payload = _normalize_tool_result(json.loads(original))
            full_bytes = canonical_json(full_payload)
            if len(full_bytes) <= MAX_INLINE_PAYLOAD_BYTES:
                record["payload"] = full_payload
                record["payload_blob_ref"] = None
            else:
                projection = _tool_projection(full_payload)
                record["payload"] = projection
                record["payload_blob_ref"] = _put_blob(store, scope, full_bytes, projection)
        else:
            raise MigrationError(f"unexpected blob event type: {event_type}")
    else:
        if event_type == "model/request" and isinstance(payload.get("request"), dict):
            request = _normalize_request(payload["request"])
            payload["request"] = request
            payload["request_content_hash"] = _sha256(canonical_json(request))
            record["payload"] = payload
        elif event_type == "tool/result":
            full_payload = _normalize_tool_result(payload)
            full_bytes = canonical_json(full_payload)
            if len(full_bytes) <= MAX_INLINE_PAYLOAD_BYTES:
                record["payload"] = full_payload
            else:
                projection = _tool_projection(full_payload)
                record["payload"] = projection
                record["payload_blob_ref"] = _put_blob(store, scope, full_bytes, projection)

    if record.get("payload_blob_ref"):
        record["payload_blob_ref"]["projection"] = dict(record.get("payload") or {})
    record.pop("record_checksum", None)
    record["record_checksum"] = "sha256:" + _sha256(canonical_json(record))
    validate_event_envelope(record, writing=True)
    return record


def _session_groups(root: Path) -> list[tuple[str, list[Path]]]:
    groups: dict[str, list[tuple[int, Path]]] = defaultdict(list)
    for path in (root / "sessions").rglob("sess_*.jsonl"):
        match = _SESSION_FILE_RE.fullmatch(path.name)
        if match:
            session_id = match.group(1)
            part = int(match.group(2) or 0)
            groups[session_id].append((part, path))
    return [
        (session_id, [path for _, path in sorted(parts)])
        for session_id, parts in sorted(groups.items())
    ]


def _scope_from_header(header: dict[str, Any]) -> ConversationScope:
    payload = header.get("payload") or {}
    return ConversationScope(
        transport=str(payload.get("transport") or "feishu"),
        tenant_id=str(header["tenant_id"]),
        visibility_scope=str(header["visibility_scope"]),  # type: ignore[arg-type]
        visibility_scope_id=str(header["visibility_scope_id"]),
        actor_id=str(header.get("actor_id") or header["visibility_scope_id"]),
    )


def _read_v1_session(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        if not path.read_bytes().endswith(b"\n"):
            raise MigrationError(f"session log has an incomplete tail: {path.name}")
        for line in path.read_bytes().splitlines():
            record = json.loads(line)
            checksum = record.get("record_checksum")
            unsigned = {key: value for key, value in record.items() if key != "record_checksum"}
            if checksum != "sha256:" + _sha256(canonical_json(unsigned)):
                raise MigrationError(f"source checksum mismatch: {path.name}")
            if record.get("format_version") != 1 or not _EVENT_ID_RE.fullmatch(str(record.get("event_id") or "")):
                raise MigrationError(f"unexpected source schema: {path.name}")
            records.append(record)
    if any(record.get("seq") != index for index, record in enumerate(records, start=1)):
        raise MigrationError("source sequence is not contiguous")
    return records


def _write_temp(path: Path, records: list[dict[str, Any]]) -> Path:
    temp = path.with_name(path.name + ".seq-migrate")
    if temp.exists():
        raise MigrationError(f"migration temp file already exists: {temp.name}")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        for record in records:
            data = canonical_json(record) + b"\n"
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short write while staging migrated session")
                view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return temp


def _referenced_blobs(root: Path) -> set[str]:
    refs: set[str] = set()
    for path in root.rglob("*.jsonl"):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    value = json.loads(line)
                    ref = value.get("payload_blob_ref")
                    if isinstance(ref, dict) and ref.get("hash"):
                        refs.add(str(ref["hash"]))
        except (json.JSONDecodeError, OSError):
            continue
    return refs


def migrate(root: Path, backup_dir: Path) -> dict[str, int]:
    root = root.expanduser().resolve()
    backup_dir = backup_dir.expanduser().resolve()
    if not root.is_dir():
        raise MigrationError(f"context root does not exist: {root}")
    if backup_dir == root or root in backup_dir.parents:
        raise MigrationError("backup directory must be outside the context root")
    if backup_dir.exists():
        raise MigrationError(f"backup directory already exists: {backup_dir}")

    store = LocalSessionEventStore(root)
    groups = _session_groups(root)
    if not groups:
        raise MigrationError("no session JSONL files found")

    shutil.copytree(root, backup_dir)
    staged: list[tuple[Path, Path]] = []
    session_scopes: list[tuple[str, ConversationScope]] = []
    original_blob_hashes: set[str] = set()
    before_events = 0
    history_results = 0

    try:
        for session_id, paths in groups:
            records = _read_v1_session(paths)
            seq_by_id = {record["event_id"]: record["seq"] for record in records}
            if len(seq_by_id) != len(records):
                raise MigrationError(f"duplicate event identity in session {session_id}")
            scope = _scope_from_header(records[0])
            session_scopes.append((session_id, scope))
            original_blob_hashes.update(
                str((record.get("payload_blob_ref") or {}).get("hash"))
                for record in records
                if (record.get("payload_blob_ref") or {}).get("hash")
            )

            migrated = []
            for record in records:
                if record.get("event_type") == "tool/result" and (record.get("payload") or {}).get("tool_name") == "search_session_history":
                    history_results += 1
                migrated.append(_transform_record(record, seq_by_id, scope, store))

            events = [SessionEvent.from_dict(record) for record in migrated]
            store._rebuild_projection(events, sum(path.stat().st_size for path in paths))
            before_events += len(records)
            staged.extend((path, _write_temp(path, migrated)) for path in paths)

        replaced: list[Path] = []
        try:
            for path, temp in staged:
                os.replace(temp, path)
                store._fsync_directory(path.parent)
                replaced.append(path)
            for session_id, scope in session_scopes:
                store.rebuild_projections(scope=scope, session_id=session_id)
                events = store.read_events(scope=scope, session_id=session_id)
                journal = SessionEventJournal(store=store, scope=scope, session_id=session_id)
                for event in events:
                    if event.event_type == "model/request":
                        journal.load_model_request_bytes(event)
        except Exception:
            for path in replaced:
                shutil.copy2(backup_dir / path.relative_to(root), path)
            for session_id, scope in session_scopes:
                session_path = store.event_path(scope=scope, session_id=session_id)
                for suffix in (".head.json", ".idx.json", ".parts.json"):
                    sidecar = session_path.with_suffix(suffix)
                    backed_up = backup_dir / sidecar.relative_to(root)
                    if backed_up.exists():
                        shutil.copy2(backed_up, sidecar)
                    else:
                        sidecar.unlink(missing_ok=True)
            backup_indexes = backup_dir / "indexes"
            current_indexes = root / "indexes"
            if current_indexes.exists():
                shutil.rmtree(current_indexes)
            if backup_indexes.exists():
                shutil.copytree(backup_indexes, current_indexes)
            raise

        live_hashes = _referenced_blobs(root)
        removed_bytes = 0
        removed_blobs = 0
        for path in (root / "blobs").rglob("*"):
            if path.is_file() and re.fullmatch(r"[0-9a-f]{64}", path.name) and path.name not in live_hashes:
                removed_bytes += path.stat().st_size
                path.unlink()
                removed_blobs += 1
        return {
            "sessions": len(groups),
            "events": before_events,
            "history_results": history_results,
            "live_blobs": len(live_hashes),
            "removed_blobs": removed_blobs,
            "removed_blob_bytes": removed_bytes,
        }
    finally:
        for _, temp in staged:
            temp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="One-time v1 ev-hash to session-sequence migration")
    parser.add_argument("--root", type=Path, required=True, help="runtime context directory")
    parser.add_argument("--backup-dir", type=Path, required=True, help="new full backup directory outside root")
    parser.add_argument("--apply", action="store_true", help="perform the offline migration")
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required; stop the bot before running this migration")
    result = migrate(args.root, args.backup_dir)
    for key, value in result.items():
        print(f"{key}={value}")
    print(f"backup={args.backup_dir.expanduser().resolve()}")


if __name__ == "__main__":
    main()
