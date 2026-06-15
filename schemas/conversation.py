from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


BlockType = Literal[
    "market_analysis",
    "trade_plan",
    "position_advice",
    "rule_explain",
    "journal_summary",
    "research_summary",
    "risk_warning",
    "text_fallback",
]


class ConversationBlock(BaseModel):
    """Transport-neutral content block produced by the conversation core."""

    type: BlockType
    title: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class DeliveryHint(BaseModel):
    """Display preference computed upstream, consumed by presenters."""

    mode: Literal["text", "rich"] = "text"
    card_style: str = "plain"
    has_rich_content: bool = False
    block_summary: list[str] = Field(default_factory=list)


class ConversationEnvelope(BaseModel):
    """Stable response contract shared by Web, Feishu, and future transports."""

    version: str = "1.0"
    reply_text: str
    blocks: list[ConversationBlock] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
    delivery_hint: DeliveryHint = Field(default_factory=DeliveryHint)
