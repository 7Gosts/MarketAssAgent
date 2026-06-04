from __future__ import annotations

from typing import Any, Callable
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_core.messages import AIMessage
from .state import AgentState
from .prompt import get_prompt
from .supervisor import supervisor_node
from tools.registry import get_all_tools


def make_call_model(llm: Any) -> Callable[[AgentState], dict[str, Any]]:
    """Factory that returns a call_model bound to a specific LLM instance."""
    prompt = get_prompt()

    def call_model(state: AgentState) -> dict[str, Any]:
        """思考节点：让 LLM 决定下一步动作"""
        messages = state["messages"]
        chain = prompt | llm
        response = chain.invoke({"messages": messages})

        # 简单判断是否需要继续调用工具
        tool_names = [t.name for t in get_all_tools()]
        needs_tool = any(name in (response.content or "") for name in tool_names)

        # 确保返回的是 AIMessage
        if not isinstance(response, AIMessage):
            response = AIMessage(content=getattr(response, "content", str(response)))

        return {
            "messages": [response],
            "next": "continue" if needs_tool else "end"
        }

    return call_model


def build_graph(llm: Any):
    """构建完整的 LangGraph 工作流，llm 必须在构建时注入"""
    tools = get_all_tools()
    tool_node = ToolNode(tools)
    call_model = make_call_model(llm)

    workflow = StateGraph(AgentState)

    workflow.add_node("reason", call_model)
    workflow.add_node("act", tool_node)
    workflow.add_node("supervisor", supervisor_node)

    workflow.set_entry_point("reason")

    workflow.add_conditional_edges(
        "reason",
        lambda state: state.get("next", "end"),
        {
            "continue": "act",
            "end": "supervisor"
        }
    )

    workflow.add_edge("act", "reason")
    workflow.add_edge("supervisor", END)

    return workflow.compile()
