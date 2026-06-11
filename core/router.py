"""Router — 意图路由器，前置分类用户输入决定处理路径"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from config.runtime_config import get_router_config
from utils.logging_utils import get_logger


logger = get_logger(__name__)


class Intent(str, Enum):
    """意图分类枚举"""
    ANALYZE = "analyze"              # 单标的技术分析
    ANALYZE_MULTI = "analyze_multi"  # 多标的对比分析
    QUOTE = "quote"                  # 行情报价
    COMPARE = "compare"              # 标的对比
    RESEARCH = "research"            # 研报搜索
    FOLLOWUP = "followup"            # 追问（依赖上次分析上下文）
    CHAT = "chat"                    # 闲聊 / 不相关


_DEFAULT_ROUTER_PROMPT = """你是意图分类器。根据用户最新消息和最近对话上下文，判断用户意图。

意图分类：
- analyze: 请求分析某标的技术面（含"看看行情"、"分析一下"、"走势如何"）
- analyze_multi: 请求同时分析多个标的（含"这几个标的"、"对比下"）
- quote: 仅请求最新报价或简单行情数据
- compare: 请求对比两个或多个标的
- research: 请求查找研报或概念板块（含"研报"、"研究报告"）
- followup: 追问上次分析主题的风险、入场位等（需要上下文）
- chat: 闲聊或不涉及市场分析

输出严格的 JSON: {"intent": "...", "symbol": "...", "symbols": [...], "interval": "..."}

其中 symbol/symbols/interval 为可选，无法确定时设为 null。仅输出 JSON，不要添加任何额外文本。"""


class Router:
    """意图路由器：前置分类用户输入，决定走哪条处理路径"""

    def __init__(
        self,
        llm: Any | None = None,
        *,
        context_rounds: int | None = None,
        temperature: float | None = None,
        session_manager: Any | None = None,
    ) -> None:
        # 从 YAML 读取默认配置
        cfg = get_router_config()
        self._context_rounds = context_rounds or int(cfg.get("router_context_rounds", 4))
        requested_temp = temperature if temperature is not None else float(cfg.get("router_temperature", 0.0))

        # LLM 初始化：复用主 Agent 的 provider 配置
        if llm is None:
            from config.runtime_config import (
                get_llm_runtime_settings,
                require_llm_model,
                resolve_llm_temperature,
            )
            llm_settings = get_llm_runtime_settings()
            llm = ChatOpenAI(
                model=require_llm_model(llm_settings, context="Router"),
                temperature=resolve_llm_temperature(llm_settings, fallback=requested_temp),
                base_url=llm_settings.get("base_url") or None,
                api_key=llm_settings.get("api_key") or None,
            )
        self._llm = llm
        self._system_prompt = _DEFAULT_ROUTER_PROMPT
        self._session_manager = session_manager

    async def route(
        self,
        text: str,
        *,
        session_id: str = "default",
        open_id: str = "",
    ) -> dict[str, Any]:
        """分类用户意图

        Args:
            text: 用户最新消息
            session_id: 会话标识
            open_id: 用户标识（用于加载历史）

        Returns:
            {"intent": "analyze", "symbol": "BTCUSDT", "interval": "4h", ...}
        """
        context = ""

        # 加载对话历史作为上下文（使用注入的 session_manager）
        if open_id and self._session_manager:
            try:
                session_id = f"feishu_{open_id}"
                history = self._session_manager.get_recent_messages(session_id, limit=self._context_rounds)
                if history:
                    context = "\n最近对话:\n" + "\n".join(
                        f"{'用户' if h.get('role') == 'user' else '助手'}: {h.get('text', '')}"
                        for h in history
                    )
            except Exception as e:
                logger.warning("[Router] 加载历史失败: %s", e)
        elif open_id and not self._session_manager:
            logger.warning("[Router] 未注入 session_manager，跳过历史加载")

        messages = [
            SystemMessage(content=self._system_prompt),
            HumanMessage(content=f"{context}\n用户最新消息: {text}" if context else text),
        ]

        response = await self._llm.ainvoke(messages)
        return self._parse_response(response.content)

    def _parse_response(self, content: str) -> dict[str, Any]:
        """解析 LLM 输出为结构化路由结果（容错处理）"""
        # 尝试提取 JSON
        try:
            # 直接解析
            result = json.loads(content.strip())
            if isinstance(result, dict) and "intent" in result:
                result.setdefault("symbol", None)
                result.setdefault("symbols", None)
                result.setdefault("interval", None)
                return result
        except json.JSONDecodeError:
            pass

        # 尝试从文本中提取 JSON 块
        try:
            match = re.search(r'\{[^}]+\}', content, re.DOTALL)
            if match:
                result = json.loads(match.group())
                if isinstance(result, dict):
                    result.setdefault("intent", "chat")
                    return result
        except (json.JSONDecodeError, ValueError):
            pass

        # fallback: 简单关键词匹配
        intent = self._keyword_fallback(content, "")
        return {"intent": intent, "symbol": None, "symbols": None, "interval": None}

    def _keyword_fallback(self, llm_output: str, user_text: str) -> str:
        """关键词兜底路由（当 LLM JSON 解析完全失败时）"""
        text = (user_text or llm_output).lower()

        if any(kw in text for kw in ["研报", "研究报告", "研究", "分析报告", "概念板块"]):
            return "research"
        if any(kw in text for kw in ["对比", "比较", "哪个好"]):
            return "compare"
        if any(kw in text for kw in ["报价", "现价", "价格", "多少"]):
            return "quote"
        if any(kw in text for kw in ["分析", "行情", "走势", "看看", "趋势", "技术面"]):
            return "analyze"
        if any(kw in text for kw in ["风险", "入场", "止损", "止盈", "建议"]):
            return "followup"

        return "chat"
