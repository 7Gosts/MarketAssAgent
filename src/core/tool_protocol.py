from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal


ToolExecutorFn = Callable[..., Any] | Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolContext:
    session_id: str
    request_id: str
    storage: Any | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    execute: ToolExecutorFn
    side_effect: Literal["read", "write"] = "read"
    requires_context: bool = False

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
