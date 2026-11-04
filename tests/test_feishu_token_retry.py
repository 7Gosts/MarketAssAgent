from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from infrastructure.adapters import feishu_adapter
from infrastructure.adapters.feishu_adapter import FeishuAdapter
from schemas.conversation import ConversationEnvelope


def test_interactive_send_refreshes_cached_token_once_on_invalid_access_token(monkeypatch):
    adapter = FeishuAdapter(conversation_service=object())  # type: ignore[arg-type]
    adapter._token_cache = {"access_token": "old-token", "expires_at": time.time() + 3600}

    async def fetch_token(*, app_id: str, app_secret: str) -> str:
        return "fresh-token"

    calls: list[dict[str, Any]] = []

    async def send_message(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError(
                '发送飞书 interactive 消息失败: status=400, body={"code":99991663,'
                '"msg":"Invalid access token for authorization."}'
            )
        return {"code": 0, "data": {"message_id": "om_ok"}}

    monkeypatch.setattr(adapter, "get_app_credentials", lambda: ("app-id", "app-secret"))
    monkeypatch.setattr(feishu_adapter, "get_tenant_access_token", fetch_token)
    monkeypatch.setattr(feishu_adapter, "send_interactive_message", send_message)

    result = asyncio.run(
        adapter._send_rendered_interactive(
            text="hello",
            receive_id="oc_test",
            receive_id_type="chat_id",
        )
    )

    assert result["data"]["message_id"] == "om_ok"
    assert [call["tenant_access_token"] for call in calls] == ["old-token", "fresh-token"]
    assert calls[0]["card"] == calls[1]["card"]


def test_plan_monitor_event_uses_conversation_reply_and_rejects_aborted_turn(monkeypatch):
    class Conversation:
        aborted = False
        calls = []

        async def run(self, **kwargs):
            self.calls.append(kwargs)
            return ConversationEnvelope(
                reply_text="ETH 条件触发，先核对当前结构。",
                meta={"aborted": self.aborted},
            )

    conversation = Conversation()
    adapter = FeishuAdapter(conversation_service=conversation)  # type: ignore[arg-type]
    sent = []

    async def send_reply(**kwargs):
        sent.append(kwargs["envelope"].reply_text)
        return {"code": 0}

    monkeypatch.setattr(adapter, "_send_reply", send_reply)
    scope_meta = {
        "transport": "feishu",
        "tenant_id": "tenant-a",
        "visibility_scope": "private",
        "visibility_scope_id": "ou_alice",
        "actor_id": "ou_alice",
    }
    args = {
        "text": "【后台计划监控事件，非用户原话】ETH 条件触发。",
        "scope_meta": scope_meta,
        "receive_id": "ou_alice",
        "receive_id_type": "open_id",
        "external_message_id": "plan_1_trigger_1",
    }

    result = asyncio.run(adapter.handle_plan_monitor_event(**args))
    assert result["msg"] == "success"
    assert conversation.calls[0]["session_id"].startswith("sess_")
    assert conversation.calls[0]["extra_meta"]["external_message_id"] == "plan_1_trigger_1"
    assert sent == ["ETH 条件触发，先核对当前结构。"]

    conversation.aborted = True
    with pytest.raises(RuntimeError, match="飞书消息处理失败"):
        asyncio.run(adapter.handle_plan_monitor_event(**args))
    assert sent == ["ETH 条件触发，先核对当前结构。"]
