"""MarketAssAgent — 研报检索工具。"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from tools.legacy_bridge import search_research


def make_research_tools() -> list:
    """创建研报检索相关工具。"""

    @tool
    def search_research_tool(
        keyword: str,
        symbol: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """搜索机构研报、板块叙事、催化/风险等信息。

        当用户问到机构观点、板块主题、概念/催化/风险时使用。

        Example:
            search_research_tool(keyword="半导体", symbol="NVDA")
            search_research_tool(keyword="AI芯片")
        """
        return search_research(
            keyword=keyword,
            symbol=symbol,
            limit=limit,
        )

    return [search_research_tool]