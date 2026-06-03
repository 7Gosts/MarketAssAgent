"""测试多轮对话 — snapshot 持久化与恢复。"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


def test_first_turn_saves_snapshot(mock_session_mgr):
    """第一轮分析后，snapshot 应被 persist_snapshot_node 保存。"""
    from core.nodes import observe_node, persist_snapshot_node

    # 模拟第一轮：observe_node 从工具结果提取 snapshot
    tool_content = json.dumps({
        "analysis_result": {
            "symbol": "BTC_USDT",
            "interval": "4h",
            "provider": "gateio",
            "trend": "偏多",
            "last_price": 67234.5,
        }
    })
    state_after_observe = observe_node({
        "messages": [ToolMessage(content=tool_content, tool_call_id="tc1")],
        "iteration_count": 0,
    })

    # 模拟 supervisor 产出 final_reply
    state_with_reply = {
        "session_id": "multi_turn_test",
        **state_after_observe,
        "final_reply": "BTC_USDT 偏多趋势\n仅供技术分析与程序化演示，不构成投资建议。",
    }

    # persist_snapshot_node 应保存
    persist_snapshot_node(state_with_reply, session_mgr=mock_session_mgr)

    # 验证保存
    assert "multi_turn_test" in mock_session_mgr.saved_snapshots
    saved = mock_session_mgr.saved_snapshots["multi_turn_test"]
    assert saved["symbol"] == "BTC_USDT"
    assert saved["trend"] == "偏多"


def test_second_turn_restores_snapshot(mock_session_mgr):
    """第二轮追问时，restore_session_node 应恢复上一轮 snapshot。"""
    from core.nodes import init_context_node, restore_session_node
    from memory.session_manager import SessionState

    # 预设 session state（模拟第一轮已保存的 snapshot）
    mock_session_mgr._state = SessionState(
        open_id="multi_turn_test",
        last_symbols=["BTC_USDT"],
        last_interval="4h",
        last_provider="gateio",
        last_facts_bundle={
            "symbol": "BTC_USDT",
            "trend": "偏多",
            "last_price": 67234.5,
            "wyckoff_123": {"side": "long", "triggered": True, "entry": 67000, "stop": 65800},
        },
        last_output_refs={"ai_overview_path": "/output/btc_overview.json"},
    )

    # 第二轮 state
    state = {
        "session_id": "multi_turn_test",
        "messages": [HumanMessage(content="止损呢")],
        "iteration_count": 0,
        "current_symbol": "",
        "current_interval": "",
        "current_provider": "",
    }

    # restore_session_node 应恢复上下文
    restored = restore_session_node(state, session_mgr=mock_session_mgr)
    assert restored["current_symbol"] == "BTC_USDT"
    assert restored["last_snapshot"]["trend"] == "偏多"

    # 合并恢复的字段
    state.update(restored)

    # init_context_node 应注入 snapshot
    result = init_context_node(state)
    messages = result["messages"]

    # 检查系统注入消息
    system_injections = [
        m for m in messages
        if isinstance(m, SystemMessage) and "[系统注入]" in m.content
    ]
    assert len(system_injections) > 0
    injection_content = system_injections[0].content

    # 验证注入内容使用 snapshot_to_context_str()（可读文本），而非原始 JSON
    assert "BTC_USDT" in injection_content
    assert "偏多" in injection_content
    assert "67234.5" in injection_content
    # 不应该出现原始 JSON 格式的 "symbol" 键
    assert '"symbol"' not in injection_content


def test_no_history_followup():
    """无分析历史时追问，init_context_node 不应注入 snapshot。"""
    from core.nodes import init_context_node

    state = {
        "messages": [HumanMessage(content="止损呢")],
        "iteration_count": 0,
        # 无 last_snapshot
    }

    result = init_context_node(state)
    messages = result["messages"]

    # 不应有系统注入消息
    system_injections = [
        m for m in messages
        if isinstance(m, SystemMessage) and "[系统注入]" in m.content
    ]
    assert len(system_injections) == 0


def test_snapshot_to_context_str_used_not_raw_json():
    """build_context_message 应使用 snapshot_to_context_str 而非 raw JSON。"""
    from core.prompt import build_context_message

    snapshot = {
        "symbol": "BTC_USDT",
        "trend": "偏多",
        "last_price": 67234.5,
    }
    state = {
        "current_symbol": "BTC_USDT",
        "last_snapshot": snapshot,
    }
    msg = build_context_message(state)
    content = msg.content

    # 应包含可读文本
    assert "BTC_USDT" in content
    assert "偏多" in content

    # "上一轮分析摘要" 应出现在 content 中（非 "上一轮分析快照"）
    assert "上一轮分析摘要" in content