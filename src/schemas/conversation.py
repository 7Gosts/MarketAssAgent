from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConversationEnvelope(BaseModel):
    """Markdown-first response contract shared by Web and Feishu."""

    version: str = "1.2"
    reply_text: str
    meta: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
    pending_turn_summary: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)
