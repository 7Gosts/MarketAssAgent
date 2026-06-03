"""MarketAssAgent — 模拟账户工具。"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from tools.legacy_bridge import view_sim_account


def make_sim_account_tools() -> list:
    """创建模拟账户相关工具。"""

    @tool
    def view_sim_account_tool(
        scope: str = "overview",
        account_id: str | None = None,
        symbol: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """查看纸交易模拟账户状态。

        scope 可选值：
        - overview: 余额 + 持仓 + 活动台账 + 对账统计
        - positions: 当前未平仓持仓
        - active_ideas: watch/pending/filled 的活动交易想法
        - orders: 最近委托
        - fills: 最近成交
        - health: order/fill 对账统计

        Example:
            view_sim_account_tool(scope="overview")
            view_sim_account_tool(scope="positions", symbol="BTC_USDT")
        """
        return view_sim_account(
            scope=scope,
            account_id=account_id,
            symbol=symbol,
            limit=limit,
        )

    return [view_sim_account_tool]