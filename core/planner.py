from __future__ import annotations

import re
from typing import Literal

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from config.runtime_config import get_llm_runtime_settings, require_llm_model, resolve_llm_temperature
from core.planner_prompt import PLANNER_SYSTEM_PROMPT


TaskType = Literal[
    "chat",
    "market_view",
    "trade_plan",
    "position_review",
    "rule_explain",
    "journal_review",
    "comparison",
    "watchlist",
]

ToolType = Literal["market_data", "technical_analysis", "research", "sim_account", "journal"]


class ResponsePlan(BaseModel):
    """市场助手的响应规划"""

    task_type: TaskType = Field(default="chat", description="当前用户任务类型")
    required_tools: list[ToolType] = Field(
        default_factory=list,
        description="建议使用的工具分组（非强制）。orchestrator 默认会暴露全量工具，让 LLM 自主选择。"
    )
    response_style: Literal["directive", "explanatory", "cautious", "brief"] = Field(
        default="directive", description="回复风格"
    )
    needs_snapshot: bool = Field(default=True, description="是否需要注入上一轮分析快照")
    key_focus: str | None = Field(default=None, description="用户重点关注点，如 entry/stop/risk/trend 等")
    user_context_needed: bool = Field(default=False, description="是否需要使用用户长期画像")

    # Compatibility fields used by existing orchestrator/envelope flow.
    preferred_blocks: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    symbol_hint: str | None = None
    interval_hint: str | None = None
    render_mode: Literal["text", "card", "auto"] = "auto"

    model_config = {"extra": "forbid"}


class ResponsePlanner:
    """响应规划器 - 助手级任务理解核心"""

    def __init__(self, llm: ChatOpenAI | None = None):
        self.llm = llm or _create_planner_llm()
        self.parser = PydanticOutputParser(pydantic_object=ResponsePlan)
        self.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", PLANNER_SYSTEM_PROMPT),
                ("user", "{user_message}\n\n当前会话简要信息：{session_summary}"),
            ]
        ).partial(format_instructions=self.parser.get_format_instructions())

    async def plan(self, user_message: str, session_summary: str = "") -> ResponsePlan:
        """生成响应计划"""
        chain = self.prompt | self.llm | self.parser
        try:
            plan = await chain.ainvoke(
                {
                    "user_message": user_message,
                    "session_summary": session_summary or "用户无历史偏好",
                }
            )
            return _normalize_plan(plan, user_message)
        except Exception:
            return self._fallback_plan(user_message)

    def _fallback_plan(self, user_message: str) -> ResponsePlan:
        """极简兜底逻辑。

        设计原则：代码不做意图预判，把决策权交给 LLM。
        - 只保留极少数明显不需要工具的场景（闲聊兜底）。
        - 其他情况返回中性 plan（required_tools 为空），让 LLM 在 ReAct 过程中自主决定是否调用工具、调用哪些工具。
        """
        normalized = user_message.lower()

        # 极简兜底：明显是闲聊的场景，避免不必要的工具调用
        if any(k in normalized for k in ["你好", "谢谢", "再见", "hello", "thanks", "hi"]):
            return _normalize_plan(
                ResponsePlan(task_type="chat", required_tools=[], response_style="brief"),
                user_message,
            )

        # 其他情况：返回中性 plan，required_tools 为空
        # LLM 会根据完整的工具列表和 Prompt 自主决策
        return _normalize_plan(
            ResponsePlan(task_type="chat", required_tools=[], response_style="directive"),
            user_message,
        )


def summarize_history(history: list[dict[str, str]] | None) -> str:
    lines: list[str] = []
    for item in (history or [])[-6:]:
        role = "用户" if item.get("role") == "user" else "助手"
        text = str(item.get("text") or "").strip()
        if text:
            lines.append(f"{role}: {text[:200]}")
    return "\n".join(lines)


def _normalize_plan(plan: ResponsePlan, user_message: str) -> ResponsePlan:
    symbol_hint = plan.symbol_hint or _extract_symbol_hint(user_message)
    interval_hint = plan.interval_hint or _extract_interval_hint(user_message, symbol_hint)
    sections = plan.sections or _default_sections(plan.task_type)
    preferred_blocks = plan.preferred_blocks or _default_blocks(plan.task_type)
    render_mode = plan.render_mode
    if render_mode == "auto" and plan.task_type in {"market_view", "trade_plan", "position_review", "comparison"}:
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


def _default_sections(task_type: TaskType) -> list[str]:
    if task_type == "trade_plan":
        return ["direction", "entry", "stop_take_profit", "position", "risk"]
    if task_type == "position_review":
        return ["position_status", "risk", "adjustment", "invalid_condition"]
    if task_type == "rule_explain":
        return ["plain_explanation", "example", "pitfalls"]
    if task_type == "comparison":
        return ["summary", "relative_strength", "choice", "risk"]
    if task_type == "market_view":
        return ["conclusion", "key_levels", "structure", "risk"]
    if task_type == "journal_review":
        return ["review_summary", "mistakes", "next_actions"]
    if task_type == "watchlist":
        return ["candidates", "signal", "risk"]
    return ["answer"]


def _default_blocks(task_type: TaskType) -> list[str]:
    if task_type in {"market_view", "comparison"}:
        return ["market_analysis", "risk_warning"]
    if task_type == "trade_plan":
        return ["market_analysis", "trade_plan", "risk_warning"]
    if task_type == "position_review":
        return ["market_analysis", "position_advice", "risk_warning"]
    if task_type == "rule_explain":
        return ["rule_explain", "text_fallback"]
    if task_type == "journal_review":
        return ["journal_summary", "risk_warning"]
    if task_type == "watchlist":
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


def _extract_interval_hint(text: str, symbol: str | None = None) -> str | None:
    """从用户消息中提取周期提示，未指定时根据市场类型返回默认值。

    规则：
    - 用户明确提到周期关键词 → 按关键词返回
    - 未指定 + 虚拟币（含 USDT/BTC/ETH/SOL 等）→ 默认 "4h"
    - 未指定 + 其他市场（A股、黄金等）→ 默认 "1d"
    """
    # 用户明确指定
    if "短线" in text or "日内" in text:
        return "15m"
    if "小时" in text:
        return "1h"
    if "日线" in text:
        return "1d"

    # 未指定时的默认策略
    if symbol:
        s = symbol.upper()
        is_crypto = any(kw in s for kw in ["USDT", "USD", "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"])
        if is_crypto:
            return "4h"
        else:
            return "1d"

    # 没有 symbol 信息时，尝试从文本中猜测是否是虚拟币
    upper = text.upper()
    if any(kw in upper for kw in ["USDT", "BTC", "ETH", "SOL", "虚拟币", "加密", "数字货币"]):
        return "4h"

    return "1d"  # 最终兜底：日线


def _create_planner_llm() -> ChatOpenAI:
    cfg = get_llm_runtime_settings()
    return ChatOpenAI(
        model=require_llm_model(cfg, context="ResponsePlanner"),
        temperature=resolve_llm_temperature(cfg, fallback=0.2),
        base_url=cfg.get("base_url") or None,
        api_key=cfg.get("api_key") or None,
    )
