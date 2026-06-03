"""测试 core/nodes.py — LangGraph 节点函数契约。"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


# ── restore_session_node ──────────────────────────────────────


def test_restore_session_noop_without_mgr():
    """session_mgr 为 None 时，restore_session_node 应为 no-op。"""
    from core.nodes import restore_session_node
    state = {"session_id": "test", "messages": []}
    result = restore_session_node(state, session_mgr=None)
    assert result == {}


def test_restore_session_restores_from_session(mock_session_mgr):
    """restore_session_node 应从 session 恢复 snapshot 等字段。"""
    from core.nodes import restore_session_node
    from memory.session_manager import SessionState

    # 预设 session state
    mock_session_mgr._state = SessionState(
        open_id="test_session",
        last_symbols=["BTC_USDT"],
        last_interval="4h",
        last_provider="gateio",
        last_facts_bundle={"symbol": "BTC_USDT", "trend": "偏多"},
        last_output_refs={"ai_overview_path": "/output/overview.json"},
    )

    state = {
        "session_id": "test_session",
        "messages": [],
        "current_symbol": "",
        "current_interval": "",
        "current_provider": "",
    }
    result = restore_session_node(state, session_mgr=mock_session_mgr)
    assert result["current_symbol"] == "BTC_USDT"
    assert result["current_interval"] == "4h"
    assert result["current_provider"] == "gateio"
    assert result["last_snapshot"]["trend"] == "偏多"


def test_restore_session_does_not_overwrite_existing(mock_session_mgr):
    """restore_session_node 不应覆盖 state 中已有的字段。"""
    from core.nodes import restore_session_node
    from memory.session_manager import SessionState

    mock_session_mgr._state = SessionState(
        open_id="test_session",
        last_symbols=["ETH_USDT"],
        last_interval="1d",
    )

    state = {
        "session_id": "test_session",
        "messages": [],
        "current_symbol": "BTC_USDT",  # 已有值
        "current_interval": "",          # 空值
    }
    result = restore_session_node(state, session_mgr=mock_session_mgr)
    assert result.get("current_symbol") is None  # 不覆盖
    assert result["current_interval"] == "1d"      # 填充空值


# ── init_context_node ─────────────────────────────────────────


def test_init_context_adds_system_prompt():
    """init_context_node 应添加包含 SYSTEM_PROMPT 的 SystemMessage。"""
    from core.nodes import init_context_node
    state = {"messages": [HumanMessage(content="你好")], "iteration_count": 0}
    result = init_context_node(state)
    messages = result["messages"]
    assert any(isinstance(m, SystemMessage) and "金融行情分析助手" in m.content for m in messages)


def test_init_context_injects_snapshot():
    """init_context_node 应在 state 有 last_snapshot 时注入 snapshot 上下文。"""
    from core.nodes import init_context_node
    state = {
        "messages": [HumanMessage(content="止损呢")],
        "iteration_count": 0,
        "last_snapshot": {"symbol": "BTC_USDT", "trend": "偏多", "last_price": 67234.5},
        "current_symbol": "BTC_USDT",
    }
    result = init_context_node(state)
    messages = result["messages"]
    # 应有系统注入消息
    system_injections = [
        m for m in messages
        if isinstance(m, SystemMessage) and "[系统注入]" in m.content
    ]
    assert len(system_injections) > 0
    assert "BTC_USDT" in system_injections[0].content


def test_init_context_force_final_on_max_iterations():
    """iteration_count 达上限时应注入强制最终回答消息。"""
    from core.nodes import init_context_node
    from core.state import MAX_ITERATIONS
    state = {
        "messages": [HumanMessage(content="继续分析")],
        "iteration_count": MAX_ITERATIONS,
    }
    result = init_context_node(state)
    messages = result["messages"]
    force_msgs = [
        m for m in messages
        if isinstance(m, HumanMessage) and "直接给出最终回答" in m.content
    ]
    assert len(force_msgs) > 0


# ── should_continue ───────────────────────────────────────────


def test_should_continue_tools_when_tool_calls():
    """有 tool_calls 时应返回 'tools'。"""
    from core.nodes import should_continue
    state = {
        "messages": [AIMessage(content="", tool_calls=[{"name": "fetch_analysis_bundle", "args": {}, "id": "tc1"}])],
        "iteration_count": 0,
    }
    assert should_continue(state) == "tools"


def test_should_continue_supervisor_when_no_tool_calls():
    """无 tool_calls 时应返回 'supervisor'。"""
    from core.nodes import should_continue
    state = {
        "messages": [AIMessage(content="分析结果")],
        "iteration_count": 0,
    }
    assert should_continue(state) == "supervisor"


def test_should_continue_supervisor_on_max_iterations():
    """达上限时即使有 tool_calls 也应返回 'supervisor'。"""
    from core.nodes import should_continue
    from core.state import MAX_ITERATIONS
    state = {
        "messages": [AIMessage(content="", tool_calls=[{"name": "fetch_analysis_bundle", "args": {}, "id": "tc1"}])],
        "iteration_count": MAX_ITERATIONS,
    }
    assert should_continue(state) == "supervisor"


def test_should_continue_empty_messages():
    """空消息时应返回 'supervisor'。"""
    from core.nodes import should_continue
    assert should_continue({"messages": []}) == "supervisor"


# ── observe_node ──────────────────────────────────────────────


def test_observe_node_increments_count():
    """observe_node 应递增 iteration_count。"""
    from core.nodes import observe_node
    state = {"messages": [], "iteration_count": 2}
    result = observe_node(state)
    assert result["iteration_count"] == 3


def test_observe_node_extracts_snapshot():
    """observe_node 应从 ToolMessage 提取 snapshot。"""
    from core.nodes import observe_node
    tool_content = json.dumps({
        "analysis_result": {
            "symbol": "BTC_USDT",
            "trend": "偏多",
            "last_price": 67234.5,
        }
    })
    state = {
        "messages": [ToolMessage(content=tool_content, tool_call_id="tc1")],
        "iteration_count": 0,
    }
    result = observe_node(state)
    assert result["last_snapshot"]["symbol"] == "BTC_USDT"
    assert result["current_symbol"] == "BTC_USDT"


# ── persist_snapshot_node ──────────────────────────────────────


def test_persist_snapshot_noop_without_mgr():
    """session_mgr 为 None 时，persist_snapshot_node 应为 no-op。"""
    from core.nodes import persist_snapshot_node
    state = {"session_id": "test", "last_snapshot": {"symbol": "BTC_USDT"}}
    result = persist_snapshot_node(state, session_mgr=None)
    assert result == {}


def test_persist_snapshot_saves_to_session(mock_session_mgr):
    """persist_snapshot_node 应将 snapshot 保存到 session。"""
    from core.nodes import persist_snapshot_node
    snapshot = {"symbol": "BTC_USDT", "trend": "偏多"}
    state = {
        "session_id": "test_session",
        "last_snapshot": snapshot,
        "final_reply": "分析完成",
    }
    persist_snapshot_node(state, session_mgr=mock_session_mgr)
    assert "test_session" in mock_session_mgr.saved_snapshots
    assert mock_session_mgr.saved_snapshots["test_session"]["symbol"] == "BTC_USDT"


def test_persist_snapshot_noop_when_no_snapshot(mock_session_mgr):
    """无 snapshot 时不应保存。"""
    from core.nodes import persist_snapshot_node
    state = {"session_id": "test_session", "last_snapshot": None}
    persist_snapshot_node(state, session_mgr=mock_session_mgr)
    assert len(mock_session_mgr.saved_snapshots) == 0