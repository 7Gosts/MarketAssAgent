from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool


GuidanceType = Literal[
    "market_view",
    "trade_plan",
    "position_review",
    "research_view",
    "source_explain",
]


_GUIDANCE: dict[str, str] = {
    "market_view": (
        "输出顺序："
        "1) 结论与现价趋势：前两句交代 current_price 和 trend；"
        "2) 最近三根K线：用1-2句概括整体含义，不逐根点评；"
        "3) 关键位：给最近支撑、最近阻力；若有斐波那契位，说明其与关键位关系；"
        "4) 触发与失效：分别给多头/空头成立条件和失效条件；"
        "5) 风险与执行：只保留最关键的一条风险，并给执行纪律；"
        "6) 复核：给一句下次复核时点。"
    ),
    "trade_plan": (
        "必须包含：方向、入场触发、止损、止盈/目标、仓位、失效条件。"
        "价格只能来自工具事实，禁止凭空编造。"
    ),
    "position_review": (
        "输出顺序：当前风险 -> 原计划是否被破坏 -> 持有/减仓/止损动作 -> 复核时间。"
        "优先结合 last_snapshot 与台账事实。"
    ),
    "research_view": (
        "输出顺序：叙事结论 -> 主要分歧 -> 催化与风险 -> 需二次验证清单。"
        "研报/新闻是叙事证据，不能当作 entry/stop/tp。"
    ),
    "source_explain": (
        "说明依据时先给来源类型和时间范围，再给关键事实。"
        "缺少证据就明确说明，不要编造依据或引用不存在的工具结果。"
    ),
}


@tool
def get_response_guidance(guidance_type: GuidanceType) -> str:
    """按需获取短指导。仅在需要更严谨结构时调用，不要每轮都调用。"""
    key = str(guidance_type or "").strip()
    if key in _GUIDANCE:
        return _GUIDANCE[key]
    return "保持简洁、客观、条件化表达。"
