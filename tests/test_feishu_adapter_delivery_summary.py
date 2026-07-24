from __future__ import annotations

import asyncio

import pytest

from infrastructure.adapters.feishu_adapter import FeishuAdapter
from schemas.conversation import ConversationEnvelope


class _ConversationServiceStub:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def run(self, **_: object) -> ConversationEnvelope:
        return ConversationEnvelope(reply_text="reply")

    async def persist_delivered_turn_summary(self, envelope: ConversationEnvelope) -> None:
        assert envelope.reply_text == "reply"
        self.events.append("summary")


def test_feishu_persists_summary_only_after_reply_is_sent():
    service = _ConversationServiceStub()
    adapter = FeishuAdapter(conversation_service=service)  # type: ignore[arg-type]

    async def _send_reply(**_: object) -> None:
        service.events.append("send")

    adapter._send_reply = _send_reply  # type: ignore[method-assign]

    result = asyncio.run(
        adapter._handle_text_message(
            message="看看 ETH 4h",
            open_id="ou_test",
            session_id="feishu_ou_test",
            receive_id="oc_test",
            receive_id_type="chat_id",
        )
    )

    assert result == {"code": 0, "msg": "success"}
    assert service.events == ["send", "summary"]


def test_feishu_does_not_persist_summary_when_reply_send_fails():
    service = _ConversationServiceStub()
    adapter = FeishuAdapter(conversation_service=service, fallback_to_template=False)  # type: ignore[arg-type]

    async def _send_reply(**_: object) -> None:
        raise RuntimeError("Feishu unavailable")

    adapter._send_reply = _send_reply  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="飞书消息处理失败"):
        asyncio.run(
            adapter._handle_text_message(
                message="看看 ETH 4h",
                open_id="ou_test",
                session_id="feishu_ou_test",
                receive_id="oc_test",
                receive_id_type="chat_id",
            )
        )

    assert service.events == []
