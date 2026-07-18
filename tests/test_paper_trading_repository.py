from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import infrastructure.persistence.paper_trading_repository as repo_module
from infrastructure.persistence.models import Base, JournalEvent, JournalIdea, PaperOrder
from infrastructure.persistence.paper_trading_repository import CreateTrackedOrderCommand, PaperTradingRepository


def test_paper_trading_repository_create_and_idempotent(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "paper_trading_repo.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(repo_module, "get_session", lambda: TestingSession())

    repo = PaperTradingRepository()
    first = repo.create_tracked_order(
        CreateTrackedOrderCommand(
            session_id="feishu_pt_repo",
            symbol="ETH_USDT",
            direction="long",
            entry_price=2500.0,
            stop_loss=2430.0,
            take_profit=2660.0,
            interval="4h",
            request_id="req_same",
            source_snapshot_id="snap_001",
        )
    )
    second = repo.create_tracked_order(
        CreateTrackedOrderCommand(
            session_id="feishu_pt_repo",
            symbol="ETHUSDT",
            direction="long",
            entry_price=2500.0,
            stop_loss=2430.0,
            take_profit=2660.0,
            interval="4h",
            request_id="req_same",
            source_snapshot_id="snap_001",
        )
    )
    active = repo.list_active_orders(session_id="feishu_pt_repo", symbol="ETHUSDT", interval="4h")
    events = repo.list_recent_events(session_id="feishu_pt_repo", limit=10)
    repo.close()

    assert first.created is True
    assert second.created is False
    assert first.idea.idea_id == second.idea.idea_id
    assert first.order.order_id == second.order.order_id
    assert len(active) == 1
    assert len(events) == 1
    assert events[0].event_type == "order_created"

    check_session = TestingSession()
    try:
        assert check_session.query(JournalIdea).count() == 1
        assert check_session.query(PaperOrder).count() == 1
        assert check_session.query(JournalEvent).count() == 1
    finally:
        check_session.close()

