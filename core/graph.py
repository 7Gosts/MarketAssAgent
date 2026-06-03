"""MarketAssAgent — LangGraph 状态机构建。"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from core.agent import init_context_node, observe_node, reason_node, should_continue
from core.state import MarketAgentState
from core.supervisor import supervisor_node
from tools.registry import make_tool_list

logger = logging.getLogger(__name__)

_GRAPH_LOCK = threading.Lock()
_COMPILED_GRAPH: Any = None


def _build_llm():
    """构建 LLM 客户端，复用 config.runtime_config。"""
    from config.runtime_config import get_llm_runtime_settings
    from langchain_openai import ChatOpenAI

    settings = get_llm_runtime_settings()
    return ChatOpenAI(
        model=settings["model"],
        temperature=float(settings.get("temperature") or 0.2),
        api_key=settings["api_key"],
        base_url=settings["base_url"],
    )


def create_market_agent_graph(*, repo_root: Path) -> Any:
    """构建并编译 LangGraph ReAct Agent 图。"""

    # 构建工具
    tools = make_tool_list(repo_root=repo_root)
    tool_node = ToolNode(tools)

    # 构建带工具绑定的 LLM
    llm = _build_llm().bind_tools(tools)

    # 节点函数（需要 llm 闭包）
    def _reason(state: MarketAgentState) -> dict[str, Any]:
        return reason_node(state, llm=llm)

    # 构建图
    workflow = StateGraph(MarketAgentState)

    workflow.add_node("init_context", init_context_node)
    workflow.add_node("reason", _reason)
    workflow.add_node("tools", tool_node)
    workflow.add_node("observe", observe_node)
    workflow.add_node("supervisor", supervisor_node)

    # 边
    workflow.add_edge(START, "init_context")
    workflow.add_edge("init_context", "reason")

    # 条件边：reason → tools 或 supervisor
    workflow.add_conditional_edges(
        "reason",
        should_continue,
        {"tools": "tools", "supervisor": "supervisor"},
    )

    # tools → observe → reason（循环）
    workflow.add_edge("tools", "observe")
    workflow.add_edge("observe", "reason")

    # supervisor → END
    workflow.add_edge("supervisor", END)

    return workflow.compile()


def get_or_create_graph(*, repo_root: Path, force_refresh: bool = False) -> Any:
    """获取或创建编译后的图（线程安全单例）。"""
    global _COMPILED_GRAPH
    if not force_refresh and _COMPILED_GRAPH is not None:
        return _COMPILED_GRAPH
    with _GRAPH_LOCK:
        if not force_refresh and _COMPILED_GRAPH is not None:
            return _COMPILED_GRAPH
        _COMPILED_GRAPH = create_market_agent_graph(repo_root=repo_root)
        return _COMPILED_GRAPH