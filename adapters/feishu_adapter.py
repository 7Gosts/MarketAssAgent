"""Feishu Adapter（支持真实消息发送 + 对话记忆）"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException, Request
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

from core.agent import MarketReActAgent
from config.runtime_config import get_llm_runtime_settings, require_llm_model
from config.settings import settings
from memory.feishu_memory import FeishuMemory, FeishuMemoryConfig


FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


def _create_chat_llm(temperature: float = 0.7) -> ChatOpenAI:
    """按统一 runtime_config 创建闲聊链路使用的 LLM。"""
    llm_settings = get_llm_runtime_settings()
    kwargs: dict[str, Any] = {
        "model": require_llm_model(llm_settings, context="Feishu chat"),
        "temperature": temperature,
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
        memory: FeishuMemory | None = None,
        chat_llm: Any | None = None,
        router: Any | None = None,
        writer: Any | None = None,
        fallback_to_template: bool = True,
    ):
        self.agent = agent
        self.settings = settings
        self._token_cache: Dict[str, Any] = {}

        # 对话记忆
        self._memory = memory or FeishuMemory(FeishuMemoryConfig.from_yaml())

        # Chat LLM（闲聊路径使用）
        self._chat_llm = chat_llm or _create_chat_llm()

        # 路由器 + 撰稿（P2 集成时注入）
        self._router = router
        self._writer = writer

        # 降级策略
        self._fallback_to_template = fallback_to_template

    async def handle_message(self, payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
        """处理飞书消息（完整流程：记忆 → 路由 → Agent/Chat → Writer → 卡片/文本 → 降级）"""
        # URL 验证事件
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge")}

        self._verify_webhook(payload, request)

        try:
            message = self._extract_message(payload)
            if not message:
                return {"code": 0, "msg": "success"}

            open_id = self._get_open_id(payload)
            session_id = self._get_session_id(payload)

            # 保存用户消息到记忆
            self._memory.save_message(open_id, "user", message)

            # 意图路由（如果 router 已注入）
            route: Dict[str, Any] = {"intent": "analyze"}
            if self._router:
                try:
                    route = await self._router.route(
                        message, session_id=session_id, open_id=open_id
                    )
                except Exception as e:
                    print(f"[Router] 路由失败，默认走分析路径: {e}")
                    route = {"intent": "analyze"}

            intent = route.get("intent", "analyze")

            if intent == "chat":
                # ── 闲聊路径：直接 LLM 回复 ──
                reply_text = await self._chat_fallback(message)
                result: Dict[str, Any] = {"intent": "chat", "recommendation": {"text": reply_text}}
            else:
                # ── 分析路径：Agent ReAct ──
                history = self._memory.load_history_window(open_id, rounds=4)
                result = await self.agent.invoke(
                    message, session_id=session_id, history=history
                )
                raw_text = self._extract_reply(result)

                # Writer 润色（如果 writer 已注入）
                if self._writer:
                    try:
                        reply_text = await self._writer.polish_or_fallback(
                            raw_text, user_question=message
                        )
                    except Exception as e:
                        print(f"[Writer] 润色失败，使用原文: {e}")
                        reply_text = raw_text
                else:
                    reply_text = raw_text

                result["polished_text"] = reply_text

            # 保存助手回复到记忆
            self._memory.save_message(open_id, "assistant", reply_text, action=intent)

            # 发送回复（卡片优先，纯文本 fallback）
            await self._send_reply(reply_text, result, payload)

            return {"code": 0, "msg": "success"}

        except Exception as e:
            print(f"Feishu webhook error: {e}")

            # 分层降级
            if self._fallback_to_template:
                try:
                    open_id = self._get_open_id(payload)
                    message = self._extract_message(payload) or ""
                    template = self._generate_template_fallback(route, str(e))
                    self._memory.save_message(open_id, "assistant", template, action="fallback")
                    await self._send_text_message(template, payload)
                    return {"code": 0, "msg": "success"}
                except Exception:
                    pass  # 降级也失败，抛出 HTTPException

            raise HTTPException(status_code=500, detail="飞书消息处理失败")

    # ── Webhook 验证 ──

    def _verify_webhook(self, payload: Dict[str, Any], request: Request) -> None:
        """验证飞书 webhook 的 verification token"""
        if not self.settings.FEISHU_VERIFICATION_TOKEN:
            return

        token = self._extract_verification_token(payload, request)
        if not token or token != self.settings.FEISHU_VERIFICATION_TOKEN:
            raise HTTPException(status_code=401, detail="Feishu webhook token invalid")

    def _extract_verification_token(self, payload: Dict[str, Any], request: Request) -> Optional[str]:
        """从 payload 或 Authorization header 中提取 verification token"""
        token = payload.get("token")
        if token:
            return token

        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth.split(" ", 1)[1].strip()

        return None

    # ── 消息提取 ──

    def _extract_message(self, payload: Dict[str, Any]) -> str:
        """从飞书事件 payload 中提取用户消息内容"""
        try:
            event = payload.get("event", {})
            message = event.get("message", {})
            content = message.get("content", "")
            if isinstance(content, str):
                try:
                    return json.loads(content).get("text", "")
                except Exception:
                    return content
            return ""
        except Exception:
            return ""

    def _get_open_id(self, payload: Dict[str, Any]) -> str:
        """提取用户 open_id"""
        event = payload.get("event", {})
        sender = event.get("sender", {}).get("sender_id", {})
        return sender.get("open_id") or sender.get("user_id") or "default"

    def _get_session_id(self, payload: Dict[str, Any]) -> str:
        """基于 open_id 生成 session_id"""
        return f"feishu_{self._get_open_id(payload)}"

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
        self, text: str, result: Dict[str, Any], payload: Dict[str, Any]
    ) -> None:
        """发送回复：优先尝试卡片，失败则降级纯文本"""
        # 尝试构建飞书卡片
        from formatters.feishu_card import format_analysis_as_card

        card, card_err = format_analysis_as_card(result).build_safe()
        if card_err is None and card:
            try:
                receive_id, receive_id_type = self._get_receive_info(payload)
                token = await self._get_access_token()
                await send_interactive_message(
                    tenant_access_token=token,
                    receive_id=receive_id,
                    card=card,
                    receive_id_type=receive_id_type,
                )
                return
            except Exception as e:
                print(f"[FeishuCard] 卡片发送失败，降级纯文本: {e}")

        # fallback: 纯文本
        await self._send_text_message(text, payload)

    async def _send_text_message(self, text: str, payload: Dict[str, Any]) -> None:
        """发送纯文本消息"""
        receive_id, receive_id_type = self._get_receive_info(payload)
        token = await self._get_access_token()
        await send_text_message(
            tenant_access_token=token,
            receive_id=receive_id,
            text=text,
            receive_id_type=receive_id_type,
        )

    def _get_receive_info(self, payload: Dict[str, Any]) -> tuple[str, str]:
        """提取回复目标（群聊 chat_id 或单聊 open_id）"""
        event = payload.get("event", {})
        message = event.get("message", {})
        sender = event.get("sender", {}).get("sender_id", {})

        receive_id_type = "open_id"
        receive_id = sender.get("open_id") or sender.get("user_id")

        if message.get("chat_id"):
            receive_id_type = "chat_id"
            receive_id = message.get("chat_id")

        if not receive_id:
            raise RuntimeError("Cannot determine Feishu receive_id")

        return receive_id, receive_id_type

    # ── Token 管理 ──

    async def _get_access_token(self) -> str:
        """带缓存的飞书 tenant_access_token 获取"""
        now = time.time()
        if self._token_cache.get("expires_at", 0) > now + 30:
            return self._token_cache["access_token"]

        if not self.settings.FEISHU_APP_ID or not self.settings.FEISHU_APP_SECRET:
            # 尝试从 YAML 配置读取
            from config.runtime_config import get_analysis_config
            cfg = get_analysis_config()
            feishu = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
            app_id = str(feishu.get("app_id", "") or self.settings.FEISHU_APP_ID or "")
            app_secret = str(feishu.get("app_secret", "") or self.settings.FEISHU_APP_SECRET or "")
            if not app_id or not app_secret:
                raise RuntimeError("Missing FEISHU_APP_ID or FEISHU_APP_SECRET")
        else:
            app_id = self.settings.FEISHU_APP_ID
            app_secret = self.settings.FEISHU_APP_SECRET

        token = await get_tenant_access_token(app_id=app_id, app_secret=app_secret)

        # 飞书 token 默认 7200 秒过期
        self._token_cache["access_token"] = token
        self._token_cache["expires_at"] = now + 7200
        return token

    # ── Chat fallback ──

    async def _chat_fallback(self, message: str) -> str:
        """闲聊路径：使用独立 Chat LLM 回复"""
        try:
            response = await self._chat_llm.ainvoke([HumanMessage(content=message)])
            return response.content
        except Exception as e:
            print(f"[ChatFallback] LLM 回复失败: {e}")
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
