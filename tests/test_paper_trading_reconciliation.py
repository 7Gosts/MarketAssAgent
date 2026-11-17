from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import infrastructure.persistence.paper_trading_repository as repo_module
from domain.trading.paper_trading_service import PaperTradingService
from infrastructure.persistence.paper_trading_repository import CreateTrackedOrderCommand, PaperTradingRepository
from infrastructure.persistence.models import Base


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def test_service_tracks_gate_trades_in_order_across_entry_and_take_profit(monkeypatch, tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ordered_price_events.sqlite3'}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(repo_module, "get_session", lambda: TestingSession())
    repo = PaperTradingRepository()
    created = repo.create_tracked_order(CreateTrackedOrderCommand(
        session_id="feishu_ordered_events",
        symbol="ETH_USDT",
        direction="short",
        entry_price=1826.0,
        stop_loss=1877.75,
        take_profit=1789.0,
        interval="4h",
        request_id="req_ordered_events",
        market="crypto",
    ))
    created.order.created_at = _utc("2026-11-16T20:00:00Z")
    order_id = created.order.order_id
    repo.session.commit()
    repo.close()

    def trade(trade_id: str, timestamp: str, price: float) -> dict[str, object]:
        return {"trade_id": trade_id, "time": timestamp, "price": price}

    baseline = trade("100", "2026-11-16T20:00:00Z", 1800.0)
    trades = [
        trade("101", "2026-11-16T20:28:00Z", 1788.51),
        trade("102", "2026-11-16T22:27:00Z", 1829.09),
        trade("103", "2026-11-16T23:00:00Z", 1788.0),
    ]
    service = PaperTradingService(PaperTradingRepository())

    def fetcher(_symbol: str, *, last_id: str | None = None):
        assert not service.repository.session.in_transaction()
        if last_id is None:
            return {"status": "success", "events": [trades[-1]]}
        if last_id == "100":
            return {"status": "success", "events": trades}
        raise AssertionError(f"unexpected cursor {last_id}")

    def older_fetcher(_symbol: str, *, last_id: str):
        assert last_id == "103"
        return {"status": "success", "events": [baseline]}

    result = service.track_active_orders(
        session_id="feishu_ordered_events",
        trade_fetcher=fetcher,
        older_trade_fetcher=older_fetcher,
    )
    bundle = service.repository.get_order_bundle(order_id=order_id)
    events = service.repository.session.query(repo_module.JournalEvent).filter_by(
        session_id="feishu_ordered_events"
    ).order_by(repo_module.JournalEvent.id).all()
    service.close()

    assert result["changed"] == 1, result["items"]
    assert bundle is not None
    assert bundle.order.status == "closed"
    assert bundle.order.filled_at.replace(tzinfo=timezone.utc) == _utc("2026-11-16T22:27:00Z")
    assert bundle.order.closed_at.replace(tzinfo=timezone.utc) == _utc("2026-11-16T23:00:00Z")
    assert [event.event_type for event in events] == [
        "order_created", "order_filled", "order_closed_tp",
    ]


def test_trade_page_cursor_and_order_events_roll_back_together(monkeypatch, tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'trade_page_transaction.sqlite3'}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(repo_module, "get_session", lambda: TestingSession())
    repo = PaperTradingRepository()
    created = repo.create_tracked_order(CreateTrackedOrderCommand(
        session_id="page_transaction",
        symbol="ETH_USDT",
        direction="short",
        entry_price=1826.0,
        stop_loss=1877.75,
        take_profit=1789.0,
        interval="4h",
        request_id="req_page_transaction",
        market="crypto",
    ))
    order_id = created.order.order_id
    created.order.simulation_rule_json = {"price_tracking": {"last_trade_id": "100"}}
    repo.session.commit()
    repo.close()

    start = datetime.now(timezone.utc).replace(microsecond=0)
    page = []
    for index in range(1000):
        page.append({
            "trade_id": str(101 + 2 * index),
            "time": (start + timedelta(seconds=index)).isoformat(),
            "price": 1800.0,
        })
    next_page = [
        {"trade_id": "3001", "time": (start + timedelta(seconds=1001)).isoformat(), "price": 1829.09},
        {"trade_id": "4007", "time": (start + timedelta(seconds=1002)).isoformat(), "price": 1788.0},
    ]
    service = PaperTradingService(PaperTradingRepository())

    def fetcher(_symbol: str, *, last_id: str | None = None):
        assert not service.repository.session.in_transaction()
        if last_id == "100":
            return {"status": "success", "events": page}
        if last_id == "2099":
            return {"status": "success", "events": next_page}
        raise AssertionError(f"unexpected cursor {last_id}")

    apply_transition = service.repository.apply_transition
    transition_count = 0

    def fail_on_close(transition, **kwargs):
        nonlocal transition_count
        transition_count += 1
        if transition_count == 2:
            raise RuntimeError("simulated page failure")
        return apply_transition(transition, **kwargs)

    monkeypatch.setattr(service.repository, "apply_transition", fail_on_close)
    first_page = service.track_active_orders(session_id="page_transaction", trade_fetcher=fetcher)
    after_first_page = service.repository.get_order_bundle(order_id=order_id)
    assert first_page["unchanged"] == 1
    assert after_first_page is not None
    assert after_first_page.order.simulation_rule_json["price_tracking"]["last_trade_id"] == "2099"

    failed = service.track_active_orders(session_id="page_transaction", trade_fetcher=fetcher)
    after_failure = service.repository.get_order_bundle(order_id=order_id)
    assert failed["failed"] == 1
    assert after_failure is not None
    assert after_failure.order.status == "pending_trigger"
    assert after_failure.order.simulation_rule_json["price_tracking"]["last_trade_id"] == "2099"
    assert service.repository.session.query(repo_module.JournalEvent).filter_by(order_id=order_id).count() == 1

    monkeypatch.setattr(service.repository, "apply_transition", apply_transition)
    recovered = service.track_active_orders(session_id="page_transaction", trade_fetcher=fetcher)
    final = service.repository.get_order_bundle(order_id=order_id)
    events = service.repository.session.query(repo_module.JournalEvent).filter_by(order_id=order_id).all()
    service.close()

    assert recovered["changed"] == 1
    assert final is not None
    assert final.order.status == "closed"
    assert final.order.simulation_rule_json["price_tracking"]["last_trade_id"] == "4007"
    assert {event.event_type for event in events} == {"order_created", "order_filled", "order_closed_tp"}


def test_active_order_query_requires_explicit_global_scope(monkeypatch, tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'active_order_scope.sqlite3'}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(repo_module, "get_session", lambda: TestingSession())
    repo = PaperTradingRepository()
    repo.create_tracked_order(CreateTrackedOrderCommand(
        session_id="session_scope",
        symbol="ETH_USDT",
        direction="long",
        entry_price=1800.0,
        stop_loss=1700.0,
        take_profit=1900.0,
        request_id="req_scope",
        market="crypto",
    ))

    assert repo.list_active_orders(session_id=None) == []
    assert len(repo.list_active_orders(session_id=None, all_sessions=True)) == 1
    repo.close()


def test_reconcile_scans_all_active_orders_without_batch_limit() -> None:
    captured: dict[str, object] = {}

    class RepositoryStub:
        def list_active_orders(self, *, session_id, symbol=None, interval=None):
            captured.update(session_id=session_id, symbol=symbol, interval=interval)
            return []

        def close(self):
            return None

    service = PaperTradingService(RepositoryStub())  # type: ignore[arg-type]
    result = service.reconcile_orders(session_id="session_all")

    assert result["status"] == "success"
    assert captured == {"session_id": "session_all", "symbol": None, "interval": None}
