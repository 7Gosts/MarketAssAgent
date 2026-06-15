from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from config.runtime_config import get_llm_runtime_settings, require_llm_model, resolve_llm_temperature
from schemas.response_plan import ResponsePlan
from utils.logging_utils import get_logger


logger = get_logger(__name__)


_PLANNER_PROMPT = """你是市场助手的任务规划器，只判断用户这次真正想让助手做什么。

不要分析行情，不要给建议，只输出 JSON。

task_type 可选：
- chat: 闲聊、问候、非市场任务
- market_view: 看行情、走势、技术面、短线判断
- trade_plan: 开单建议、交易计划、入场/止损/止盈
- position_advice: 仓位、减仓、加仓、持仓风险
- rule_explain: 交易规则、方法解释、策略说明
- comparison: 多个标的比较、哪个好
- research: 研报、消息、基本面资料
- journal_review: 复盘、查看历史操作、台账

输出 JSON 字段：
{"task_type": "...", "needs_tools": true/false, "preferred_blocks": [...], "sections": [...], "tone": "direct|conversational|careful", "symbol_hint": null, "interval_hint": null, "render_mode": "auto|text|card"}

规则：
- 用户要当前行情、开单、仓位判断、比较时 needs_tools=true。
- 用户问规则解释、交易方法、普通聊天时 needs_tools=false，除非明确要求结合当前行情。
- 开单建议必须 task_type=trade_plan。
- 不确定时选择最贴近用户目标的 task_type，不要默认 market_view。"""


class ResponsePlanner:
    """Plans the assistant's response shape before execution."""

    def __init__(self, llm: Any | None = None) -> None:
        if llm is None:
            cfg = get_llm_runtime_settings()
            llm = ChatOpenAI(
                model=require_llm_model(cfg, context="ResponsePlanner"),
                temperature=resolve_llm_temperature(cfg, fallback=0.2),
                base_url=cfg.get("base_url") or None,
                api_key=cfg.get("api_key") or None,
            )
        self._llm = llm

    async def plan(self, text: str, history: list[dict[str, str]] | None = None) -> ResponsePlan:
        history_text = _format_history(history or [])
        content = f"最近对话:\n{history_text}\n\n用户最新消息: {text}" if history_text else text
        try:
            response = await self._llm.ainvoke(
                [
                    SystemMessage(content=_PLANNER_PROMPT),
                    HumanMessage(content=content),
                ]
            )
            return self._parse_plan(str(response.content), text)
        except Exception as exc:
            logger.warning("[ResponsePlanner] 规划失败，使用关键词兜底: %s", exc)
            return self._fallback_plan(text)

    def _parse_plan(self, content: str, user_text: str) -> ResponsePlan:
        try:
            payload = json.loads(content.strip())
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            payload = json.loads(match.group()) if match else {}

        if not isinstance(payload, dict):
            return self._fallback_plan(user_text)

        try:
            plan = ResponsePlan(**payload)
        except Exception:
            return self._fallback_plan(user_text)

        return _normalize_plan(plan, user_text)

    def _fallback_plan(self, text: str) -> ResponsePlan:
        normalized = text.lower()
        if any(k in normalized for k in ["开单", "交易计划", "入场", "止损", "止盈", "做多", "做空"]):
            return _normalize_plan(
                ResponsePlan(task_type="trade_plan", needs_tools=True, render_mode="card"),
                text,
            )
        if any(k in normalized for k in ["仓位", "减仓", "加仓", "持仓"]):
            return _normalize_plan(
                ResponsePlan(task_type="position_advice", needs_tools=True, render_mode="card"),
                text,
            )
        if any(k in normalized for k in ["规则", "方法", "怎么理解", "右侧交易", "左侧交易"]):
            return _normalize_plan(ResponsePlan(task_type="rule_explain", needs_tools=False), text)
        if any(k in normalized for k in ["对比", "比较", "哪个好"]):
            return _normalize_plan(ResponsePlan(task_type="comparison", needs_tools=True, render_mode="card"), text)
        if any(k in normalized for k in ["研报", "研究", "消息", "基本面"]):
            return _normalize_plan(ResponsePlan(task_type="research", needs_tools=True), text)
        if any(k in normalized for k in ["行情", "走势", "看看", "技术面", "短线"]):
            return _normalize_plan(ResponsePlan(task_type="market_view", needs_tools=True, render_mode="card"), text)
        return _normalize_plan(ResponsePlan(task_type="chat", needs_tools=False, render_mode="text"), text)


def _normalize_plan(plan: ResponsePlan, user_text: str) -> ResponsePlan:
    symbol_hint = plan.symbol_hint or _extract_symbol_hint(user_text)
    interval_hint = plan.interval_hint or _extract_interval_hint(user_text)
    sections = plan.sections or _default_sections(plan.task_type)
    preferred_blocks = plan.preferred_blocks or _default_blocks(plan.task_type)
    render_mode = plan.render_mode
    if render_mode == "auto" and plan.task_type in {"market_view", "trade_plan", "position_advice", "comparison"}:
        render_mode = "card"

    return plan.model_copy(
        update={
            "symbol_hint": symbol_hint,
            "interval_hint": interval_hint,
            "sections": sections,
            "preferred_blocks": preferred_blocks,
            "render_mode": render_mode,
        }
    )


def _default_sections(task_type: str) -> list[str]:
    if task_type == "trade_plan":
        return ["direction", "entry", "stop_take_profit", "position", "risk"]
    if task_type == "position_advice":
        return ["position_status", "risk", "adjustment", "invalid_condition"]
    if task_type == "rule_explain":
        return ["plain_explanation", "example", "pitfalls"]
    if task_type == "comparison":
        return ["summary", "relative_strength", "choice", "risk"]
    if task_type == "market_view":
        return ["conclusion", "key_levels", "structure", "risk"]
    return ["answer"]


def _default_blocks(task_type: str) -> list[str]:
    if task_type in {"market_view", "comparison"}:
        return ["market_analysis", "risk_warning"]
    if task_type == "trade_plan":
        return ["market_analysis", "trade_plan", "risk_warning"]
    if task_type == "position_advice":
        return ["market_analysis", "position_advice", "risk_warning"]
    if task_type == "research":
        return ["research_summary", "risk_warning"]
    return ["text_fallback"]


def _extract_symbol_hint(text: str) -> str | None:
    upper = text.upper()
    for token in re.findall(r"[A-Z]{2,10}(?:USDT|USD)?|[0-9]{6}|AU[0-9]{1,4}", upper):
        if token in {"USDT", "USD"}:
            continue
        if token == "BTC":
            return "BTCUSDT"
        if token == "ETH":
            return "ETHUSDT"
        return token
    if "比特币" in text:
        return "BTCUSDT"
    if "以太" in text:
        return "ETHUSDT"
    return None


def _extract_interval_hint(text: str) -> str | None:
    if "短线" in text or "日内" in text:
        return "15m"
    if "小时" in text:
        return "1h"
    if "日线" in text:
        return "1d"
    return None


def _format_history(history: list[dict[str, str]]) -> str:
    lines = []
    for item in history[-6:]:
        role = "用户" if item.get("role") == "user" else "助手"
        text = str(item.get("text") or "").strip()
        if text:
            lines.append(f"{role}: {text[:200]}")
    return "\n".join(lines)
