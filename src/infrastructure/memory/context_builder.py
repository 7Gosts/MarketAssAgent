from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from core.conversation_scope import ConversationScope
from core.message_protocol import Message, ToolCall, tool_message
from infrastructure.memory.event_store import (
    EventStoreError,
    InvariantViolation,
    LocalSessionEventStore,
    SessionEvent,
)


@dataclass(frozen=True)
class BuiltSessionContext:
    messages: list[Message]
    evidence_index: dict[str, str]
    checkpoint_event_id: str | None
    estimated_tokens: int

    @property
    def evidence_event_ids(self) -> tuple[str, ...]:
        return tuple(self.evidence_index)


class SessionContextBuilder:
    """Build model context from complete events on the explicitly active branch."""

    def __init__(self, store: LocalSessionEventStore) -> None:
        self.store = store

    def build(
        self,
        *,
        scope: ConversationScope,
        session_id: str,
        system_prompt: str,
        recent_turns: int | None = None,
    ) -> BuiltSessionContext:
        events = self.store.read_branch(scope=scope, session_id=session_id)
        checkpoint = self._latest_checkpoint(events)
        visible_events = self._events_after_checkpoint(events, checkpoint)
        visible_events = self._filter_aborted_turns(visible_events)
        if recent_turns is not None:
            visible_events = self._last_turns(visible_events, max(1, int(recent_turns)))
        self._assert_complete_tool_protocol(visible_events)

        summary = str((checkpoint.payload or {}).get("summary") or "").strip() if checkpoint else ""
        messages, visible_evidence_ids = self.render_messages(
            scope=scope,
            system_prompt=system_prompt,
            visible_events=visible_events,
            checkpoint_summary=summary,
        )
        checkpoint_sources = (
            (checkpoint.payload or {}).get("source_event_ids") or []
            if checkpoint is not None
            else []
        )
        evidence_index = {
            str(event_id): "checkpoint_cited"
            for event_id in checkpoint_sources
            if str(event_id)
        }
        for event_id in visible_evidence_ids:
            evidence_index[event_id] = "verbatim"

        return BuiltSessionContext(
            messages=messages,
            evidence_index=evidence_index,
            checkpoint_event_id=checkpoint.event_id if checkpoint else None,
            estimated_tokens=estimate_messages_tokens(messages),
        )

    def render_messages(
        self,
        *,
        scope: ConversationScope,
        system_prompt: str,
        visible_events: list[SessionEvent],
        checkpoint_summary: str = "",
    ) -> tuple[list[Message], list[str]]:
        self._assert_complete_tool_protocol(visible_events)
        messages = [Message(role="system", content=str(system_prompt or "").strip())]
        evidence_ids: list[str] = []
        if checkpoint_summary:
            messages.append(Message(
                role="system",
                content="以下是较早对话的只读抽取式检查点：\n" + checkpoint_summary,
            ))
        plans_by_operation = {
            str((event.payload or {}).get("operation_id") or ""): event
            for event in visible_events
            if event.event_type == "tool/call_planned"
        }
        reconciled_results = self._reconciled_results(visible_events)
        for event in visible_events:
            payload = self._event_payload(scope, event)
            if event.event_type == "user/message":
                messages.append(Message(role="user", content=str(payload.get("text") or "")))
                evidence_ids.append(event.event_id)
            elif event.event_type == "assistant/message":
                calls = tuple(
                    ToolCall(
                        id=str(item.get("id") or ""),
                        name=str(item.get("name") or ""),
                        arguments=dict(item.get("arguments") or {}),
                    )
                    for item in payload.get("tool_calls") or []
                    if isinstance(item, dict)
                )
                messages.append(
                    Message(
                        role="assistant",
                        content=str(payload.get("content") or ""),
                        tool_calls=calls,
                    )
                )
                evidence_ids.append(event.event_id)
            elif event.event_type == "assistant/local_message":
                messages.append(Message(role="assistant", content=str(payload.get("content") or "")))
                evidence_ids.append(event.event_id)
            elif event.event_type == "tool/result":
                operation_id = str(payload.get("operation_id") or "")
                plan = plans_by_operation.get(operation_id)
                if plan is None:
                    raise InvariantViolation("tool result has no reachable plan")
                plan_payload = plan.payload or {}
                result_payload = reconciled_results.get(event.event_id, payload)
                messages.append(
                    tool_message(
                        tool_call_id=str(plan_payload.get("provider_tool_call_id") or ""),
                        name=str(plan_payload.get("tool_name") or ""),
                        result=result_payload.get("result"),
                    )
                )
                evidence_ids.append(event.event_id)

        return messages, evidence_ids

    def _event_payload(self, scope: ConversationScope, event: SessionEvent) -> dict[str, Any]:
        if event.payload_blob_ref and event.event_type in {"user/message", "assistant/message"}:
            return self.store.resolve_event_payload(scope=scope, event=event)
        return event.payload or {}

    @staticmethod
    def _latest_checkpoint(events: list[SessionEvent]) -> SessionEvent | None:
        checkpoints = [event for event in events if event.event_type == "context/compaction"]
        return checkpoints[-1] if checkpoints else None

    @staticmethod
    def _events_after_checkpoint(
        events: list[SessionEvent],
        checkpoint: SessionEvent | None,
    ) -> list[SessionEvent]:
        if checkpoint is None:
            return events
        first_kept = str((checkpoint.payload or {}).get("first_kept_event_id") or "")
        for index, event in enumerate(events):
            if event.event_id == first_kept:
                return events[index:]
        raise InvariantViolation("checkpoint first_kept_event_id is not reachable")

    @staticmethod
    def _last_turns(events: list[SessionEvent], limit: int) -> list[SessionEvent]:
        prefix: list[SessionEvent] = []
        turns: list[list[SessionEvent]] = []
        current: list[SessionEvent] | None = None
        for event in events:
            if event.event_type == "user/message":
                if current is not None:
                    turns.append(current)
                current = [event]
            elif current is None:
                prefix.append(event)
            else:
                current.append(event)
        if current is not None:
            turns.append(current)
        return [*prefix, *(event for turn in turns[-limit:] for event in turn)]

    @staticmethod
    def _filter_aborted_turns(events: list[SessionEvent]) -> list[SessionEvent]:
        aborted_user_ids = {
            str((event.payload or {}).get("turn_user_event_id") or "")
            for event in events
            if event.event_type == "turn/aborted"
        }
        if not aborted_user_ids:
            return events

        prefix: list[SessionEvent] = []
        turns: list[list[SessionEvent]] = []
        current: list[SessionEvent] | None = None
        for event in events:
            if event.event_type == "user/message":
                if current is not None:
                    turns.append(current)
                current = [event]
            elif current is None:
                prefix.append(event)
            else:
                current.append(event)
        if current is not None:
            turns.append(current)

        visible_turns = [
            turn
            for turn in turns
            if (
                turn[0].event_id not in aborted_user_ids
                or any(event.event_type == "assistant/local_message" for event in turn)
            )
        ]
        return [*prefix, *(event for turn in visible_turns for event in turn)]

    @staticmethod
    def _assert_complete_tool_protocol(events: list[SessionEvent]) -> None:
        terminal_operations = {
            str((event.payload or {}).get("operation_id") or "")
            for event in events
            if event.event_type == "tool/result"
        }
        planned_call_ids = {
            (
                str((event.payload or {}).get("assistant_event_id") or ""),
                str((event.payload or {}).get("provider_tool_call_id") or ""),
            ): str((event.payload or {}).get("operation_id") or "")
            for event in events
            if event.event_type == "tool/call_planned"
        }
        for event in events:
            if event.event_type != "assistant/message":
                continue
            for call in (event.payload or {}).get("tool_calls") or []:
                call_id = str(call.get("id") or "") if isinstance(call, dict) else ""
                operation_id = planned_call_ids.get((event.event_id, call_id))
                if not operation_id or operation_id not in terminal_operations:
                    raise InvariantViolation(f"assistant tool call is incomplete: {call_id}")

    @staticmethod
    def _reconciled_results(events: list[SessionEvent]) -> dict[str, dict[str, Any]]:
        reconciled: dict[str, dict[str, Any]] = {}
        for event in events:
            if event.event_type != "tool/reconciliation":
                continue
            payload = event.payload or {}
            result_id = str(payload.get("tool_result_event_id") or "")
            reconciled[result_id] = {
                "status": payload.get("status"),
                "result": payload.get("result"),
            }
        return reconciled


def request_payload_from_bytes(content: bytes) -> dict[str, Any]:
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("model request body must be an object")
    return value


def estimate_text_tokens(text: str, *, margin: float = 0.08) -> int:
    """Conservative local estimate for mixed Chinese and ASCII model input."""
    raw = str(text or "")
    ascii_chars = sum(ord(char) < 128 for char in raw)
    non_ascii_chars = len(raw) - ascii_chars
    base = math.ceil(ascii_chars / 4) + math.ceil(non_ascii_chars * 1.15)
    return max(1, math.ceil(base * (1.0 + max(0.0, margin))))


def estimate_messages_tokens(messages: list[Message], *, margin: float = 0.08) -> int:
    serialized = json.dumps(
        [message.to_openai_dict() for message in messages],
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return estimate_text_tokens(serialized, margin=margin)
