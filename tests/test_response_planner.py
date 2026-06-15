from __future__ import annotations

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
