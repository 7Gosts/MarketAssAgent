from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool


GuidanceType = Literal[
    "market_view",
    "comparison",
    "trade_plan",
    "position_review",
    "rule_explain",
    "provenance",
    "profile_update",
]


_GUIDANCE: dict[str, str] = {
    "market_view": (
        "先给行情结论，再给关键事实（趋势、关键位、量价）。"
        "机会明确时给条件化操作建议，不明确时只给观察条件。"
    ),
    "comparison": (
        "聚焦相对强弱、结构差异和触发条件。"
        "没有明显优势时明确说明暂不选边。"
    ),
    "trade_plan": (
        "必须包含入场触发、止损、止盈/目标、仓位、失效条件。"
        "事实不足先查工具，不要凭空给价格。"
    ),
    "position_review": (
        "优先评估风险和计划是否被破坏，再给减仓/止损/持有建议。"
        "结合 last_snapshot 与台账，不要只复述行情。"
    ),
    "rule_explain": (
        "直接解释规则，术语最小化。可举例，不要强行调用行情工具。"
    ),
    "provenance": (
        "优先引用最近工具来源和 last_snapshot。"
        "缺少来源时明确说明上下文不足，不要编造依据。"
    ),
    "profile_update": (
        "仅在用户明确表达偏好/风险/风格变化时更新画像。"
        "更新时写明 reason 与 confidence，优先追加 observations/style_history。"
    ),
}


@tool
def get_response_guidance(guidance_type: GuidanceType) -> str:
    """按需获取短指导。仅在需要更严谨结构时调用，不要每轮都调用。"""
    return _GUIDANCE.get(guidance_type, "保持简洁、客观、条件化表达。")
