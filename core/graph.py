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
    """Factory that returns a call_model bound to a specific LLM instance with tool calling."""
    tools = get_all_tools()
    llm_with_tools = llm.bind_tools(tools) if tools else llm
    prompt = get_prompt()

    def call_model(state: AgentState) -> dict[str, Any]:
        """思考节点：让 LLM 决定下一步动作（支持真正的 Tool Calling）"""
        messages = state["messages"]
        chain = prompt | llm_with_tools
        response = chain.invoke({"messages": messages})

        # 真正的 Tool Calling 判断
        has_tool_calls = bool(getattr(response, "tool_calls", None))

        # 确保返回的是 AIMessage
        if not isinstance(response, AIMessage):
            response = AIMessage(
                content=getattr(response, "content", str(response)),
                tool_calls=getattr(response, "tool_calls", None)
            )

        return {
            "messages": [response],
            "next": "continue" if has_tool_calls else "end"
        }

    return call_model


def build_graph(llm: Any):
    """构建完整的 LangGraph 工作流，支持真正的 Tool Calling"""
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
