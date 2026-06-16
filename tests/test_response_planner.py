from __future__ import annotations

from core.planner import ResponsePlan, _normalize_plan
from services.response_planner import ResponsePlanner


def test_planner_fallback_detects_trade_plan():
    """_fallback_plan 已弱化，只保留极少数闲聊兜底，其他情况返回中性 plan。"""
    plan = ResponsePlanner(llm=object())._fallback_plan("给出一个合适的 btc 开单建议")

    # 新逻辑：代码不做意图预判，返回中性 plan（task_type="chat"）
    assert plan.task_type == "chat"
    assert plan.required_tools == []


def test_planner_fallback_does_not_force_rule_explain_into_market_view():
    """_fallback_plan 已弱化，不再根据关键词强行分类。"""
    plan = ResponsePlanner(llm=object())._fallback_plan("右侧交易是什么意思")

    # 新逻辑：返回中性 plan
    assert plan.task_type == "chat"
    assert plan.required_tools == []


def test_planner_fallback_detects_position_review_phrase():
    """_fallback_plan 已弱化，不再根据关键词强行分类。"""
    plan = ResponsePlanner(llm=object())._fallback_plan("长安汽车还能拿吗")

    # 新逻辑：返回中性 plan
    assert plan.task_type == "chat"
    assert plan.required_tools == []


def test_planner_fallback_detects_profile_update():
    """_fallback_plan 能识别画像维护关键词，返回 profile_update 任务。"""
    plan = ResponsePlanner(llm=object())._fallback_plan("我现在偏多，以后按短线风格看")

    assert plan.task_type == "profile_update"
    # _normalize_plan 兜底会设置 required_tools=["profile"]
    assert plan.required_tools == ["profile"]


def test_planner_fallback_profile_update_beats_chat_keyword():
    """画像维护优先级应高于礼貌闲聊词。"""
    plan = ResponsePlanner(llm=object())._fallback_plan("谢谢，记住我现在偏多，以后按短线风格看")

    assert plan.task_type == "profile_update"
    assert plan.required_tools == ["profile"]


def test_planner_fallback_keeps_chat_for_normal_talk():
    """普通闲聊仍然保持 chat 任务。"""
    plan = ResponsePlanner(llm=object())._fallback_plan("今天天气怎么样")

    assert plan.task_type == "chat"
    assert plan.required_tools == []


def test_response_plan_accepts_profile_tooltype():
    """ResponsePlan 接受 required_tools=["profile"]。"""
    plan = ResponsePlan(task_type="profile_update", required_tools=["profile"])
    assert plan.task_type == "profile_update"
    assert plan.required_tools == ["profile"]


def test_normalize_plan_forces_profile_update():
    """_normalize_plan 能把画像关键词输入修正为 profile_update，即使原 plan 是 chat。"""
    plan = ResponsePlan(task_type="chat", required_tools=[])
    corrected = _normalize_plan(plan, "我现在偏多，以后按短线风格看")

    assert corrected.task_type == "profile_update"
    assert corrected.required_tools == ["profile"]
