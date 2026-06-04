from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_core.messages import HumanMessage
from .state import AgentState
from .prompt import get_prompt
from .supervisor import supervisor_node
from tools.registry import get_all_tools


def call_model(state: AgentState):
    """思考节点：让 LLM 决定下一步动作"""
    prompt = get_prompt()
    messages = state["messages"]
    
    # 构建输入
    chain = prompt | state.get("llm")  # 后续在 agent.py 中注入真实 LLM
    
    response = chain.invoke({"messages": messages})
    
    # 决定下一步
    if "Action" in response.content or any(tool.name in response.content for tool in get_all_tools()):
        next_step = "continue"
    else:
        next_step = "end"
    
    return {
        "messages": [response],
        "next": next_step
    }


def build_graph(llm):
    """构建完整的 LangGraph 工作流"""
    tools = get_all_tools()
    tool_node = ToolNode(tools)
    
    workflow = StateGraph(AgentState)
    
    workflow.add_node("reason", call_model)
    workflow.add_node("act", tool_node)
    workflow.add_node("supervisor", supervisor_node)
    
    workflow.set_entry_point("reason")
    
    # 条件路由
    workflow.add_conditional_edges(
        "reason",
        lambda state: state.get("next", "end"),
        {
            "continue": "act",
            "end": "supervisor"
        }
    )
    
    workflow.add_edge("act", "reason")      # 执行工具后回到思考
    workflow.add_edge("supervisor", END)
    
    return workflow.compile()
