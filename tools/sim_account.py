"""模拟交易工具 — 对接真实 PostgreSQL 台账

工具列表:
- simulate_open_position: 模拟开仓并记录到 journals 表
- get_journal_status: 查询当前持仓与台账状态
"""

from __future__ import annotations

from typing import Any, Dict

from langchain_core.tools import tool


@tool
def simulate_open_position(
    symbol: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    session_id: str = "default",
) -> Dict[str, Any]:
    """模拟开仓并记录到台账（真正写入 journals 表）

    Args:
        symbol: 标的代码
        direction: 方向 (long/short)
        entry_price: 入场价格
        stop_loss: 止损价格
        take_profit: 止盈价格
        session_id: 会话标识

    Returns:
        包含 journal_id 和确认信息的字典
    """
    try:
        from persistence.journal_repository import JournalRepository

        repo = JournalRepository()
        journal = repo.create(
            session_id=session_id,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status="open",
            notes=f"模拟{direction} {symbol} @ {entry_price}",
        )
        return {
            "status": "success",
            "journal_id": journal.id,
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "message": f"已模拟{direction} {symbol} @ {entry_price}，Journal ID: {journal.id}",
        }
    except Exception as e:
        # 即使数据库不可用也不让工具崩溃，返回 error dict
        return {
            "status": "error",
            "journal_id": None,
            "symbol": symbol,
            "direction": direction,
            "message": f"模拟开仓失败（数据库不可用）: {e}",
        }


@tool
def get_journal_status(session_id: str = "default") -> Dict[str, Any]:
    """查询当前模拟持仓与台账状态（从 PostgreSQL 读取）

    Args:
        session_id: 会话标识

    Returns:
        当前持仓列表和统计信息
    """
    try:
        from persistence.journal_repository import JournalRepository

        repo = JournalRepository()
        journals = repo.list_by_session(session_id, limit=50)
        open_positions = [j for j in journals if j.status == "open"]

        positions_detail = []
        for j in open_positions:
            positions_detail.append({
                "id": j.id,
                "symbol": j.symbol,
                "direction": j.direction,
                "entry_price": j.entry_price,
                "stop_loss": j.stop_loss,
                "take_profit": j.take_profit,
                "notes": j.notes[:100] if j.notes else None,
                "created_at": j.created_at.isoformat() if j.created_at else None,
            })

        return {
            "session_id": session_id,
            "open_positions": positions_detail,
            "total_open": len(open_positions),
            "total_records": len(journals),
            "message": f"当前有 {len(open_positions)} 条持仓记录，共 {len(journals)} 条台账记录",
        }
    except Exception as e:
        return {
            "session_id": session_id,
            "open_positions": [],
            "total_open": 0,
            "total_records": 0,
            "message": f"查询台账失败（数据库不可用）: {e}",
        }