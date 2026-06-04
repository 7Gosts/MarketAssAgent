"""MarketReActAgent 主入口类。

封装 LangGraph 的编译与运行，提供简洁的 invoke 接口。
"""

from __future__ import annotations

from typing import Any

from core.graph import compiled_graph
from core.state import AgentState


class MarketReActAgent:
    """MarketAssAgent 的核心 ReAct Agent。

    用法示例：
        agent = MarketReActAgent()
        result = agent.invoke("BTC_USDT 4h 行情分析", session_id="user_123")
    """

    def __init__(self) -> None:
        self.graph = compiled_graph

    def invoke(self, user_input: str, session_id: str = "default") -> dict[str, Any]:
        """运行一次完整的 ReAct 流程。

        Args:
            user_input: 用户自然语言输入
            session_id: 会话标识

        Returns:
            最终的 AgentState（包含 recommendation 等）
        """
        initial_state: AgentState = {
            "messages": [{"role": "user", "content": user_input}],
            "session_id": session_id,
            "current_symbol": None,
            "current_interval": None,
            "last_snapshot": None,
            "analysis_result": None,
            "risk_assessment": None,
            "recommendation": None,
            "intent": None,
            "next": None,
            "metadata": {},
            "error": None,
        }

        # LangGraph invoke 返回最终状态
        final_state = self.graph.invoke(initial_state)
        return final_state


# 便捷单例（可选）
agent = MarketReActAgent()
