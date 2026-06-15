from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


TaskType = Literal[
    "chat",
    "market_view",
    "trade_plan",
    "position_advice",
    "rule_explain",
    "comparison",
    "research",
    "journal_review",
]


class ResponsePlan(BaseModel):
    """User-goal plan used before tool execution and rendering."""

    task_type: TaskType = "chat"
    role: str = "market_assistant"
    tone: Literal["direct", "conversational", "careful"] = "direct"
    needs_tools: bool = False
    preferred_blocks: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    symbol_hint: str | None = None
    interval_hint: str | None = None
    render_mode: Literal["text", "card", "auto"] = "auto"
