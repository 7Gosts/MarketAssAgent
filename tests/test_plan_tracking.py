from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import tools.plan_tracking as tracking
from core.conversation_scope import ConversationScope
from infrastructure.persistence.models import Base, JournalEvent, JournalIdea, PaperOrder


def _database(monkeypatch, tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'plan_tracking.sqlite3'}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(tracking, "get_session", lambda: TestingSession())
    return TestingSession


def test_start_plan_monitor_is_idempotent_and_does_not_create_an_order(monkeypatch, tmp_path: Path):
    TestingSession = _database(monkeypatch, tmp_path)
    scope = ConversationScope(
        transport="feishu",
        tenant_id="tenant_1",
        visibility_scope="private",
        visibility_scope_id="ou_user_1",
        actor_id="ou_user_1",
    )
    kwargs = {
        "session_id": "sess_1",
        "request_id": "req_1",
        "scope": scope,
        "symbol": "ETH_USDT",
        "direction": "short",
        "entry_zone_low": 2267,
        "entry_zone_high": 2282,
    }

    first = tracking.start_plan_monitor(**kwargs)
    second = tracking.start_plan_monitor(**kwargs)
    session = TestingSession()
    try:
        assert first["plan_id"] == second["plan_id"]
        assert session.query(JournalIdea).count() == 1
        assert session.query(PaperOrder).count() == 0
        assert session.query(JournalEvent).filter_by(event_type="plan_monitor_created").count() == 1
        assert first["entry_zone"] == [2267, 2282]
        assert first["interval"] == "15m"
        idea = session.query(JournalIdea).one()
        assert idea.valid_until - idea.created_at == timedelta(hours=8)
    finally:
        session.close()


def test_new_plan_can_use_72_hours_and_4h_without_replacing_existing_plan(monkeypatch, tmp_path: Path):
    TestingSession = _database(monkeypatch, tmp_path)
    scope = ConversationScope(
        transport="feishu",
        tenant_id="tenant_1",
        visibility_scope="private",
        visibility_scope_id="ou_user_1",
        actor_id="ou_user_1",
    )
    first = tracking.start_plan_monitor(
        session_id="sess_1",
        request_id="req_1",
        scope=scope,
        symbol="ETH_USDT",
        direction="short",
        entry_zone_low=2267,
        entry_zone_high=2282,
    )
    second = tracking.start_plan_monitor(
        session_id="sess_1",
        request_id="req_2",
        scope=scope,
        symbol="ETH_USDT",
        direction="short",
        entry_zone_low=2274,
        entry_zone_high=2283,
        interval="4h",
        valid_hours=72,
    )

    assert first["status"] == "active"
    assert second["status"] == "active"
    assert second["interval"] == "4h"
    session = TestingSession()
    try:
        ideas = session.query(JournalIdea).order_by(JournalIdea.created_at, JournalIdea.id).all()
        assert len(ideas) == 2
        assert [idea.interval for idea in ideas] == ["15m", "4h"]
        assert ideas[1].valid_until - ideas[1].created_at == timedelta(hours=72)
    finally:
        session.close()

    rejected = tracking.start_plan_monitor(
        session_id="sess_1",
        request_id="req_3",
        scope=scope,
        symbol="ETH_USDT",
        direction="short",
        entry_zone_low=2274,
        entry_zone_high=2283,
        interval="30m",
        valid_hours=73,
    )
    assert rejected["status"] == "error"


def test_4h_monitor_waits_for_closed_candle_and_fetches_selected_interval(monkeypatch, tmp_path: Path):
    TestingSession = _database(monkeypatch, tmp_path)
    created = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)
    session = TestingSession()
    session.add(JournalIdea(
        idea_id="plan_4h",
        session_id="sess_1",
        source_request_id="req_4h",
        symbol="ETH_USDT",
        symbol_key="ETH_USDT",
        interval="4h",
        side="short",
        setup_type="plan_monitor",
        state="watch",
        entry_zone_low=2274,
        entry_zone_high=2283,
        valid_until=created + timedelta(hours=72),
        created_at=created,
        updated_at=created,
        meta_json={"plan_monitor": {
            "status": "active",
            "zone_touched": False,
            "last_entry_bar": "",
            "destination": {"receive_id": "ou_user_1", "receive_id_type": "open_id"},
        }},
    ))
    session.add(JournalIdea(
        idea_id="plan_15m",
        session_id="sess_1",
        source_request_id="req_15m",
        symbol="ETH_USDT",
        symbol_key="ETH_USDT",
        interval="15m",
        side="short",
        setup_type="plan_monitor",
        state="watch",
        entry_zone_low=2274,
        entry_zone_high=2283,
        valid_until=created + timedelta(hours=8),
        created_at=created,
        updated_at=created,
        meta_json={"plan_monitor": {
            "status": "active",
            "zone_touched": False,
            "last_entry_bar": "",
            "destination": {"receive_id": "ou_user_1", "receive_id_type": "open_id"},
        }},
    ))
    session.commit()
    session.close()

    payload = {"status": "success", "market": "crypto", "data": [
        {"time": "2026-11-03T20:00:00+00:00", "high": 2265, "low": 2258, "close": 2260},
        {"time": "2026-11-04T00:00:00+00:00", "high": 2278, "low": 2260, "close": 2270},
    ]}
    fetched_intervals = []
    sent = []

    def fetcher(*, symbol, interval):
        fetched_intervals.append(interval)
        return payload if interval == "4h" else {"status": "error", "error": "unused 15m fixture"}

    async def notify(plan, candle):
        sent.append((plan["interval"], candle["close"]))

    before_close = asyncio.run(tracking.run_plan_monitor_cycle(
        now=datetime(2026, 11, 4, 3, 59, tzinfo=timezone.utc),
        market_fetcher=fetcher,
        notifier=notify,
    ))
    at_close = asyncio.run(tracking.run_plan_monitor_cycle(
        now=datetime(2026, 11, 4, 4, 0, tzinfo=timezone.utc),
        market_fetcher=fetcher,
        notifier=notify,
    ))

    assert before_close["triggered"] == 0
    assert before_close["failed"] == 1
    assert at_close["triggered"] == 1
    assert at_close["failed"] == 1
    assert fetched_intervals.count("4h") == 2
    assert fetched_intervals.count("15m") == 2
    assert sent == [("4h", 2270)]

    event = tracking.build_plan_monitor_event(
        {
            "plan_id": "plan_4h",
            "symbol": "ETH_USDT",
            "direction": "short",
            "zone_low": 2274,
            "zone_high": 2283,
            "interval": "4h",
        },
        {"time": "2026-11-04T00:00:00+00:00", "high": 2278, "low": 2260, "close": 2270},
    )
    assert "4h K线" in event
    assert "2026-11-04 12:00 CST" in event


def test_plan_monitor_cancellation_is_scoped_to_its_session(monkeypatch, tmp_path: Path):
    _database(monkeypatch, tmp_path)
    scope = ConversationScope(
        transport="feishu",
        tenant_id="tenant_1",
        visibility_scope="private",
        visibility_scope_id="ou_user_1",
        actor_id="ou_user_1",
    )
    plan = tracking.start_plan_monitor(
        session_id="sess_1",
        request_id="req_1",
        scope=scope,
        symbol="ETH_USDT",
        direction="short",
        entry_zone_low=2267,
        entry_zone_high=2282,
        valid_hours=8,
    )

    assert tracking.cancel_plan_monitor(session_id="other_session", plan_id=plan["plan_id"])["status"] == "not_found"
    assert tracking.cancel_plan_monitor(session_id="sess_1", plan_id=plan["plan_id"])["status"] == "cancelled"
    status = tracking.get_plan_monitors(session_id="sess_1")
    assert status["items"][0]["status"] == "cancelled"


def test_monitor_notifies_once_after_zone_touch_and_rejection(monkeypatch, tmp_path: Path):
    TestingSession = _database(monkeypatch, tmp_path)
    created = datetime(2026, 11, 4, 0, 1, tzinfo=timezone.utc)
    session = TestingSession()
    session.add(JournalIdea(
        idea_id="plan_1",
        session_id="sess_1",
        source_request_id="req_1",
        symbol="ETH_USDT",
        symbol_key="ETH_USDT",
        interval="15m",
        side="short",
        setup_type="plan_monitor",
        state="watch",
        entry_zone_low=2267,
        entry_zone_high=2282,
        valid_until=created + timedelta(hours=8),
        created_at=created,
        updated_at=created,
        meta_json={"plan_monitor": {
            "status": "active",
            "zone_touched": False,
            "last_entry_bar": "",
            "destination": {"receive_id": "ou_user_1", "receive_id_type": "open_id"},
        }},
    ))
    session.commit()
    session.close()

    payload = {"status": "success", "market": "crypto", "data": [
        {"time": "2026-11-04T00:00:00+00:00", "high": 2265, "low": 2258, "close": 2260},
        {"time": "2026-11-04T00:15:00+00:00", "high": 2274, "low": 2260, "close": 2270},
        {"time": "2026-11-04T00:30:00+00:00", "high": 2266, "low": 2260, "close": 2264},
    ]}
    sent = []
    attempts = 0
    attempt_numbers = []

    async def notify(plan, candle):
        nonlocal attempts
        attempts += 1
        attempt_numbers.append(plan["monitor"]["notification_attempt"])
        if attempts == 1:
            raise RuntimeError("temporary delivery failure")
        sent.append((plan["plan_id"], candle["close"]))

    first = asyncio.run(tracking.run_plan_monitor_cycle(
        now=datetime(2026, 11, 4, 0, 30, tzinfo=timezone.utc),
        market_fetcher=lambda **_kwargs: payload,
        notifier=notify,
    ))
    second = asyncio.run(tracking.run_plan_monitor_cycle(
        now=datetime(2026, 11, 4, 0, 45, tzinfo=timezone.utc),
        market_fetcher=lambda **_kwargs: payload,
        notifier=notify,
    ))
    third = asyncio.run(tracking.run_plan_monitor_cycle(
        now=datetime(2026, 11, 4, 0, 46, tzinfo=timezone.utc),
        market_fetcher=lambda **_kwargs: payload,
        notifier=notify,
    ))
    fourth = asyncio.run(tracking.run_plan_monitor_cycle(
        now=datetime(2026, 11, 4, 0, 47, tzinfo=timezone.utc),
        market_fetcher=lambda **_kwargs: payload,
        notifier=notify,
    ))
    session = TestingSession()
    try:
        idea = session.query(JournalIdea).filter_by(idea_id="plan_1").one()
        assert first["triggered"] == 0
        assert second["failed"] == 1
        assert third["triggered"] == 1
        assert fourth["checked"] == 0
        assert attempts == 2
        assert attempt_numbers == [1, 2]
        assert sent == [("plan_1", 2264.0)]
        assert idea.meta_json["plan_monitor"]["status"] == "triggered"
        assert session.query(JournalEvent).filter_by(event_type="plan_monitor_triggered").count() == 1
    finally:
        session.close()


def test_zone_reclaim_needs_a_zone_touch():
    result = tracking.evaluate_zone_reclaim(
        direction="short",
        zone_low=2267,
        zone_high=2282,
        already_touched=False,
        previous_close=2260,
        candles=[{"high": 2266, "low": 2258, "close": 2261}],
    )

    assert result["triggered"] is False
    assert result["zone_touched"] is False
