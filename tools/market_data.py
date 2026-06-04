from langchain_core.tools import tool
from typing import Dict, Any


@tool
def fetch_market_data(symbol: str, interval: str = "1d") -> Dict[str, Any]:
    """抽象数据源获取 K 线数据（后续可对接 tickflow / gateio 等）"""
    return {
        "symbol": symbol,
        "interval": interval,
        "status": "success",
        "message": f"已获取 {symbol} {interval} K 线数据（占位实现）"
    }
