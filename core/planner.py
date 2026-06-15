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
    required_tools: list[ToolType] = Field(default_factory=list, description="本次需要调用的工具")
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

    @property
    def needs_tools(self) -> bool:
        return len(self.required_tools) > 0


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
        normalized = user_message.lower()
        if any(k in normalized for k in ["开单", "交易计划", "入场", "止损", "止盈", "做多", "做空"]):
            return _normalize_plan(
                ResponsePlan(
                    task_type="trade_plan",
                    required_tools=["market_data", "technical_analysis"],
                    response_style="directive",
                    key_focus="entry",
                ),
                user_message,
            )
        if any(k in normalized for k in ["仓位", "减仓", "加仓", "持仓", "还能拿吗", "该不该拿", "要不要拿", "要不要减仓"]):
            return _normalize_plan(
                ResponsePlan(
                    task_type="position_review",
                    required_tools=["market_data", "technical_analysis", "journal"],
                    response_style="cautious",
                    user_context_needed=True,
                    key_focus="risk",
                ),
                user_message,
            )
        if any(k in normalized for k in ["规则", "方法", "怎么理解", "右侧交易", "左侧交易"]):
            return _normalize_plan(
                ResponsePlan(task_type="rule_explain", required_tools=[], response_style="explanatory"),
                user_message,
            )
        if any(k in normalized for k in ["复盘", "台账", "记录"]):
            return _normalize_plan(
                ResponsePlan(task_type="journal_review", required_tools=["journal"], response_style="explanatory"),
                user_message,
            )
        if any(k in normalized for k in ["对比", "比较", "哪个好"]):
            return _normalize_plan(
                ResponsePlan(
                    task_type="comparison",
                    required_tools=["market_data", "technical_analysis"],
                    response_style="directive",
                ),
                user_message,
            )
        if any(k in normalized for k in ["研报", "研究", "消息", "基本面"]):
            return _normalize_plan(
                ResponsePlan(task_type="watchlist", required_tools=["research"], response_style="brief"),
                user_message,
            )
        if any(k in normalized for k in ["行情", "走势", "看看", "技术面", "短线"]):
            return _normalize_plan(
                ResponsePlan(
                    task_type="market_view",
                    required_tools=["market_data", "technical_analysis"],
                    response_style="directive",
                ),
                user_message,
            )
        return _normalize_plan(ResponsePlan(task_type="chat", required_tools=[], response_style="brief"), user_message)


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
    interval_hint = plan.interval_hint or _extract_interval_hint(user_message)
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


def _extract_interval_hint(text: str) -> str | None:
    if "短线" in text or "日内" in text:
        return "15m"
    if "小时" in text:
        return "1h"
    if "日线" in text:
        return "1d"
    return None


def _create_planner_llm() -> ChatOpenAI:
    cfg = get_llm_runtime_settings()
    return ChatOpenAI(
        model=require_llm_model(cfg, context="ResponsePlanner"),
        temperature=resolve_llm_temperature(cfg, fallback=0.2),
        base_url=cfg.get("base_url") or None,
        api_key=cfg.get("api_key") or None,
    )
