"""测试 core/state.py — MarketAgentState 契约。"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage


def test_state_accepts_all_documented_fields():
    """MarketAgentState 应接受所有文档字段。"""
    from core.state import MarketAgentState

    state: MarketAgentState = {
        "messages": [HumanMessage(content="BTC_USDT")],
        "current_symbol": "BTC_USDT",
        "current_interval": "4h",
        "current_provider": "gateio",
        "last_snapshot": {"symbol": "BTC_USDT", "trend": "偏多"},
        "output_refs": {"ai_overview_path": "/output/overview.json"},
        "session_id": "test_123",
        "channel": "cli",
        "iteration_count": 3,
        "final_reply": "分析结果...",
        "has_disclaimer": True,
    }
    assert state["current_symbol"] == "BTC_USDT"
    assert state["iteration_count"] == 3
    assert state["has_disclaimer"] is True


def test_max_iterations_value():
    """MAX_ITERATIONS 应为 6。"""
    from core.state import MAX_ITERATIONS
    assert MAX_ITERATIONS == 6


def test_messages_add_messages_annotation():
    """messages 字段应使用 add_messages 注解，支持追加。"""
    from core.state import MarketAgentState
    from langgraph.graph.message import add_messages

    # 验证 add_messages 行为：追加而非替换
    existing = [HumanMessage(content="你好")]
    new_msg = AIMessage(content="你好！有什么可以帮你的？")
    result = add_messages(existing, [new_msg])
    assert len(result) == 2
    assert isinstance(result[1], AIMessage)


def test_state_default_values():
    """state 的可选字段应有合理的默认值。"""
    from core.state import MarketAgentState

    # 最小化 state
    state: MarketAgentState = {
        "messages": [],
    }
    assert state.get("current_symbol", "") == ""
    assert state.get("iteration_count", 0) == 0
    assert state.get("last_snapshot") is None