from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from core.tool_protocol import ToolContext


def search_session_history(
    keyword: str,
    days: int = 7,
    limit: int = 20,
    *,
    context: ToolContext,
) -> dict[str, Any]:
    """Search original turns inside the caller's server-authenticated visibility scope."""
    if context.event_store is None or context.conversation_scope is None:
        return {"status": "error", "error": "event context is not configured", "items": []}
    clean_keyword = str(keyword or "").strip()
    if not clean_keyword:
        return {"status": "error", "error": "keyword is required", "items": []}
    return query_session_history(
        keyword=clean_keyword,
        days=days,
        limit=limit,
        event_store=context.event_store,
        scope=context.conversation_scope,
        exclude_session_id=context.session_id,
        exclude_user_seq=context.turn_user_seq,
    )


def query_session_history(
    *,
    keyword: str,
    days: int,
    limit: int,
    event_store: Any,
    scope: Any,
    exclude_session_id: str = "",
    exclude_user_seq: int = 0,
) -> dict[str, Any]:
    """Search and return timestamped user/assistant messages in the caller's scope."""
    clean_keyword = str(keyword or "").strip()
    if not clean_keyword:
        return {"status": "error", "error": "keyword is required", "items": []}
    safe_days = min(max(int(days or 7), 1), 90)
    safe_limit = min(max(int(limit or 20), 1), 100)
    occurred_after = (
        datetime.now(timezone.utc) - timedelta(days=safe_days, seconds=2)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    events = event_store.scan_scope(
        scope=scope,
        occurred_after=occurred_after,
        event_types={
            "user/message",
            "assistant/message",
            "assistant/local_message",
        },
    )
    terms = _search_terms(clean_keyword)
    turns = _group_turns(
        events,
        payload_loader=lambda event: event_store.resolve_event_payload(
            scope=scope,
            event=event,
        ),
    )
    if exclude_session_id and exclude_user_seq:
        turns = [
            turn for turn in turns
            if (turn["session_id"], turn["user_seq"]) != (exclude_session_id, exclude_user_seq)
        ]
    matched = [turn for turn in turns if _turn_matches(turn, terms)]
    truncated = len(matched) > safe_limit
    selected = matched[-safe_limit:]
    items = [message for turn in selected for message in turn["messages"]]
    return {
        "status": "success",
        "keyword": clean_keyword,
        "time_range": {"days": safe_days, "occurred_after": occurred_after},
        "truncated": truncated,
        "items": items,
    }


def _search_terms(keyword: str) -> tuple[str, ...]:
    normalized = keyword.casefold()
    parts = [part for part in re.split(r"[\s,，。;；:/]+", normalized) if part]
    ascii_terms = re.findall(r"[a-z0-9_]{2,}", normalized)
    domain_terms = [
        token
        for token in (
            "多头", "做多", "空头", "做空", "入场", "机会", "止损", "止盈",
            "黄金", "白银", "半导体", "芯片", "持仓", "挂单", "趋势", "震荡",
        )
        if token in normalized
    ]
    return tuple(dict.fromkeys([normalized, *parts, *ascii_terms, *domain_terms]))


def _group_turns(events: list[Any], *, payload_loader: Any | None = None) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    by_session: dict[str, list[Any]] = {}
    for event in events:
        by_session.setdefault(event.session_id, []).append(event)
    for session_events in by_session.values():
        current: dict[str, Any] | None = None
        for event in sorted(session_events, key=lambda item: item.seq):
            payload = payload_loader(event) if payload_loader else (event.payload or {})
            if event.event_type == "user/message":
                if current is not None:
                    turns.append(current)
                current = {
                    "session_id": event.session_id,
                    "occurred_at": event.occurred_at,
                    "user_seq": event.seq,
                    "messages": [{
                        "occurred_at": event.occurred_at,
                        "role": "user",
                        "content": str(payload.get("text") or ""),
                    }],
                }
            elif event.event_type in {"assistant/message", "assistant/local_message"} and current is not None:
                current["messages"].append({
                    "occurred_at": event.occurred_at,
                    "role": "assistant",
                    "content": str(payload.get("content") or ""),
                })
        if current is not None:
            turns.append(current)
    return sorted(turns, key=lambda turn: (turn["occurred_at"], turn["session_id"]))


def _turn_matches(turn: dict[str, Any], terms: tuple[str, ...]) -> bool:
    haystack = "\n".join(
        str(item.get("content") or "") for item in turn.get("messages") or []
    ).casefold()
    return any(term in haystack for term in terms)
