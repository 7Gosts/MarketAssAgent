from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import router
from schemas.conversation import ConversationEnvelope


class _ConversationServiceStub:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def run(self, **_: object) -> ConversationEnvelope:
        self.events.append("run")
        return ConversationEnvelope(
            reply_text="reply",
            pending_turn_summary={"request_id": "req_001"},
        )

    async def persist_delivered_turn_summary(self, envelope: ConversationEnvelope) -> None:
        assert envelope.reply_text == "reply"
        self.events.append("summary")


def test_api_persists_turn_summary_after_response_is_rendered():
    service = _ConversationServiceStub()
    app = FastAPI()
    app.include_router(router)
    app.state.services = SimpleNamespace(conversation_service=service)

    response = TestClient(app).post(
        "/agent/run",
        json={"text": "看看 ETH 4h", "session_id": "api_test"},
    )

    assert response.status_code == 200
    assert response.json()["envelope"]["reply_text"] == "reply"
    assert "pending_turn_summary" not in response.json()["envelope"]
    assert service.events == ["run", "summary"]
