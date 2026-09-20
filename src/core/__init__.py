"""MarketAssAgent 原生工具调用核心模块。"""

from .state import AgentState

__all__ = ["AgentState", "MarketReActAgent"]


def __getattr__(name: str):
    if name == "MarketReActAgent":
        from .agent import MarketReActAgent

        return MarketReActAgent
    raise AttributeError(f"module 'core' has no attribute {name!r}")
