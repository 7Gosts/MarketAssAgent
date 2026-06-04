"""MarketAssAgent — 工具注册表。

提供统一的工具加载入口，供 LangGraph act_node 调用。
注意：部分工具依赖旧 app/ 模块，当前为占位实现。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool


def make_tool_list(*, repo_root: Path | None = None) -> list[BaseTool]:
    """构建完整工具列表（安全加载）。

    当前返回空列表作为占位，后续实现各 make_*_tools 后启用。
    """
    tools: list[BaseTool] = []
    # TODO: 恢复以下调用（需先修复 tools/market_data.py 等对 app/ 的依赖）
    # from tools.market_data import make_market_data_tools
    # from tools.sim_account import make_sim_account_tools
    # from tools.research import make_research_tools
    # tools = (
    #     make_market_data_tools(repo_root=repo_root or Path("."))
    #     + make_sim_account_tools()
    #     + make_research_tools()
    # )
    return tools


def get_tool_by_name(name: str) -> BaseTool | None:
    """按名称获取单个工具（占位）。"""
    for t in make_tool_list():
        if t.name == name:
            return t
    return None