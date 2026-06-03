"""MarketAssAgent — 禁止口径与合规数据（独立于 app/ 的纯数据模块）。"""

from __future__ import annotations

# 禁止出现在输出中的口径关键词
FORBIDDEN_CLAIMS: tuple[str, ...] = (
    "已成交",
    "成交回报",
    "主力资金净流入",
    "交易所逐笔资金流",
)
