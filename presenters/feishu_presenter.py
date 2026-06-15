from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from schemas.conversation import ConversationEnvelope


@dataclass(frozen=True)
class FeishuDelivery:
    kind: Literal["text", "card"]
    text: str = ""
    card: dict[str, Any] | None = None


class FeishuPresenter:
    """Translate a conversation envelope into a Feishu delivery payload."""

    def render(self, envelope: ConversationEnvelope) -> FeishuDelivery:
        if envelope.delivery_hint.mode != "rich":
            return FeishuDelivery(kind="text", text=envelope.reply_text)

        if envelope.delivery_hint.card_style in {"market_analysis", "assistant_response"}:
            from formatters.feishu_card import format_market_analysis_envelope_as_card

            card, error = format_market_analysis_envelope_as_card(envelope).build_safe()
            if error is None and card:
                return FeishuDelivery(kind="card", card=card)

        return FeishuDelivery(kind="text", text=envelope.reply_text)
