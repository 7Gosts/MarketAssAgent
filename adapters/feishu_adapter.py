"""Feishu Adapter（支持真实消息发送 + 对话记忆）"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict

import httpx
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

from core.agent import MarketReActAgent
from config.runtime_config import get_llm_runtime_settings, require_llm_model, resolve_llm_temperature
from config.settings import settings
from schemas.conversation import ConversationEnvelope
from services.conversation_service import ConversationService
from utils.logging_utils import get_logger


FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
logger = get_logger(__name__)


def _create_chat_llm(temperature: float = 0.7) -> ChatOpenAI:
    """按统一 runtime_config 创建闲聊链路使用的 LLM。"""
    llm_settings = get_llm_runtime_settings()
    kwargs: dict[str, Any] = {
        "model": require_llm_model(llm_settings, context="Feishu chat"),
        "temperature": resolve_llm_temperature(llm_settings, fallback=temperature),
    }
    base_url = str(llm_settings.get("base_url") or "").strip()
    if base_url:
        kwargs["base_url"] = base_url
    api_key = str(llm_settings.get("api_key") or "").strip()
    if api_key:
        kwargs["api_key"] = api_key

    return ChatOpenAI(**kwargs)


async def get_tenant_access_token(
    app_id: str,
    app_secret: str,
    timeout_sec: float = 10.0,
) -> str:
    """获取飞书 tenant_access_token"""
    if not app_id or not app_secret:
        raise RuntimeError("缺少飞书 app_id/app_secret。")
    payload = {"app_id": app_id, "app_secret": app_secret}
    headers = {"Content-Type": "application/json; charset=utf-8"}
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        resp = await client.post(FEISHU_TOKEN_URL, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    if int(data.get("code", -1)) != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {data}")

    token = str(data.get("tenant_access_token") or "").strip()
    if not token:
        raise RuntimeError(f"tenant_access_token 为空: {data}")
    return token


async def send_text_message(
    tenant_access_token: str,
    receive_id: str,
    text: str,
    receive_id_type: str = "open_id",
    timeout_sec: float = 10.0,
) -> Dict[str, Any]:
    """发送飞书纯文本消息"""
    if not tenant_access_token:
        raise RuntimeError("缺少 tenant_access_token。")
    if not receive_id:
        raise RuntimeError("缺少 receive_id。")

    headers = {
        "Authorization": f"Bearer {tenant_access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    params = {"receive_id_type": receive_id_type}

    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        resp = await client.post(FEISHU_MESSAGE_URL, params=params, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

    if int(data.get("code", -1)) != 0:
        raise RuntimeError(f"发送飞书消息失败: {data}")
    return data


async def send_post_message(
    tenant_access_token: str,
    receive_id: str,
    text: str,
    receive_id_type: str = "open_id",
    timeout_sec: float = 10.0,
) -> Dict[str, Any]:
    """发送飞书 post 富文本消息（统一 Markdown 展示入口）。"""
    if not tenant_access_token:
        raise RuntimeError("缺少 tenant_access_token。")
    if not receive_id:
        raise RuntimeError("缺少 receive_id。")

    headers = {
        "Authorization": f"Bearer {tenant_access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "receive_id": receive_id,
        "msg_type": "post",
        "content": json.dumps(_build_post_body(text), ensure_ascii=False),
    }
    params = {"receive_id_type": receive_id_type}

    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        resp = await client.post(FEISHU_MESSAGE_URL, params=params, headers=headers, json=payload)
        if resp.status_code >= 400:
            body = resp.text
            raise RuntimeError(f"发送飞书 post 消息失败: status={resp.status_code}, body={body}")
        data = resp.json()

    if int(data.get("code", -1)) != 0:
        raise RuntimeError(f"发送飞书 post 消息失败: {data}")
    return data


async def send_interactive_message(
    tenant_access_token: str,
    receive_id: str,
    card: Dict[str, Any],
    receive_id_type: str = "open_id",
    timeout_sec: float = 10.0,
) -> Dict[str, Any]:
    """发送飞书 interactive 消息（轻量 markdown 卡片）。"""
    if not tenant_access_token:
        raise RuntimeError("缺少 tenant_access_token。")
    if not receive_id:
        raise RuntimeError("缺少 receive_id。")

    headers = {
        "Authorization": f"Bearer {tenant_access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "receive_id": receive_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    params = {"receive_id_type": receive_id_type}

    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        resp = await client.post(FEISHU_MESSAGE_URL, params=params, headers=headers, json=payload)
        if resp.status_code >= 400:
            body = resp.text
            raise RuntimeError(f"发送飞书 interactive 消息失败: status={resp.status_code}, body={body}")
        data = resp.json()

    if int(data.get("code", -1)) != 0:
        raise RuntimeError(f"发送飞书 interactive 消息失败: {data}")
    return data


def _build_markdown_card(text: str) -> Dict[str, Any]:
    normalized = (text or "").strip() or "（空响应）"
    max_chunk = 1100
    sections = [normalized[i : i + max_chunk] for i in range(0, len(normalized), max_chunk)] or [normalized]
    elements: list[dict[str, Any]] = []
    for idx, chunk in enumerate(sections):
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": chunk}})
        if idx != len(sections) - 1:
            elements.append({"tag": "hr"})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "市场助手回复"},
        },
        "elements": elements,
    }


def _build_post_body(text: str) -> Dict[str, Any]:
    """按飞书 post 结构构建内容，避免单元素过长导致 400。"""
    normalized = (text or "").strip() or "（空响应）"
    lines = [line.strip() for line in normalized.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        lines = [normalized]

    # 控制单文本段长度，减少被 Feishu 拒绝的概率
    max_chunk = 900
    content: list[list[dict[str, str]]] = []
    for line in lines:
        if len(line) <= max_chunk:
            content.append([{"tag": "text", "text": line}])
            continue
        start = 0
        while start < len(line):
            chunk = line[start : start + max_chunk]
            content.append([{"tag": "text", "text": chunk}])
            start += max_chunk

    return {
        "post": {
            "zh_cn": {
                "title": "市场助手回复",
                "content": content,
            }
        }
    }


class FeishuAdapter:
    """飞书机器人适配器（统一 Markdown/post 展示）"""

    def __init__(
        self,
        agent: MarketReActAgent,
        chat_llm: Any | None = None,
        router: Any | None = None,
        writer: Any | None = None,
        fallback_to_template: bool = True,
        conversation_service: ConversationService | None = None,
    ):
        self.agent = agent
        self.settings = settings
        self._token_cache: Dict[str, Any] = {}

        # 统一会话记忆编排层（由 app_factory 注入）
        self._conversation_service = conversation_service

        # 旧闲聊 LLM 仅作兼容兜底；主路径已统一走 ConversationService。
        self._chat_llm = chat_llm

        # 路由器 + 撰稿（P2 集成时注入）
        self._router = router
        self._writer = writer

        # 降级策略
        self._fallback_to_template = fallback_to_template

    def _make_chat_invoke(self):
        """返回一个可被 ConversationService 使用的 chat 调用函数"""
        async def _chat_invoke(text: str, session_id: str, history: list | None = None):
            messages = []
            if history:
                for h in history:
                    if h.get("role") == "user":
                        messages.append(HumanMessage(content=h.get("text", "")))
                    else:
                        messages.append(AIMessage(content=h.get("text", "")))
            messages.append(HumanMessage(content=text))
            if self._chat_llm is None:
                self._chat_llm = _create_chat_llm()
            resp = await self._chat_llm.ainvoke(messages)
            return {"reply": resp.content}

        return _chat_invoke

    async def handle_longconn_message(
        self,
        *,
        text: str,
        open_id: str = "",
        user_id: str = "",
        chat_id: str = "",
    ) -> Dict[str, Any]:
        """处理飞书长连接收到的一条文本消息。"""
        sender_id = open_id or user_id or "default"
        receive_id = chat_id or open_id or user_id
        receive_id_type = "chat_id" if chat_id else "open_id"

        if not receive_id:
            raise RuntimeError("Cannot determine Feishu receive_id")

        return await self._handle_text_message(
            message=text,
            open_id=sender_id,
            session_id=f"feishu_{sender_id}",
            receive_id=receive_id,
            receive_id_type=receive_id_type,
        )

    async def _handle_text_message(
        self,
        *,
        message: str,
        open_id: str,
        session_id: str,
        receive_id: str,
        receive_id_type: str,
    ) -> Dict[str, Any]:
        route: Dict[str, Any] = {"intent": "analyze"}

        try:
            if self._conversation_service is None:
                raise RuntimeError("ConversationService 未注入到 FeishuAdapter")

            envelope = await self._conversation_service.run(
                text=message,
                session_id=session_id,
                history_limit=8,
            )

            # 发送回复（统一 post，异常时降级 text）
            await self._send_reply(
                envelope=envelope,
                receive_id=receive_id,
                receive_id_type=receive_id_type,
            )

            return {"code": 0, "msg": "success"}

        except Exception as e:
            logger.exception("Feishu message handling error: %s", e)

            # 分层降级
            if self._fallback_to_template:
                try:
                    template = self._generate_template_fallback(route, str(e))
                    # 降级路径也尝试通过 service 保存（如果可用）
                    if self._conversation_service:
                        self._conversation_service.session_manager.save_reply(session_id, template)
                    await self._send_text_message(
                        text=template,
                        receive_id=receive_id,
                        receive_id_type=receive_id_type,
                    )
                    return {"code": 0, "msg": "success"}
                except Exception:
                    pass  # 降级也失败，抛出 HTTPException

            raise RuntimeError("飞书消息处理失败") from e

    def _extract_reply(self, result: Dict[str, Any]) -> str:
        """从 Agent 返回结果中提取回复文本"""
        messages = result.get("messages", [])
        if messages:
            last_msg = messages[-1]
            if hasattr(last_msg, "content"):
                return last_msg.content
            return str(last_msg)

        # 优先取 recommendation.text
        rec = result.get("recommendation") or {}
        if rec.get("text"):
            return rec["text"]

        return "已收到消息，正在处理中..."

    # ── 消息发送 ──

    async def _send_reply(
        self,
        envelope: ConversationEnvelope,
        receive_id: str,
        receive_id_type: str,
    ) -> None:
        """发送回复：优先 interactive markdown，其次 post，最后 text。"""
        mode = os.environ.get("FEISHU_REPLY_MODE", "interactive_md").strip().lower()
        text = envelope.reply_text

        if mode in {"interactive", "interactive_md", "card"}:
            try:
                await self._send_interactive_markdown(
                    text=text,
                    receive_id=receive_id,
                    receive_id_type=receive_id_type,
                )
                return
            except Exception as e:
                logger.warning("send_interactive_markdown failed, fallback to post: %s", e)

        if mode in {"interactive", "interactive_md", "card", "post"}:
            try:
                await self._send_post_message(
                    text=text,
                    receive_id=receive_id,
                    receive_id_type=receive_id_type,
                )
                return
            except Exception as e:
                logger.warning("send_post_message failed, fallback to text: %s", e)

        await self._send_text_message(
            text=text,
            receive_id=receive_id,
            receive_id_type=receive_id_type,
        )

    async def _send_interactive_markdown(
        self,
        text: str,
        receive_id: str,
        receive_id_type: str,
    ) -> None:
        token = await self._get_access_token()
        card = _build_markdown_card(text)
        await send_interactive_message(
            tenant_access_token=token,
            receive_id=receive_id,
            card=card,
            receive_id_type=receive_id_type,
        )

    async def _send_post_message(
        self,
        text: str,
        receive_id: str,
        receive_id_type: str,
    ) -> None:
        token = await self._get_access_token()
        await send_post_message(
            tenant_access_token=token,
            receive_id=receive_id,
            text=text,
            receive_id_type=receive_id_type,
        )

    async def _send_text_message(
        self,
        text: str,
        receive_id: str,
        receive_id_type: str,
    ) -> None:
        """发送纯文本消息"""
        token = await self._get_access_token()
        await send_text_message(
            tenant_access_token=token,
            receive_id=receive_id,
            text=text,
            receive_id_type=receive_id_type,
        )

    # ── Token 管理 ──

    async def _get_access_token(self) -> str:
        """带缓存的飞书 tenant_access_token 获取"""
        now = time.time()
        if self._token_cache.get("expires_at", 0) > now + 30:
            return self._token_cache["access_token"]

        app_id, app_secret = self.get_app_credentials()
        token = await get_tenant_access_token(app_id=app_id, app_secret=app_secret)

        # 飞书 token 默认 7200 秒过期
        self._token_cache["access_token"] = token
        self._token_cache["expires_at"] = now + 7200
        return token

    def get_app_credentials(self) -> tuple[str, str]:
        """获取飞书 app_id / app_secret，优先环境变量，再回退 YAML。"""
        if self.settings.FEISHU_APP_ID and self.settings.FEISHU_APP_SECRET:
            return self.settings.FEISHU_APP_ID, self.settings.FEISHU_APP_SECRET

        from config.runtime_config import get_analysis_config

        cfg = get_analysis_config()
        feishu = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
        app_id = str(feishu.get("app_id", "") or self.settings.FEISHU_APP_ID or "")
        app_secret = str(feishu.get("app_secret", "") or self.settings.FEISHU_APP_SECRET or "")
        if not app_id or not app_secret:
            raise RuntimeError("Missing FEISHU_APP_ID or FEISHU_APP_SECRET")
        return app_id, app_secret

    # ── Chat fallback ──

    async def _chat_fallback(self, message: str) -> str:
        """闲聊路径：使用独立 Chat LLM 回复"""
        try:
            if self._chat_llm is None:
                self._chat_llm = _create_chat_llm()
            response = await self._chat_llm.ainvoke([HumanMessage(content=message)])
            return response.content
        except Exception as e:
            logger.warning("[ChatFallback] LLM 回复失败: %s", e)
            return "你好！我是市场分析助手，可以帮你分析股票、加密货币等技术面。"

    # ── 降级模板 ──

    def _generate_template_fallback(
        self, route: Dict[str, Any], error_msg: str
    ) -> str:
        """根据意图生成模板化降级回复"""
        intent = route.get("intent", "chat")
        symbol = route.get("symbol", "")
        if intent in ("analyze", "analyze_multi", "followup"):
            return f"{symbol or '该标的'}的技术分析暂时不可用，请稍后重试。"
        if intent == "research":
            return "研报搜索暂时不可用，请稍后重试。"
        return "抱歉，我暂时无法处理您的请求，请稍后重试。"
