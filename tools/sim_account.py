"""MarketAssAgent — 模拟账户工具。"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool


def make_sim_account_tools() -> list:
    """创建模拟账户相关工具。"""

    @tool
    def view_sim_account(
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
            view_sim_account(scope="overview")
            view_sim_account(scope="positions", symbol="BTC_USDT")
        """
        from app.capabilities.sim_account_capability import view_sim_account_state

        result = view_sim_account_state(
            scope=scope,
            account_id=account_id,
            symbol=symbol,
            limit=limit,
        )
        return result.to_dict()

    return [view_sim_account]