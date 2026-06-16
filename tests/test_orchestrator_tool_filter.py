from __future__ import annotations

from core.orchestrator import AssistantOrchestrator
from core.planner import ResponsePlan
from services.envelope_builder import EnvelopeBuilder


class _Tool:
    def __init__(self, name: str):
        self.name = name


def _make_orchestrator() -> AssistantOrchestrator:
    tools = [
        _Tool("fetch_market_data"),
        _Tool("analyze_market"),
        _Tool("get_key_levels"),
        _Tool("evaluate_structure"),
        _Tool("analyze_multi"),
        _Tool("search_research_reports"),
        _Tool("simulate_open_position"),
        _Tool("get_journal_status"),
        _Tool("get_user_profile"),
        _Tool("update_user_profile"),
    ]
    return AssistantOrchestrator(
        agent_graph=object(),
        chat_llm=object(),
        tools_registry=tools,
        envelope_builder=EnvelopeBuilder(),
    )


def test_filter_tools_by_plan_trade_plan():
    """当 required_tools 只有大类时，返回全量工具（让 LLM 自主决策）。"""
    orchestrator = _make_orchestrator()
    plan = ResponsePlan(task_type="trade_plan", required_tools=["market_data", "technical_analysis"])

    allowed = orchestrator._filter_tools_by_plan(plan)

    # 新逻辑：只有大类时不做严格过滤，返回全量工具
    assert set(allowed) == {
        "fetch_market_data",
        "analyze_market",
        "get_key_levels",
        "evaluate_structure",
        "analyze_multi",
        "search_research_reports",
        "simulate_open_position",
        "get_journal_status",
        "get_user_profile",
        "update_user_profile",
    }


def test_filter_tools_by_plan_empty_required_tools_returns_all():
    orchestrator = _make_orchestrator()
    plan = ResponsePlan(task_type="chat", required_tools=[])

    allowed = orchestrator._filter_tools_by_plan(plan)

    assert allowed == [
        "fetch_market_data",
        "analyze_market",
        "get_key_levels",
        "evaluate_structure",
        "analyze_multi",
        "search_research_reports",
        "simulate_open_position",
        "get_journal_status",
        "get_user_profile",
        "update_user_profile",
    ]


def test_filter_tools_by_plan_profile_update():
    """profile_update 任务应能拿到 profile tools。"""
    orchestrator = _make_orchestrator()
    plan = ResponsePlan(task_type="profile_update", required_tools=[])

    allowed = orchestrator._filter_tools_by_plan(plan)

    assert "get_user_profile" in allowed
    assert "update_user_profile" in allowed
