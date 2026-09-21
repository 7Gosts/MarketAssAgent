from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal


MessageRole = Literal["system", "user", "assistant", "tool"]


_MA_REGIME_LABELS = {"bullish": "均线偏多", "bearish": "均线偏空", "mixed": "均线方向分化", "unavailable": "数据不足"}
_MA_ALIGNMENT_LABELS = {"bullish": "多头排列", "bearish": "空头排列", "mixed": "均线交错", "unavailable": "数据不足"}
_CANDLE_EVENT_LABELS = {"break_up": "向上突破", "break_down": "向下跌破", "inside": "内包整理"}


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: MessageRole
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    tool_call_id: str = ""
    name: str = ""

    def to_openai_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.role == "assistant" and self.tool_calls:
            result["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(
                            call.arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        ),
                    },
                }
                for call in self.tool_calls
            ]
        if self.role == "tool":
            result["tool_call_id"] = self.tool_call_id
            if self.name:
                result["name"] = self.name
        return result


def user_message(content: str) -> Message:
    return Message(role="user", content=str(content or ""))


def _present_market_analysis(analysis: Any) -> Any:
    if not isinstance(analysis, dict):
        return analysis

    analysis = dict(analysis)
    analysis["ma_regime"] = _MA_REGIME_LABELS.get(analysis.get("ma_regime"), analysis.get("ma_regime"))
    analysis["ma_alignment"] = _MA_ALIGNMENT_LABELS.get(analysis.get("ma_alignment"), analysis.get("ma_alignment"))
    if isinstance(analysis.get("recent_candles"), list):
        analysis["recent_candles"] = [
            {**candle, "event": _CANDLE_EVENT_LABELS.get(candle.get("event"), candle.get("event"))}
            if isinstance(candle, dict) else candle
            for candle in analysis["recent_candles"]
        ]

    return analysis


def _present_analyze_market_result(result: dict[str, Any]) -> dict[str, Any]:
    out = dict(result)
    items = out.get("items")
    if not isinstance(items, list):
        return out

    presented_items: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            presented_items.append(item)
            continue
        presented_item = dict(item)
        presented_item["analysis"] = _present_market_analysis(presented_item.get("analysis"))
        presented_items.append(presented_item)
    out["items"] = presented_items
    return out


def _present_previous_analysis_snapshot_result(result: dict[str, Any]) -> dict[str, Any]:
    out = dict(result)
    snapshot = out.get("snapshot")
    if not isinstance(snapshot, dict):
        return out

    snapshot = dict(snapshot)
    snapshot["ma_regime"] = _MA_REGIME_LABELS.get(snapshot.get("ma_regime"), snapshot.get("ma_regime"))
    out["snapshot"] = snapshot
    return out


def tool_message(*, tool_call_id: str, name: str, result: Any) -> Message:
    if isinstance(result, str):
        content = result
    else:
        tool_name = str(name or "")
        if isinstance(result, dict):
            if tool_name == "analyze_market":
                result = _present_analyze_market_result(result)
            elif tool_name == "get_previous_analysis_snapshot":
                result = _present_previous_analysis_snapshot_result(result)
        content = json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
    return Message(
        role="tool",
        content=content,
        tool_call_id=str(tool_call_id or ""),
        name=str(name or ""),
    )


def build_messages(
    *,
    system_prompt: str,
    history: list[dict[str, Any]],
    user_input: str,
) -> list[Message]:
    messages = [Message(role="system", content=str(system_prompt or "").strip())]
    for item in history or []:
        role = "user" if str(item.get("role") or "").strip().lower() == "user" else "assistant"
        content = item.get("text") if item.get("text") is not None else item.get("content")
        messages.append(Message(role=role, content=str(content or "")))
    messages.append(user_message(user_input))
    return messages
