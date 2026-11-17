from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import infrastructure.persistence.paper_trading_repository as repo_module
from domain.trading.paper_trading_service import PaperTradingService
from infrastructure.persistence.paper_trading_repository import PaperTradingRepository
from infrastructure.persistence.models import Base
from tools.sim_account import get_journal_status, simulate_open_position


def test_journal_status_can_filter_symbol_and_interval(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "journal_status.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(repo_module, "get_session", lambda: TestingSession())

    simulate_open_position(**
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
    simulate_open_position(**
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

    repo = PaperTradingRepository()
    btc_order = repo.list_active_orders(session_id="feishu_status", symbol="BTCUSDT", interval="1h")[0].order
    baseline_time = btc_order.created_at
    repo.close()

    service = PaperTradingService()
    service.track_active_orders(
        session_id="feishu_status",
        symbol="BTCUSDT",
        interval="1h",
        trade_fetcher=lambda _symbol, last_id=None: {
            "status": "success",
            "events": (
                [
                    {"trade_id": "101", "time": baseline_time.isoformat(), "price": 61900.0},
                    {"trade_id": "103", "time": (baseline_time + timedelta(seconds=1)).isoformat(), "price": 62000.0},
                ] if last_id is None else
                [{"trade_id": "103", "time": (baseline_time + timedelta(seconds=1)).isoformat(), "price": 62000.0}]
                if last_id == "101" else []
            ),
        },
    )
    service.close()

    btc = get_journal_status(**{"session_id": "feishu_status", "symbol": "BTC_USDT", "interval": "1h"})
    eth = get_journal_status(**{"session_id": "feishu_status", "symbol": "ETH_USDT", "interval": "4h"})

    assert btc["status"] == "success"
    assert "session_id" not in btc
    assert btc["total_open"] == 1
    assert btc["total_pending"] == 0
    assert btc["open_positions"][0]["symbol"] == "BTC_USDT"
    assert btc["open_positions"][0]["filled_at"]
    assert btc["open_positions"][0]["filled_price"] == 62000.0
    assert {event["symbol"] for event in btc["recent_events"]} == {"BTC_USDT"}
    assert all("event_id" not in event and "idea_id" not in event for event in btc["recent_events"])

    assert eth["status"] == "success"
    assert "session_id" not in eth
    assert eth["total_open"] == 0
    assert eth["total_pending"] == 1
    assert eth["pending_orders"][0]["symbol"] == "ETH_USDT"
    assert {event["symbol"] for event in eth["recent_events"]} == {"ETH_USDT"}
    assert all("event_id" not in event and "idea_id" not in event for event in eth["recent_events"])
