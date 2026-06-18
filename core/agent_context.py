from __future__ import annotations

import json
from typing import Any


_PROFILE_FIELDS = (
    "preferred_style",
    "risk_profile",
    "market_bias",
    "favorite_symbols",
    "preferred_timeframes",
    "max_position_ratio",
    "notes",
    "observations",
    "style_history",
)


def build_direct_agent_input(
    *,
    user_text: str,
    session_id: str,
    storage_key: str,
    user_profile: dict[str, Any] | None,
    last_snapshot: dict[str, Any] | None,
    recent_sources: list[dict[str, Any]] | None,
    recent_conclusion: dict[str, Any] | None = None,
    max_chars: int | None = None,
    max_recent_sources: int = 3,
    max_conclusion_chars: int = 240,
) -> str:
    compact_profile = _compact_user_profile(user_profile)
    compact_snapshot = _compact_snapshot(last_snapshot)
    compact_sources = _compact_recent_sources(recent_sources, max_count=max_recent_sources)
    compact_recent_conclusion = _compact_recent_conclusion(
        recent_conclusion,
        max_len=max_conclusion_chars,
    )
    user_text_clean = str(user_text or "").strip() or "无"

    text = _render_direct_input(
        session_id=session_id,
        storage_key=storage_key,
        user_text=user_text_clean,
        compact_profile=compact_profile,
        compact_snapshot=compact_snapshot,
        compact_recent_conclusion=compact_recent_conclusion,
        compact_sources=compact_sources,
    )
    if max_chars is None or len(text) <= max_chars:
        return text

    # 预算超限时按“低价值优先裁剪”策略收缩，保留用户消息与关键快照事实。
    compact_sources = []
    text = _render_direct_input(
        session_id=session_id,
        storage_key=storage_key,
        user_text=user_text_clean,
        compact_profile=compact_profile,
        compact_snapshot=compact_snapshot,
        compact_recent_conclusion=compact_recent_conclusion,
        compact_sources=compact_sources,
    )
    if len(text) <= max_chars:
        return text

    compact_profile = _compact_user_profile_min(compact_profile)
    text = _render_direct_input(
        session_id=session_id,
        storage_key=storage_key,
        user_text=user_text_clean,
        compact_profile=compact_profile,
        compact_snapshot=compact_snapshot,
        compact_recent_conclusion=compact_recent_conclusion,
        compact_sources=compact_sources,
    )
    if len(text) <= max_chars:
        return text

    compact_recent_conclusion = _compact_recent_conclusion(
        compact_recent_conclusion,
        max_len=max(80, max_conclusion_chars // 2),
    )
    text = _render_direct_input(
        session_id=session_id,
        storage_key=storage_key,
        user_text=user_text_clean,
        compact_profile=compact_profile,
        compact_snapshot=compact_snapshot,
        compact_recent_conclusion=compact_recent_conclusion,
        compact_sources=compact_sources,
    )
    if len(text) <= max_chars:
        return text

    compact_snapshot = _compact_snapshot_min(compact_snapshot)
    text = _render_direct_input(
        session_id=session_id,
        storage_key=storage_key,
        user_text=user_text_clean,
        compact_profile=compact_profile,
        compact_snapshot=compact_snapshot,
        compact_recent_conclusion=compact_recent_conclusion,
        compact_sources=compact_sources,
    )
    if len(text) <= max_chars:
        return text

    return _render_minimal_direct_input(
        session_id=session_id,
        storage_key=storage_key,
        user_text=user_text_clean,
        compact_snapshot=compact_snapshot,
        max_chars=max_chars,
    )


def _compact_user_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}

    out: dict[str, Any] = {}
    for key in _PROFILE_FIELDS:
        value = profile.get(key)
        if value in (None, "", [], {}):
            continue
        if key in {"observations", "style_history"} and isinstance(value, list):
            out[key] = value[-5:]
            continue
        out[key] = value
    return out


def _compact_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    return snapshot


def _compact_recent_sources(
    sources: list[dict[str, Any]] | None,
    *,
    max_count: int,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in sources or []:
        if not isinstance(item, dict):
            continue
        ts = str(item.get("timestamp") or item.get("ts") or "").strip()
        tool = str(item.get("tool") or item.get("source") or "").strip()
        summary = str(item.get("summary") or "").strip()
        tool_call_id = str(item.get("tool_call_id") or "").strip()
        row = {
            "timestamp": ts,
            "tool": tool,
            "summary": summary,
            "tool_call_id": tool_call_id,
        }
        if any(row.values()):
            out.append(row)
    return out[: max(max_count, 1)]


def _compact_recent_conclusion(
    payload: dict[str, Any] | None,
    *,
    max_len: int,
) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("last_user_question", "last_assistant_conclusion", "snapshot_hint"):
        val = str(payload.get(key) or "").strip()
        if val:
            out[key] = _truncate_text(val, max_len=max_len)
    return out


def _compact_user_profile_min(profile: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("preferred_style", "risk_profile", "market_bias", "favorite_symbols", "preferred_timeframes"):
        value = profile.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            out[key] = value[:3]
            continue
        out[key] = value
    return out


def _compact_snapshot_min(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    keep_keys = (
        "symbol",
        "interval",
        "timestamp",
        "current_price",
        "trend",
        "structure_signals",
        "levels_v2",
        "actionability",
        "key_levels",
        "raw_insights",
    )
    out: dict[str, Any] = {}
    for key in keep_keys:
        if key not in snapshot:
            continue
        value = snapshot.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "raw_insights":
            out[key] = _truncate_text(str(value), max_len=160)
            continue
        if key == "key_levels" and isinstance(value, dict):
            out[key] = {
                "support": (value.get("support") or [])[:2],
                "resistance": (value.get("resistance") or [])[:2],
            }
            continue
        out[key] = value
    return out


def _truncate_text(text: str, *, max_len: int) -> str:
    raw = str(text or "")
    if len(raw) <= max_len:
        return raw
    if max_len <= 3:
        return raw[:max_len]
    return raw[: max_len - 3] + "..."


def _render_direct_input(
    *,
    session_id: str,
    storage_key: str,
    user_text: str,
    compact_profile: dict[str, Any],
    compact_snapshot: dict[str, Any],
    compact_recent_conclusion: dict[str, str],
    compact_sources: list[dict[str, str]],
) -> str:
    sections = [
        "【运行上下文】",
        f"session_id: {session_id or 'unknown'}",
        f"storage_key: {storage_key or 'unknown'}",
        "",
        "【用户画像】",
        _dump_json_or_none(compact_profile),
        "",
        "【上一轮市场快照】",
        _dump_json_or_none(compact_snapshot),
        "",
        "【最近对话结论】",
        _render_recent_conclusion_or_none(compact_recent_conclusion),
        "",
        "【最近工具来源】",
        _render_sources_or_none(compact_sources),
        "",
        "【用户当前消息】",
        user_text,
    ]
    return "\n".join(sections).strip()


def _render_minimal_direct_input(
    *,
    session_id: str,
    storage_key: str,
    user_text: str,
    compact_snapshot: dict[str, Any],
    max_chars: int,
) -> str:
    sections = [
        "【运行上下文】",
        f"session_id: {session_id or 'unknown'}",
        f"storage_key: {storage_key or 'unknown'}",
        "",
        "【上一轮市场快照】",
        _dump_json_or_none(compact_snapshot),
        "",
        "【用户当前消息】",
        user_text,
    ]
    text = "\n".join(sections).strip()
    if len(text) <= max_chars:
        return text
    snapshot_line = _dump_json_or_none(compact_snapshot)
    keep_budget = max(120, max_chars - 220)
    snapshot_line = _truncate_text(snapshot_line, max_len=keep_budget)
    return "\n".join(
        [
            "【运行上下文】",
            f"session_id: {session_id or 'unknown'}",
            f"storage_key: {storage_key or 'unknown'}",
            "",
            "【上一轮市场快照】",
            snapshot_line,
            "",
            "【用户当前消息】",
            user_text,
        ]
    ).strip()


def _dump_json_or_none(payload: dict[str, Any]) -> str:
    if not payload:
        return "无"
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        return "无"


def _render_sources_or_none(sources: list[dict[str, str]]) -> str:
    if not sources:
        return "无"
    lines: list[str] = []
    for row in sources:
        ts = row.get("timestamp", "").strip()
        tool = row.get("tool", "").strip() or "unknown_tool"
        summary = row.get("summary", "").strip() or "无摘要"
        tool_call_id = row.get("tool_call_id", "").strip()
        line = f"- {ts} {tool}: {summary}".strip()
        if tool_call_id:
            line = f"{line} (tool_call_id={tool_call_id})"
        lines.append(line)
    return "\n".join(lines)


def _render_recent_conclusion_or_none(payload: dict[str, str]) -> str:
    if not payload:
        return "无"
    lines: list[str] = []
    if payload.get("last_user_question"):
        lines.append(f"- 上一轮用户问题: {payload['last_user_question']}")
    if payload.get("last_assistant_conclusion"):
        lines.append(f"- 上一轮助手结论: {payload['last_assistant_conclusion']}")
    if payload.get("snapshot_hint"):
        lines.append(f"- 快照提示: {payload['snapshot_hint']}")
    return "\n".join(lines) if lines else "无"
