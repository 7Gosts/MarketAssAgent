"""MarketAssAgent Core - LangGraph ReAct Agent 核心模块"""

from .state import AgentState, AnalysisSnapshot

__all__ = ["AgentState", "AnalysisSnapshot", "MarketReActAgent"]


def __getattr__(name: str):
    if name == "MarketReActAgent":
        from .agent import MarketReActAgent

        return MarketReActAgent
    raise AttributeError(f"module 'core' has no attribute {name!r}")
