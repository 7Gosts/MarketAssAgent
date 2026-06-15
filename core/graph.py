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
    tools_by_name = {getattr(t, "name", ""): t for t in tools}
    prompt = get_prompt()

    def call_model(state: AgentState) -> dict[str, Any]:
        """思考节点：让 LLM 决定下一步动作（支持真正的 Tool Calling）"""
        messages = state["messages"]
        requested = state.get("allowed_tools") or []
        allowed = [t for t in requested if t in tools_by_name]
        active_tools = [tools_by_name[name] for name in allowed] if allowed else tools
        llm_with_tools = llm.bind_tools(active_tools) if active_tools else llm
        chain = prompt | llm_with_tools
        response = chain.invoke({"messages": messages})

        # 强约束：即使模型返回了越权工具调用，也在图层过滤。
        allowed_names = {getattr(t, "name", "") for t in active_tools}
        raw_tool_calls = list(getattr(response, "tool_calls", None) or [])
        filtered_tool_calls = [
            tc for tc in raw_tool_calls
            if str(tc.get("name", "")) in allowed_names
        ]

        # 真正的 Tool Calling 判断
        has_tool_calls = bool(filtered_tool_calls)

        # 确保返回的是 AIMessage
        if not isinstance(response, AIMessage):
            response = AIMessage(
                content=getattr(response, "content", str(response)),
                tool_calls=filtered_tool_calls
            )
        else:
            response = AIMessage(content=response.content, tool_calls=filtered_tool_calls)

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
