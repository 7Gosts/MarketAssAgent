from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event as sqlalchemy_event
from sqlalchemy.orm import sessionmaker

import infrastructure.persistence.paper_trading_repository as repo_module
from infrastructure.persistence.models import Base, JournalEvent, JournalIdea, PaperOrder
from infrastructure.persistence.paper_trading_repository import CreateTrackedOrderCommand, PaperTradingRepository


def test_paper_trading_repository_create_and_idempotent(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "paper_trading_repo.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    statements = []
    sqlalchemy_event.listen(
        engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _params, _context, _many: statements.append(statement),
    )
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
    repo.create_tracked_order(
        CreateTrackedOrderCommand(
            session_id="feishu_pt_repo",
            symbol="BTC_USDT",
            direction="short",
            entry_price=62000.0,
            stop_loss=63000.0,
            take_profit=60000.0,
            interval="1h",
            request_id="req_newer_btc",
        )
    )
    repo.session.add(JournalEvent(
        idea_id=first.idea.idea_id,
        order_id=first.order.order_id,
        session_id="feishu_pt_repo",
        event_type="order_filled",
        new_order_status="filled",
        event_time=first.order.created_at,
        event_price=2500.0,
    ))
    repo.session.add(JournalEvent(
        idea_id=first.idea.idea_id,
        order_id=first.order.order_id,
        session_id="feishu_pt_repo",
        event_type="order_filled",
        new_order_status="filled",
        event_time=first.order.created_at,
        event_price=2500.0,
    ))
    repo.session.commit()
    active = repo.list_active_orders(session_id="feishu_pt_repo", symbol="ETHUSDT", interval="4h")
    recent_orders = repo.list_recent_orders(
        session_id="feishu_pt_repo", limit=1, symbol="ETHUSDT", interval="4h",
    )
    latest_event = repo.list_recent_events(
        session_id="feishu_pt_repo", limit=1, symbol="ETHUSDT", interval="4h",
    )
    all_eth_events = repo.list_recent_events(
        session_id="feishu_pt_repo", limit=10, symbol="ETHUSDT", interval="4h",
    )
    repo.close()

    assert first.created is True
    assert second.created is False
    assert first.idea.idea_id == second.idea.idea_id
    assert first.order.order_id == second.order.order_id
    assert len(active) == 1
    assert len(recent_orders) == 1
    assert recent_orders[0].order_id == first.order.order_id
    assert not hasattr(recent_orders[0], "idea_id")
    assert len(latest_event) == 1
    assert latest_event[0].event_type == "order_filled"
    assert len(all_eth_events) == 3
    assert {event.event_type for event in all_eth_events} == {"order_created", "order_filled"}
    assert all(event.event_time == first.order.created_at for event in all_eth_events)
    assert latest_event[0].symbol == "ETH_USDT"
    assert latest_event[0].order_id == first.order.order_id
    assert not hasattr(latest_event[0], "event_id")
    assert not hasattr(latest_event[0], "idea_id")
    event_query = next(
        statement for statement in statements
        if "FROM journal_events" in statement and "JOIN paper_orders" in statement
    )
    order_query = next(
        statement for statement in statements
        if "paper_orders.order_id AS order_id" in statement
        and "FROM journal_ideas JOIN paper_orders" in statement
    )
    order_columns = order_query.split("FROM", 1)[0]
    assert "journal_events.event_id" not in event_query
    assert "journal_events.payload_json" not in event_query
    assert "paper_orders.order_id" in order_columns
    assert "journal_ideas.idea_id" not in order_columns
    assert "meta_json" not in order_columns
    assert "paper_orders.symbol_key =" in event_query
    assert "paper_orders.interval =" in event_query

    check_session = TestingSession()
    try:
        assert check_session.query(JournalIdea).count() == 2
        assert check_session.query(PaperOrder).count() == 2
        assert check_session.query(JournalEvent).count() == 4
    finally:
        check_session.close()

