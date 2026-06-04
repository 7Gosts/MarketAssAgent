from typing import Dict, Any
from core.agent import MarketReActAgent


class FeishuAdapter:
    """飞书机器人适配器"""
    
    def __init__(self, agent: MarketReActAgent):
        self.agent = agent
    
    async def handle_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """处理飞书消息"""
        try:
            # 提取消息内容（根据飞书 webhook 格式调整）
            message = self._extract_message(payload)
            if not message:
                return {"status": "ignored"}
            
            session_id = self._get_session_id(payload)
            
            # 调用核心 Agent
            result = await self.agent.invoke(
                user_input=message,
                session_id=session_id
            )
            
            reply_text = self._extract_reply(result)
            
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
        """从飞书 payload 中提取用户消息"""
        try:
            # 飞书事件格式适配
            event = payload.get("event", {})
            message = event.get("message", {})
            content = message.get("content", "")
            
            # 简单文本消息处理
            if isinstance(content, str):
                import json
                try:
                    content_dict = json.loads(content)
                    return content_dict.get("text", "")
                except:
                    return content
            return ""
        except:
            return ""
    
    def _get_session_id(self, payload: Dict) -> str:
        """生成或获取会话 ID"""
        event = payload.get("event", {})
        sender = event.get("sender", {}).get("sender_id", "default")
        return f"feishu_{sender}"
    
    def _extract_reply(self, result: Dict) -> str:
        """从 Agent 返回结果中提取最终回复文本"""
        messages = result.get("messages", [])
        if messages:
            last_msg = messages[-1]
            if hasattr(last_msg, "content"):
                return last_msg.content
            return str(last_msg)
        return "已收到消息，正在处理中..."
