from .state import AgentState


def supervisor_node(state: AgentState):
    """最终输出守卫 - 确保格式规范"""
    messages = state["messages"]
    last_message = messages[-1].content if messages else ""
    
    # 这里可以加入更复杂的格式化、免责声明等逻辑
    return {
        "messages": messages,
        "next": "end"
    }
