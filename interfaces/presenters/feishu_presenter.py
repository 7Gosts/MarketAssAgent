from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from schemas.conversation import ConversationEnvelope


@dataclass(frozen=True)
class FeishuDelivery:
    kind: Literal["text"]
    text: str = ""


class FeishuPresenter:
    """Translate envelope to Feishu text payload (markdown-first)."""

    def render(self, envelope: ConversationEnvelope) -> FeishuDelivery:
        return FeishuDelivery(kind="text", text=envelope.reply_text)
