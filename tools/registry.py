"""MarketAssAgent — 工具注册表。"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool

from tools.market_data import make_market_data_tools
from tools.sim_account import make_sim_account_tools
from tools.research import make_research_tools


def make_tool_list(*, repo_root: Path) -> list[BaseTool]:
    """构建完整工具列表，注入 repo_root。"""
    tools = (
        make_market_data_tools(repo_root=repo_root)
        + make_sim_account_tools()
        + make_research_tools()
    )
    return tools