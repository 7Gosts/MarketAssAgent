from __future__ import annotations

from typing import Any

from core.planner import ResponsePlan


BASE_SYSTEM_PROMPT = """你是一个经验丰富、专业、谨慎且直接的市场助手。
目标：帮助用户做出更好的交易决策，保持客观、简洁、非情绪化表达。

回复格式要求（必须遵守）：
- 使用清晰的 Markdown 结构输出（短标题、列表、加粗）。
- 关键价格位和操作条件用 **粗体** 强调。
- 风险提示放在最后，并使用 `>` 引用格式。
- 不要输出 JSON，不要输出模板占位符。"""


TASK_PROMPTS = {
    "trade_plan": "用户想要交易计划，请给出清晰的入场、止损、止盈、仓位建议，并说明关键条件。",
    "position_review": "用户在讨论已有仓位，请重点评估风险、是否该减仓或止盈，给出具体行动建议。",
    "rule_explain": "用户在询问规则，请用清晰易懂的方式解释，不需要调用行情工具。",
    "market_view": "用户想看行情，请客观分析当前结构、关键位和可能走势。",
    "comparison": "用户希望对比多个标的，请给出差异、优劣和风险点。",
    "journal_review": "用户在做复盘，请总结执行偏差、关键错误和下一步改进。",
    "watchlist": "用户在筛选观察标的，请给出优先级与触发条件。",
    "profile_update": "用户在表达交易偏好、风险态度或风格变化，请主动调用 get_user_profile / update_user_profile 工具维护画像。",  # 新增
}


def get_full_prompt(
    plan: ResponsePlan,
    user_message: str,
    *,
    context: dict[str, Any] | None = None,
) -> str:
    task_prompt = TASK_PROMPTS.get(plan.task_type, "")
    user_profile = {}
    if isinstance(context, dict):
        raw_profile = context.get("user_profile")
        if isinstance(raw_profile, dict):
            user_profile = raw_profile

    profile_str = ""
    if user_profile:
        profile_str = (
            "用户画像："
            f"风格={user_profile.get('preferred_style', 'unknown')}，"
            f"风险偏好={user_profile.get('risk_profile', 'unknown')}，"
            f"常用标的={user_profile.get('favorite_symbols', [])}"
        )

    # 注入当前用户画像 storage_key（优先 user_id，其次 session_id）
    storage_key = ""
    if isinstance(context, dict):
        storage_key = context.get("storage_key") or context.get("user_id") or context.get("session_id") or ""

    storage_key_line = ""
    if storage_key:
        storage_key_line = f"\n当前用户画像 storage_key: {storage_key}\n如需调用 get_user_profile / update_user_profile，必须使用该 storage_key，不要自己编造。"

    return f"""{BASE_SYSTEM_PROMPT}

{profile_str if user_profile else ''}{storage_key_line}

当前任务类型：{plan.task_type}
重点关注：{plan.key_focus or '无'}
回复风格：{plan.response_style}

{task_prompt}

用户输入：{user_message}
请简洁、客观地回复，避免夸张和口语化表达。"""
