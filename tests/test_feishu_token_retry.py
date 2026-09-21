from __future__ import annotations

import asyncio
import time
from typing import Any

from infrastructure.adapters import feishu_adapter
from infrastructure.adapters.feishu_adapter import FeishuAdapter


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
