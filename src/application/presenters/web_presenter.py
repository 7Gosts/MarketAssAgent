from __future__ import annotations

from schemas.conversation import ConversationEnvelope


class WebPresenter:
    """Render the shared envelope into the clean Web/API response shape."""

    def render(self, *, envelope: ConversationEnvelope) -> dict[str, object]:
        return {"envelope": envelope.model_dump(mode="json")}
