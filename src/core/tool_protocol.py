from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal


ToolExecutorFn = Callable[..., Any] | Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolContext:
    session_id: str
    request_id: str
    operation_id: str = ""
    turn_user_event_id: str = ""
    conversation_scope: Any | None = None
    event_store: Any | None = None
    storage: Any | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    execute: ToolExecutorFn
    version: str = "1"
    effect_class: Literal[
        "pure_query",
        "idempotent_write",
        "queryable_effect",
        "opaque_effect",
    ] = "opaque_effect"
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
