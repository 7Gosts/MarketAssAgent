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
    "profile_update",   # 新增：用户画像维护任务
]

ToolType = Literal["market_data", "technical_analysis", "research", "sim_account", "journal", "profile"]


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
    error_reason: str | None = Field(
        default=None,
        description="仅在 fallback 场景使用，标识 planner 未按预期完成的原因。",
    )
    required_provenance: bool = Field(
        default=False,
        description="当用户追问依据来源时，要求回复中包含来源链路。",
    )

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
            return self._fallback_plan(user_message, error_reason="planner_exception")

    def _fallback_plan(self, user_message: str, error_reason: str | None = None) -> ResponsePlan:
        """极简兜底逻辑。"""
        normalized = user_message.lower()

        # 画像维护类输入 → profile_update
        profile_keywords = [
            "我偏好", "我喜欢", "我习惯", "我风险", "我仓位",
            "我现在偏多", "我现在偏空", "我最近偏多", "我最近偏空",
            "我改成", "以后按", "记住", "我的风格", "my risk", "my style", "remember"
        ]
        if any(k in normalized for k in profile_keywords):
            return _normalize_plan(
                ResponsePlan(task_type="profile_update", required_tools=[], response_style="directive", error_reason=error_reason),
                user_message,
            )

        # 极简兜底：明显是闲聊的场景
        if any(k in normalized for k in ["你好", "谢谢", "再见", "hello", "thanks", "hi"]):
            return _normalize_plan(
                ResponsePlan(task_type="chat", required_tools=[], response_style="brief", error_reason=error_reason),
                user_message,
            )

        # 其他情况返回中性 plan
        return _normalize_plan(
            ResponsePlan(task_type="chat", required_tools=[], response_style="directive", error_reason=error_reason),
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
    # 画像维护兜底：即使 planner 返回 chat，只要命中画像关键词就强制修正为 profile_update
    profile_keywords = [
        "我偏好", "我喜欢", "我习惯", "我风险", "我仓位",
        "我现在偏多", "我现在偏空", "我最近偏多", "我最近偏空",
        "我改成", "以后按", "记住", "我的风格", "my risk", "my style", "remember"
    ]
    normalized_msg = user_message.lower()
    if any(k in normalized_msg for k in profile_keywords):
        if plan.task_type != "profile_update":
            plan = plan.model_copy(update={"task_type": "profile_update"})
        if not plan.required_tools:
            plan = plan.model_copy(update={"required_tools": ["profile"]})

    symbol_hint = plan.symbol_hint or _extract_symbol_hint(user_message)
    interval_hint = plan.interval_hint or _extract_interval_hint(user_message, symbol_hint)
    sections = plan.sections or _default_sections(plan.task_type)
    preferred_blocks = plan.preferred_blocks or _default_blocks(plan.task_type)
    required_provenance = bool(plan.required_provenance or _requires_provenance(user_message))
    user_context_needed = bool(plan.user_context_needed or _requires_user_context(user_message))
    render_mode = plan.render_mode
    if render_mode == "auto" and plan.task_type in {"market_view", "trade_plan", "position_review", "comparison"}:
        render_mode = "card"

    return plan.model_copy(
        update={
            "symbol_hint": symbol_hint,
            "interval_hint": interval_hint,
            "sections": sections,
            "preferred_blocks": preferred_blocks,
            "required_provenance": required_provenance,
            "user_context_needed": user_context_needed,
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
    if task_type == "profile_update":
        return ["profile_update", "reason", "confidence"]
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
    if task_type == "profile_update":
        return ["profile_update", "risk_warning"]
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


def _requires_provenance(text: str) -> bool:
    t = str(text or "").lower()
    keywords = [
        "怎么知道",
        "依据",
        "来源",
        "从哪",
        "为什么这么说",
        "证据",
        "based on what",
        "source",
        "how do you know",
    ]
    return any(k in t for k in keywords)


def _requires_user_context(text: str) -> bool:
    t = str(text or "").lower()
    keywords = [
        "我的",
        "我持仓",
        "我仓位",
        "我偏好",
        "我喜欢",
        "我习惯",
        "刚才那笔",
        "之前那笔",
        "my position",
        "my risk",
        "my style",
    ]
    return any(k in t for k in keywords)
