"""Feishu Adapter（支持真实消息发送 + 对话记忆）"""

from __future__ import annotations

import json
import time
from typing import Any, Dict

import httpx
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

from core.agent import MarketReActAgent
from config.runtime_config import get_llm_runtime_settings, require_llm_model, resolve_llm_temperature
from config.settings import settings
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


async def send_interactive_message(
    tenant_access_token: str,
    receive_id: str,
    card: Dict[str, Any],
    receive_id_type: str = "open_id",
    timeout_sec: float = 10.0,
) -> Dict[str, Any]:
    """发送飞书交互式卡片消息"""
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
        resp.raise_for_status()
        data = resp.json()

    if int(data.get("code", -1)) != 0:
        raise RuntimeError(f"发送飞书卡片失败: {data}")
    return data


class FeishuAdapter:
    """飞书机器人适配器（支持对话记忆 + 卡片消息）"""

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

        # Chat LLM（闲聊路径使用）
        self._chat_llm = chat_llm or _create_chat_llm()

        # 路由器 + 撰稿（P2 集成时注入）
        self._router = router
        self._writer = writer

        # 降级策略
        self._fallback_to_template = fallback_to_template

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
            # 意图路由（如果 router 已注入）
            if self._router:
                try:
                    route = await self._router.route(
                        message, session_id=session_id, open_id=open_id
                    )
                except Exception as e:
                    logger.warning("[Router] 路由失败，默认走分析路径: %s", e)
                    route = {"intent": "analyze"}

            intent = route.get("intent", "analyze")

            if intent == "chat":
                # ── 闲聊路径：直接 LLM 回复 ──
                reply_text = await self._chat_fallback(message)
                result: Dict[str, Any] = {"intent": "chat", "recommendation": {"text": reply_text}}
            else:
                # ── 分析路径：通过 ConversationService 统一编排记忆 ──
                if self._conversation_service is None:
                    raise RuntimeError("ConversationService 未注入到 FeishuAdapter")

                conv_result = await self._conversation_service.run(
                    text=message,
                    session_id=session_id,
                    history_limit=8,
                )
                result = conv_result["result"]
                reply_text = conv_result["reply_text"]

                # Writer 润色（如果 writer 已注入）
                if self._writer and reply_text:
                    try:
                        reply_text = await self._writer.polish_or_fallback(
                            reply_text, user_question=message
                        )
                    except Exception as e:
                        logger.warning("[Writer] 润色失败，使用原文: %s", e)

            # 注意：记忆读写已由 ConversationService 统一处理，此处不再重复调用

            # 发送回复（卡片优先，纯文本 fallback）
            await self._send_reply(
                text=reply_text,
                result=result,
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
        text: str,
        result: Dict[str, Any],
        receive_id: str,
        receive_id_type: str,
    ) -> None:
        """发送回复：优先尝试卡片，失败则降级纯文本"""
        # 尝试构建飞书卡片
        from formatters.feishu_card import format_analysis_as_card

        card, card_err = format_analysis_as_card(result).build_safe()
        if card_err is None and card:
            try:
                token = await self._get_access_token()
                await send_interactive_message(
                    tenant_access_token=token,
                    receive_id=receive_id,
                    card=card,
                    receive_id_type=receive_id_type,
                )
                return
            except Exception as e:
                logger.warning("[FeishuCard] 卡片发送失败，降级纯文本: %s", e)

        # fallback: 纯文本
        await self._send_text_message(
            text=text,
            receive_id=receive_id,
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
