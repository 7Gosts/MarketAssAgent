from .state import AgentState


def supervisor_node(state: AgentState):
    """最终输出守卫 - 生成 recommendation 并添加免责声明"""
    messages = state["messages"]
    last_content = ""
    if messages:
        last = messages[-1]
        last_content = getattr(last, "content", str(last))

    disclaimer = "仅供技术分析与程序化演示，不构成投资建议。"

    recommendation = {
        "text": last_content,
        "disclaimer": disclaimer,
        "timestamp": __import__("datetime").datetime.now().isoformat()
    }

    return {
        "messages": messages,
        "recommendation": recommendation,
        "next": "end"
    }
