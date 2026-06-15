from __future__ import annotations

from services.response_planner import ResponsePlanner


def test_planner_fallback_detects_trade_plan():
    plan = ResponsePlanner(llm=object())._fallback_plan("给出一个合适的 btc 开单建议")

    assert plan.task_type == "trade_plan"
    assert plan.needs_tools is True
    assert plan.symbol_hint == "BTCUSDT"
    assert "entry" in plan.sections


def test_planner_fallback_does_not_force_rule_explain_into_market_view():
    plan = ResponsePlanner(llm=object())._fallback_plan("右侧交易是什么意思")

    assert plan.task_type == "rule_explain"
    assert plan.needs_tools is False
    assert plan.render_mode == "auto"


def test_planner_fallback_detects_position_review_phrase():
    plan = ResponsePlanner(llm=object())._fallback_plan("长安汽车还能拿吗")

    assert plan.task_type == "position_review"
    assert "market_data" in plan.required_tools
