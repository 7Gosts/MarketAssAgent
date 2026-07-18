from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import infrastructure.persistence.paper_trading_repository as repo_module
from domain.trading.paper_trading_service import PaperTradingService
from infrastructure.persistence.models import Base
from tools.sim_account import get_journal_status, simulate_open_position


def test_journal_status_can_filter_symbol_and_interval(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "journal_status.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(repo_module, "get_session", lambda: TestingSession())

    simulate_open_position.invoke(
        {
            "session_id": "feishu_status",
            "symbol": "BTC_USDT",
            "direction": "long",
            "entry_price": 62000.0,
            "stop_loss": 60500.0,
            "take_profit": 65000.0,
            "interval": "1h",
            "request_id": "req_status_001",
        }
    )
    simulate_open_position.invoke(
        {
            "session_id": "feishu_status",
            "symbol": "ETH_USDT",
            "direction": "short",
            "entry_price": 3500.0,
            "stop_loss": 3600.0,
            "take_profit": 3300.0,
            "interval": "4h",
            "request_id": "req_status_002",
        }
    )

    service = PaperTradingService()
    service.reconcile_orders(
        session_id="feishu_status",
        symbol="BTCUSDT",
        interval="1h",
        allow_historical_bars=True,
        bars=[
            {
                "time": "2026-07-16T10:00:00Z",
                "open": 61800.0,
                "high": 62120.0,
                "low": 61750.0,
                "close": 62050.0,
                "volume": 10.0,
            }
        ],
    )
    service.close()

    btc = get_journal_status.invoke({"session_id": "feishu_status", "symbol": "BTC_USDT", "interval": "1h"})
    eth = get_journal_status.invoke({"session_id": "feishu_status", "symbol": "ETH_USDT", "interval": "4h"})

    assert btc["status"] == "success"
    assert btc["total_open"] == 1
    assert btc["total_pending"] == 0
    assert btc["open_positions"][0]["symbol"] == "BTC_USDT"

    assert eth["status"] == "success"
    assert eth["total_open"] == 0
    assert eth["total_pending"] == 1
    assert eth["pending_orders"][0]["symbol"] == "ETH_USDT"
