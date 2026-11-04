from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from core.conversation_scope import ConversationScope, stable_id
from core.message_protocol import Message, ToolCall
from infrastructure.memory.event_schema import EFFECT_CLASSES, MAX_INLINE_PAYLOAD_BYTES, canonical_json
from infrastructure.memory.event_store import AppendResult, LocalSessionEventStore, SessionEvent


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _operation_id(
    *,
    session_id: str,
    turn_user_seq: int,
    assistant_seq: int,
    provider_tool_call_id: str,
    tool_name: str,
    tool_version: str,
) -> str:
    value = canonical_json({
        "session_id": session_id,
        "turn_user_seq": turn_user_seq,
        "assistant_seq": assistant_seq,
        "provider_tool_call_id": provider_tool_call_id,
        "tool_name": tool_name,
        "tool_version": tool_version,
    })
    return "op_" + hashlib.sha256(value).hexdigest()[:40]


@dataclass(frozen=True)
class PlannedToolCall:
    event: SessionEvent
    operation_id: str


class SessionEventJournal:
    """Typed append facade for one session and its active branch."""

    def __init__(
        self,
        *,
        store: LocalSessionEventStore,
        scope: ConversationScope,
        session_id: str,
    ) -> None:
        self.store = store
        self.scope = scope
        self.session_id = session_id

    def ensure_session(self) -> SessionEvent:
        events = self.store.read_events(scope=self.scope, session_id=self.session_id)
        if events:
            return events[0]
        return self.store.create_session(
            scope=self.scope,
            session_id=self.session_id,
            branch_id=stable_id("br", self.session_id, "main"),
        )

    def append_user_message(
        self,
        *,
        text: str,
        transport: str,
        tenant_or_app_id: str,
        external_message_id: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> SessionEvent:
        return self.accept_user_message(
            text=text,
            transport=transport,
            tenant_or_app_id=tenant_or_app_id,
            external_message_id=external_message_id,
            attachments=attachments,
        ).event

    def accept_user_message(
        self,
        *,
        text: str,
        transport: str,
        tenant_or_app_id: str,
        external_message_id: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AppendResult:
        branch_id, parent = self._active_head()
        full_payload = {
            "text": str(text),
            "transport": str(transport),
            "external_message_id": str(external_message_id),
            "attachments": list(attachments or []),
        }
        blob_content = None
        payload = full_payload
        if len(canonical_json(full_payload)) > MAX_INLINE_PAYLOAD_BYTES:
            blob_content = canonical_json(full_payload)
            payload = {
                "text": str(text)[:12000],
                "text_truncated": True,
                "transport": str(transport),
                "external_message_id": str(external_message_id),
                "attachments": [],
            }
        return self.store.append(
            scope=self.scope,
            session_id=self.session_id,
            branch_id=branch_id,
            parent_seq=parent,
            expected_parent_seq=parent,
            event_type="user/message",
            payload=payload,
            blob_content=blob_content,
            blob_media_type="application/json",
            idempotency_scope="inbound",
            idempotency_key_object={
                "transport": str(transport),
                "tenant_or_app_id": str(tenant_or_app_id),
                "external_message_id": str(external_message_id),
            },
        )

    def append_model_request(
        self,
        *,
        request_payload: dict[str, Any],
        prompt_version: str,
        checkpoint_seq: int | None = None,
    ) -> SessionEvent:
        request_bytes = canonical_json(request_payload)
        request_hash = _content_hash(request_bytes)
        projection: dict[str, Any] = {
            "request_content_hash": request_hash,
            "model": str(request_payload.get("model") or ""),
            "parameters": {
                key: value
                for key, value in request_payload.items()
                if key not in {"model", "messages", "input", "tools"}
            },
            "prompt_version": str(prompt_version),
            "checkpoint_seq": checkpoint_seq,
        }
        blob_content: bytes | None = None
        inline = {**projection, "request": request_payload}
        if len(canonical_json(inline)) <= MAX_INLINE_PAYLOAD_BYTES:
            payload = inline
        else:
            payload = projection
            blob_content = request_bytes
        branch_id, parent = self._active_head()
        return self.store.append(
            scope=self.scope,
            session_id=self.session_id,
            branch_id=branch_id,
            parent_seq=parent,
            expected_parent_seq=parent,
            event_type="model/request",
            payload=payload,
            blob_content=blob_content,
            blob_media_type="application/json",
            idempotency_scope="model_request",
            idempotency_key_object={
                "session_id": self.session_id,
                "branch_id": branch_id,
                "parent_seq": parent,
                "request_content_hash": request_hash,
            },
            actor_id=None,
        ).event

    def append_assistant_message(
        self,
        *,
        model_request_seq: int,
        message: Message,
        provider_response_id: str = "",
        provider_content: str | None = None,
    ) -> SessionEvent:
        calls = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ]
        response = canonical_json({"content": message.content, "tool_calls": calls})
        response_identity = str(provider_response_id or _content_hash(response))
        full_payload = {
            "model_request_seq": model_request_seq,
            "provider_response_id": str(provider_response_id),
            "content": message.content,
            "tool_calls": calls,
        }
        if provider_content is not None and provider_content != message.content:
            full_payload["provider_content"] = provider_content
        blob_content = None
        payload = full_payload
        if len(canonical_json(full_payload)) > MAX_INLINE_PAYLOAD_BYTES:
            blob_content = canonical_json(full_payload)
            payload = {
                "model_request_seq": model_request_seq,
                "provider_response_id": str(provider_response_id),
                "content": message.content[:12000],
                "content_truncated": True,
                "tool_calls": calls,
            }
        return self._append(
            event_type="assistant/message",
            payload=payload,
            blob_content=blob_content,
            blob_media_type="application/json",
            idempotency_scope="assistant_message",
            idempotency_key_object={
                "session_id": self.session_id,
                "model_request_seq": model_request_seq,
                "response_identity": response_identity,
            },
        )

    def append_assistant_attempt(
        self,
        *,
        model_request_seq: int,
        attempt_ordinal: int,
        status: str,
        error_type: str,
        provider_response_id: str = "",
        content: str = "",
        details: dict[str, Any] | None = None,
    ) -> SessionEvent:
        full_payload = {
            "model_request_seq": model_request_seq,
            "attempt_ordinal": int(attempt_ordinal),
            "status": status,
            "error_type": error_type,
            "provider_response_id": str(provider_response_id),
            "content": str(content),
            "details": dict(details or {}),
        }
        blob_content = None
        payload = full_payload
        if len(canonical_json(full_payload)) > MAX_INLINE_PAYLOAD_BYTES:
            blob_content = canonical_json(full_payload)
            payload = {
                "model_request_seq": model_request_seq,
                "attempt_ordinal": int(attempt_ordinal),
                "status": status,
                "error_type": error_type,
                "provider_response_id": str(provider_response_id),
                "content": str(content)[:12000],
                "content_truncated": True,
            }
        return self._append(
            event_type="assistant/attempt",
            payload=payload,
            idempotency_scope="assistant_attempt",
            idempotency_key_object={
                "session_id": self.session_id,
                "model_request_seq": model_request_seq,
                "attempt_ordinal": int(attempt_ordinal),
            },
            blob_content=blob_content,
            blob_media_type="application/json",
        )

    def append_local_assistant_message(
        self,
        *,
        turn_user_seq: int,
        reason: str,
        content: str,
    ) -> SessionEvent:
        return self._append(
            event_type="assistant/local_message",
            payload={
                "turn_user_seq": turn_user_seq,
                "reason": str(reason),
                "content": str(content),
            },
            idempotency_scope="assistant_local_message",
            idempotency_key_object={
                "session_id": self.session_id,
                "turn_user_seq": turn_user_seq,
                "reason": str(reason),
            },
        )

    def append_tool_plan(
        self,
        *,
        turn_user_seq: int,
        assistant_seq: int,
        call: ToolCall,
        tool_version: str,
        effect_class: str,
    ) -> PlannedToolCall:
        if effect_class not in EFFECT_CLASSES:
            raise ValueError(f"invalid effect_class: {effect_class}")
        operation_id = _operation_id(
            session_id=self.session_id,
            turn_user_seq=turn_user_seq,
            assistant_seq=assistant_seq,
            provider_tool_call_id=call.id,
            tool_name=call.name,
            tool_version=tool_version,
        )
        args_hash = _content_hash(canonical_json(call.arguments))
        event = self._append(
            event_type="tool/call_planned",
            payload={
                "operation_id": operation_id,
                "turn_user_seq": turn_user_seq,
                "assistant_seq": assistant_seq,
                "provider_tool_call_id": call.id,
                "tool_name": call.name,
                "tool_version": tool_version,
                "effect_class": effect_class,
                "arguments": call.arguments,
                "args_hash": args_hash,
            },
            idempotency_scope="tool_call",
            idempotency_key_object={"operation_id": operation_id},
        )
        return PlannedToolCall(event=event, operation_id=operation_id)

    def append_tool_started(self, *, operation_id: str, attempt_no: int) -> SessionEvent:
        return self._append(
            event_type="tool/call_started",
            payload={"operation_id": operation_id, "attempt_no": int(attempt_no)},
            idempotency_scope="tool_call_started",
            idempotency_key_object={"operation_id": operation_id, "attempt_no": int(attempt_no)},
        )

    def append_tool_result(
        self,
        *,
        operation_id: str,
        provider_tool_call_id: str,
        tool_name: str,
        status: str,
        result: Any,
    ) -> SessionEvent:
        full_payload = {
            "operation_id": operation_id,
            "provider_tool_call_id": provider_tool_call_id,
            "tool_name": tool_name,
            "status": status,
            "result": result,
        }
        blob_content = None
        payload = full_payload
        if len(canonical_json(full_payload)) > MAX_INLINE_PAYLOAD_BYTES:
            blob_content = canonical_json(full_payload)
            payload = {
                "operation_id": operation_id,
                "provider_tool_call_id": provider_tool_call_id,
                "tool_name": tool_name,
                "status": status,
                "result": _bounded_projection(result),
                "result_truncated": True,
                "result_content_hash": _content_hash(blob_content),
            }
        return self._append(
            event_type="tool/result",
            payload=payload,
            blob_content=blob_content,
            blob_media_type="application/json",
            idempotency_scope="tool_result",
            idempotency_key_object={"operation_id": operation_id},
        )

    def append_delivery_planned(
        self,
        *,
        transport: str,
        tenant_or_app_id: str,
        destination: str,
        assistant_seq: int,
    ) -> SessionEvent:
        return self._append(
            event_type="delivery/planned",
            payload={
                "transport": transport,
                "tenant_or_app_id": tenant_or_app_id,
                "destination": destination,
                "assistant_seq": assistant_seq,
            },
            idempotency_scope="delivery_plan",
            idempotency_key_object={
                "transport": transport,
                "tenant_or_app_id": tenant_or_app_id,
                "destination": destination,
                "assistant_seq": assistant_seq,
            },
        )

    def append_delivery_started(self, *, planned_seq: int, attempt_no: int) -> SessionEvent:
        return self._append(
            event_type="delivery/started",
            payload={"delivery_planned_seq": planned_seq, "attempt_no": int(attempt_no)},
            idempotency_scope="delivery_started",
            idempotency_key_object={
                "delivery_planned_seq": planned_seq,
                "attempt_no": int(attempt_no),
            },
        )

    def append_delivery_result(
        self,
        *,
        planned_seq: int,
        status: str,
        provider_message_id: str = "",
        error: str = "",
    ) -> SessionEvent:
        return self._append(
            event_type="delivery/result",
            payload={
                "delivery_planned_seq": planned_seq,
                "status": status,
                "provider_message_id": provider_message_id,
                "error": error,
            },
            idempotency_scope="delivery_result",
            idempotency_key_object={"delivery_planned_seq": planned_seq},
        )

    def append_turn_aborted(
        self,
        *,
        turn_user_seq: int,
        abort_nonce: str,
        reason: str,
        last_attempt_seq: int | None = None,
    ) -> SessionEvent:
        return self._append(
            event_type="turn/aborted",
            payload={
                "turn_user_seq": turn_user_seq,
                "reason": reason,
                "last_attempt_seq": last_attempt_seq,
            },
            idempotency_scope="turn_aborted",
            idempotency_key_object={
                "session_id": self.session_id,
                "turn_user_seq": turn_user_seq,
                "abort_nonce": abort_nonce,
            },
        )

    def append_compaction(
        self,
        *,
        summary: str,
        covered_from_seq: int,
        covered_to_seq: int,
        first_kept_seq: int,
        input_tokens: int,
        lower_checkpoint_seqs: list[int],
    ) -> SessionEvent:
        checkpoint_hash = _content_hash(str(summary).encode("utf-8"))
        projection = {
            "covered_from_seq": covered_from_seq,
            "covered_to_seq": covered_to_seq,
            "first_kept_seq": first_kept_seq,
            "input_tokens": int(input_tokens),
            "generator": "llm-v1",
            "checkpoint_content_hash": checkpoint_hash,
        }
        full_payload = {
            **projection,
            "summary": str(summary),
            "lower_checkpoint_seqs": list(dict.fromkeys(lower_checkpoint_seqs)),
        }
        payload = full_payload
        blob_content = canonical_json(full_payload)
        if len(blob_content) <= MAX_INLINE_PAYLOAD_BYTES:
            blob_content = None
        else:
            payload = projection
        branch_id, parent = self._active_head()
        return self.store.append(
            scope=self.scope,
            session_id=self.session_id,
            branch_id=branch_id,
            parent_seq=parent,
            expected_parent_seq=parent,
            event_type="context/compaction",
            payload=payload,
            blob_content=blob_content,
            blob_media_type="application/json",
            idempotency_scope="compaction",
            idempotency_key_object={
                "session_id": self.session_id,
                "branch_id": branch_id,
                "covered_to_seq": covered_to_seq,
                "checkpoint_content_hash": checkpoint_hash,
            },
            actor_id=None,
        ).event

    def recover_incomplete_tool_calls(self, registry: Any) -> list[SessionEvent]:
        """Close orphaned calls without replaying an uncertain external effect."""
        events = self.store.read_branch(scope=self.scope, session_id=self.session_id)
        planned_by_call_id = {
            (
                (event.payload or {}).get("assistant_seq"),
                str((event.payload or {}).get("provider_tool_call_id") or ""),
            ): event
            for event in events
            if event.event_type == "tool/call_planned"
        }
        terminal_operations = {
            str((event.payload or {}).get("operation_id") or "")
            for event in events
            if event.event_type == "tool/result"
        }
        recovered: list[SessionEvent] = []

        current_user_seq: int | None = None
        for event in events:
            if event.event_type == "user/message":
                current_user_seq = event.seq
            if event.event_type != "assistant/message":
                continue
            for item in (event.payload or {}).get("tool_calls") or []:
                if not isinstance(item, dict):
                    continue
                call_id = str(item.get("id") or "")
                call_key = (event.seq, call_id)
                if call_key in planned_by_call_id:
                    continue
                if current_user_seq is None:
                    raise ValueError("assistant tool call has no preceding user message")
                spec = registry.get(str(item.get("name") or ""))
                planned = self.append_tool_plan(
                    turn_user_seq=current_user_seq,
                    assistant_seq=event.seq,
                    call=ToolCall(
                        id=call_id,
                        name=str(item.get("name") or ""),
                        arguments=dict(item.get("arguments") or {}),
                    ),
                    tool_version=str(getattr(spec, "version", "unregistered")),
                    effect_class=str(getattr(spec, "effect_class", "opaque_effect")),
                )
                planned_by_call_id[call_key] = planned.event
                recovered.append(planned.event)

        refreshed = self.store.read_branch(scope=self.scope, session_id=self.session_id)
        starts = {
            str((event.payload or {}).get("operation_id") or "")
            for event in refreshed
            if event.event_type == "tool/call_started"
        }
        terminal_operations.update(
            str((event.payload or {}).get("operation_id") or "")
            for event in refreshed
            if event.event_type == "tool/result"
        )
        for plan in (event for event in refreshed if event.event_type == "tool/call_planned"):
            payload = plan.payload or {}
            operation_id = str(payload.get("operation_id") or "")
            if operation_id in terminal_operations:
                continue
            status = "unknown" if operation_id in starts else "interrupted"
            result = self.append_tool_result(
                operation_id=operation_id,
                provider_tool_call_id=str(payload.get("provider_tool_call_id") or ""),
                tool_name=str(payload.get("tool_name") or ""),
                status=status,
                result={
                    "status": status,
                    "error": "process_interrupted_before_tool_result",
                },
            )
            recovered.append(result)
        return recovered

    def load_model_request_bytes(self, event: SessionEvent) -> bytes:
        if event.event_type != "model/request":
            raise ValueError("event is not model/request")
        payload = event.payload or {}
        request = payload.get("request")
        if isinstance(request, dict):
            content = canonical_json(request)
        elif event.payload_blob_ref:
            content = self.store.read_blob(scope=self.scope, blob_ref=event.payload_blob_ref)
        else:
            raise ValueError("model/request has no request body")
        expected = str(payload.get("request_content_hash") or "")
        if _content_hash(content) != expected:
            raise ValueError("model/request content hash mismatch")
        return content

    def _append(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        idempotency_scope: str,
        idempotency_key_object: dict[str, Any],
        blob_content: bytes | None = None,
        blob_media_type: str = "application/octet-stream",
    ) -> SessionEvent:
        branch_id, parent = self._active_head()
        return self.store.append(
            scope=self.scope,
            session_id=self.session_id,
            branch_id=branch_id,
            parent_seq=parent,
            expected_parent_seq=parent,
            event_type=event_type,
            payload=payload,
            idempotency_scope=idempotency_scope,
            idempotency_key_object=idempotency_key_object,
            blob_content=blob_content,
            blob_media_type=blob_media_type,
            actor_id=None,
        ).event

    def _active_head(self) -> tuple[str, int]:
        projection = self.store.projection(scope=self.scope, session_id=self.session_id)
        if not projection.active_branch_id:
            raise ValueError("session has not been created")
        return projection.active_branch_id, projection.branch_heads[projection.active_branch_id]


def _bounded_projection(value: Any, max_chars: int = 12000) -> dict[str, Any]:
    rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return {
        "truncated": True,
        "preview": rendered[:max_chars],
        "original_chars": len(rendered),
    }
