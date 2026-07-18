from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import infrastructure.persistence.paper_trading_repository as repo_module
from domain.trading.paper_trading_service import PaperTradingService
from infrastructure.persistence.paper_trading_repository import CreateTrackedOrderCommand, PaperTradingRepository
from infrastructure.persistence.models import Base


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_reconcile_pending_to_filled_then_closed(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "paper_trading_reconcile.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(repo_module, "get_session", lambda: TestingSession())

    repo = PaperTradingRepository()
    created = repo.create_tracked_order(
        CreateTrackedOrderCommand(
            session_id="feishu_reconcile",
            symbol="BTCUSDT",
            direction="long",
            entry_price=62000.0,
            stop_loss=60500.0,
            take_profit=65000.0,
            interval="1h",
            request_id="req_reconcile_001",
        )
    )
    repo.close()

    service = PaperTradingService(PaperTradingRepository())
    filled = service.reconcile_orders(
        session_id="feishu_reconcile",
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
        request_id="req_reconcile_fill",
    )
    closed = service.reconcile_orders(
        session_id="feishu_reconcile",
        symbol="BTCUSDT",
        interval="1h",
        allow_historical_bars=True,
        bars=[
            {
                "time": "2026-07-16T11:00:00Z",
                "open": 62100.0,
                "high": 65100.0,
                "low": 62000.0,
                "close": 64950.0,
                "volume": 11.0,
            }
        ],
        request_id="req_reconcile_close",
    )
    final_status = service.repository.list_recent_orders(session_id="feishu_reconcile", limit=10)
    events = service.repository.list_recent_events(session_id="feishu_reconcile", limit=10)
    service.close()

    assert created.order.status == "pending_trigger"

    assert filled["status"] == "success"
    assert filled["changed"] == 1
    assert filled["items"][0]["event_type"] == "order_filled"
    assert filled["items"][0]["order_status"] == "filled"

    assert closed["status"] == "success"
    assert closed["changed"] == 1
    assert closed["items"][0]["event_type"] == "order_closed_tp"
    assert closed["items"][0]["order_status"] == "closed"

    assert len(final_status) == 1
    assert final_status[0].idea.state == "closed"
    assert final_status[0].order.status == "closed"
    assert final_status[0].order.closed_at.isoformat().startswith("2026-07-16T11:00:00")
    assert len(events) >= 3
    assert {event.event_type for event in events[:3]} == {"order_closed_tp", "order_filled", "order_created"}
