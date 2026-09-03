from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal


MessageRole = Literal["system", "user", "assistant", "tool"]


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


def tool_message(*, tool_call_id: str, name: str, result: Any) -> Message:
    if isinstance(result, str):
        content = result
    else:
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
