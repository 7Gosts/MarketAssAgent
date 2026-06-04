from langchain_core.tools import tool
from typing import Dict, Any


@tool
def simulate_open_position(symbol: str, direction: str, entry_price: float,
                          stop_loss: float, take_profit: float) -> Dict[str, Any]:
    """模拟开仓并记录到台账"""
    return {
        "status": "success",
        "journal_id": "JNL_20260604_001",
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "message": f"已模拟{direction} {symbol} @ {entry_price}"
    }


@tool
def get_journal_status(session_id: str = "default") -> Dict[str, Any]:
    """查询当前模拟持仓与台账状态"""
    return {
        "session_id": session_id,
        "open_positions": [],
        "total_pnl": 0.0,
        "message": "当前无持仓记录"
    }
