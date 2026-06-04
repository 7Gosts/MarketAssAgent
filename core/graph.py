"""LangGraph StateGraph 构建（ReAct 风格）。

节点设计：
- reason: LLM 思考下一步（是否需要工具）
- act: 执行工具调用
- observe: 将工具结果写回状态
- supervisor: 输出守卫（最终响应校验 + 免责声明）

条件边：should_continue 控制是否继续工具调用循环。
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from core.prompt import SYSTEM_PROMPT
from core.state import AgentState


def reason_node(state: AgentState) -> dict[str, Any]:
    """思考节点：根据当前状态决定下一步动作。

    目前为占位实现，后续接入 LLM + Tool-calling。
    """
    # TODO: 调用 LLM 判断是否需要工具
    # 临时策略：如果没有 last_snapshot，则需要调用工具
    if not state.get("last_snapshot"):
        return {"next": "act"}
    return {"next": "supervisor"}


def act_node(state: AgentState) -> dict[str, Any]:
    """工具执行节点。

    目前为占位，后续对接 tools/registry.py 中的工具。
    """
    # TODO: 解析 state 中的 tool_calls 并执行
    # 临时返回一个模拟快照
    symbol = state.get("current_symbol") or "UNKNOWN"
    interval = state.get("current_interval") or "1d"
    mock_snapshot = {
        "symbol": symbol,
        "interval": interval,
        "trend": "偏多",
        "key_levels": {"support": 100.0, "resistance": 120.0},
        "structure": "123法则成立",
        "confidence": 75,
        "timestamp": "2026-06-04T18:00:00",
    }
    return {"last_snapshot": mock_snapshot, "next": "observe"}


def observe_node(state: AgentState) -> dict[str, Any]:
    """观察节点：将工具结果合并进状态。"""
    # 目前 act_node 已直接写入 last_snapshot
    # 此节点可用于进一步处理或日志
    return {"next": "reason"}


def supervisor_node(state: AgentState) -> dict[str, Any]:
    """输出守卫节点：最终响应前校验 + 附加免责声明。"""
    recommendation = state.get("recommendation") or {}
    disclaimer = "仅供技术分析与程序化演示，不构成投资建议。"
    recommendation.setdefault("disclaimer", disclaimer)
    return {"recommendation": recommendation, "next": END}


def should_continue(state: AgentState) -> Literal["act", "supervisor", END]:
    """条件边：根据 next 字段决定流转。"""
    nxt = state.get("next")
    if nxt == "act":
        return "act"
    if nxt == "supervisor":
        return "supervisor"
    if nxt == END:
        return END
    # 默认回到 reason 继续思考
    return "reason"


def build_graph() -> StateGraph:
    """构建并返回编译后的 StateGraph。"""
    graph = StateGraph(AgentState)

    # 注册节点
    graph.add_node("reason", reason_node)
    graph.add_node("act", act_node)
    graph.add_node("observe", observe_node)
    graph.add_node("supervisor", supervisor_node)

    # 边
    graph.add_edge(START, "reason")
    graph.add_conditional_edges(
        "reason",
        should_continue,
        {
            "act": "act",
            "supervisor": "supervisor",
            END: END,
        },
    )
    graph.add_edge("act", "observe")
    graph.add_edge("observe", "reason")
    graph.add_edge("supervisor", END)

    return graph.compile()


# 全局编译图（单例模式，生产环境可按需重新编译）
compiled_graph = build_graph()
