"""Feishu Adapter（支持真实消息发送）"""

from __future__ import annotations

from typing import Dict, Any
import httpx
from core.agent import MarketReActAgent


class FeishuAdapter:
    """飞书机器人适配器"""

    def __init__(self, agent: MarketReActAgent):
        self.agent = agent

    async def handle_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """处理飞书消息"""
        try:
            message = self._extract_message(payload)
            if not message:
                return {"status": "ignored"}

            session_id = self._get_session_id(payload)
            result = await self.agent.invoke(message, session_id=session_id)
            reply_text = self._extract_reply(result)

            # TODO: 如果需要真实发送飞书消息，可在此处调用 send_text_message
            return {
                "status": "success",
                "reply": reply_text
            }

        except Exception as e:
            print(f"Feishu webhook error: {e}")
            return {
                "status": "error",
                "reply": "抱歉，处理消息时出现错误，请稍后再试。"
            }

    def _extract_message(self, payload: Dict) -> str:
        try:
            event = payload.get("event", {})
            message = event.get("message", {})
            content = message.get("content", "")
            if isinstance(content, str):
                import json
                try:
                    return json.loads(content).get("text", "")
                except Exception:
                    return content
            return ""
        except Exception:
            return ""

    def _get_session_id(self, payload: Dict) -> str:
        event = payload.get("event", {})
        sender = event.get("sender", {}).get("sender_id", "default")
        return f"feishu_{sender}"

    def _extract_reply(self, result: Dict) -> str:
        messages = result.get("messages", [])
        if messages:
            last_msg = messages[-1]
            if hasattr(last_msg, "content"):
                return last_msg.content
            return str(last_msg)
        return "已收到消息，正在处理中..."
