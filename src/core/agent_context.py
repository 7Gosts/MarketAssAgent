from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


_LIGHT_SUMMARY_FIELD_LIMITS = {
    "recent_dialogue_summary": 500,
    "current_carryover_hint": 180,
    "snapshot_hint": 120,
}


def build_light_agent_input(
    *,
    user_text: str,
    session_id: str,
    storage_key: str,
    conversation_summary: dict[str, str] | None = None,
    max_chars: int | None = None,
    max_summary_chars: int = 1000,
) -> str:
    compact_summary = _compact_conversation_summary(
        conversation_summary,
        max_chars=max_summary_chars,
    )
    user_text_clean = str(user_text or "").strip() or "无"

    text = _render_light_input(
        session_id=session_id,
        storage_key=storage_key,
        user_text=user_text_clean,
        compact_summary=compact_summary,
    )
    if max_chars is None or len(text) <= max_chars:
        return text

    compact_summary = _compact_conversation_summary(
        compact_summary,
        max_chars=max(240, max_summary_chars // 2),
    )
    text = _render_light_input(
        session_id=session_id,
        storage_key=storage_key,
        user_text=user_text_clean,
        compact_summary=compact_summary,
    )
    if len(text) <= max_chars:
        return text

    return _render_light_input_minimal(
        session_id=session_id,
        storage_key=storage_key,
        user_text=user_text_clean,
    )


def _compact_conversation_summary(
    payload: dict[str, Any] | None,
    *,
    max_chars: int,
) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}

    out: dict[str, str] = {}
    for key, limit in _LIGHT_SUMMARY_FIELD_LIMITS.items():
        val = str(payload.get(key) or "").strip()
        if val:
            out[key] = _truncate_text(val, max_len=min(limit, max_chars))

    if not out:
        return {}

    rendered = _render_conversation_summary_or_none(out)
    if len(rendered) <= max_chars:
        return out

    overflow = len(rendered) - max_chars
    recent = out.get("recent_dialogue_summary", "")
    if recent:
        out["recent_dialogue_summary"] = _truncate_text(
            recent,
            max_len=max(120, len(recent) - overflow),
        )
    return out


def _truncate_text(text: str, *, max_len: int) -> str:
    raw = str(text or "")
    if len(raw) <= max_len:
        return raw
    if max_len <= 3:
        return raw[:max_len]
    return raw[: max_len - 3] + "..."


def _render_light_input(
    *,
    session_id: str,
    storage_key: str,
    user_text: str,
    compact_summary: dict[str, str],
) -> str:
    sections = [
        "【运行上下文】",
        f"session_id: {session_id or 'unknown'}",
        f"storage_key: {storage_key or 'unknown'}",
        f"beijing_time: {_current_beijing_time()}",
        "",
        "【历史对话摘要】",
        _render_conversation_summary_or_none(compact_summary),
        "",
        "【任务目标】",
        "\n".join(
            [
                "你的目标是充分回答用户当前问题，不是复述模板。",
                "追问、持仓延续、风险确认、来源追问时，优先按需查询上下文工具。",
                "上下文工具优先顺序：get_last_snapshot -> search_conversation_summaries -> get_recent_tool_observations。",
                "需要实时行情、关键位、趋势或交易动作确认时，再调用行情工具。",
                "资料不足时继续调用工具；资料充分时停止调用工具并直接回答。",
            ]
        ),
        "",
        "【用户当前消息】",
        user_text,
    ]
    return "\n".join(sections).strip()


def _render_light_input_minimal(
    *,
    session_id: str,
    storage_key: str,
    user_text: str,
) -> str:
    return "\n".join(
        [
            "【运行上下文】",
            f"session_id: {session_id or 'unknown'}",
            f"storage_key: {storage_key or 'unknown'}",
            f"beijing_time: {_current_beijing_time()}",
            "",
            "【任务目标】",
            "你的目标是充分回答用户当前问题；资料不足时继续调用工具，资料充分时直接回答。",
            "",
            "【用户当前消息】",
            user_text,
        ]
    ).strip()


def _render_conversation_summary_or_none(payload: dict[str, str]) -> str:
    if not payload:
        return "无"
    lines: list[str] = []
    if payload.get("recent_dialogue_summary"):
        lines.append(f"- 最近对话摘要: {payload['recent_dialogue_summary']}")
    if payload.get("current_carryover_hint"):
        lines.append(f"- 当前承接线索: {payload['current_carryover_hint']}")
    if payload.get("snapshot_hint"):
        lines.append(f"- 快照提示: {payload['snapshot_hint']}")
    return "\n".join(lines) if lines else "无"


def _current_beijing_time() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S CST")
