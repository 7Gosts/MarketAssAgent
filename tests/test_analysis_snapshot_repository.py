from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

import infrastructure.persistence.analysis_snapshot_repository as repo_module
from infrastructure.persistence.analysis_snapshot_repository import AnalysisSnapshotRepository
from infrastructure.persistence.models import Base


def test_analysis_snapshot_repository_create_and_query(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "analysis_snapshot_repo.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    columns = {column["name"] for column in inspect(engine).get_columns("analysis_snapshots")}
    assert "ma_regime" in columns
    assert "trend" not in columns
    assert "stance" not in columns

    monkeypatch.setattr(repo_module, "get_session", lambda: TestingSession())

    repo = AnalysisSnapshotRepository()
    row = repo.create(
        session_id="feishu_test_snapshot_repo",
        request_id="req_001",
        snapshot_payload={
            "schema_version": "analysis_snapshot.v2",
            "symbol": "ETH_USDT",
            "interval": "4h",
            "timestamp": "2026-07-13T10:00:00",
            "price": 1778.0,
            "ma_regime": "mixed",
            "support": [1770.0],
            "resistance": [1795.3],
        },
        raw_snapshot={
            "symbol": "ETH_USDT",
            "interval": "4h",
            "timestamp": "2026-07-13T10:00:00",
            "current_price": 1778.0,
            "ma_regime": "mixed",
            "support_levels": [1770.0],
            "resistance_levels": [1795.3],
        },
    )
    repo.close()

    assert row.snapshot_id.startswith("snap_")
    assert row.symbol_key == "ETHUSDT"
    assert row.current_price == 1778.0
    assert row.ma_regime == "mixed"
    assert row.support_json == [1770.0]
    assert row.resistance_json == [1795.3]

    repo = AnalysisSnapshotRepository()
    previous = repo.get_previous_by_context(
        session_id="feishu_test_snapshot_repo",
        symbol="ETHUSDT",
        interval="4h",
    )
    compact = repo.to_compact_payload(previous) if previous is not None else {}
    repo.close()

    assert previous is not None
    assert compact == {
        "schema_version": "analysis_snapshot.v2",
        "symbol": "ETH_USDT",
        "interval": "4h",
        "timestamp": "2026-07-13T10:00:00",
        "price": 1778.0,
        "ma_regime": "mixed",
        "support": [1770.0],
        "resistance": [1795.3],
    }


def test_analysis_snapshot_repository_excludes_same_request(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "analysis_snapshot_repo_exclude.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(repo_module, "get_session", lambda: TestingSession())

    repo = AnalysisSnapshotRepository()
    repo.create(
        session_id="feishu_test_snapshot_repo_exclude",
        request_id="req_old",
        snapshot_payload={
            "schema_version": "analysis_snapshot.v2",
            "symbol": "ETH_USDT",
            "interval": "4h",
            "timestamp": "2026-07-13T10:00:00",
            "price": 1778.0,
            "ma_regime": "mixed",
        },
    )
    repo.create(
        session_id="feishu_test_snapshot_repo_exclude",
        request_id="req_new",
        snapshot_payload={
            "schema_version": "analysis_snapshot.v2",
            "symbol": "ETHUSDT",
            "interval": "4h",
            "timestamp": "2026-07-13T12:00:00",
            "price": 1795.3,
            "ma_regime": "bullish",
        },
    )

    previous = repo.get_previous_by_context(
        session_id="feishu_test_snapshot_repo_exclude",
        symbol="ETH_USDT",
        interval="4h",
        exclude_request_id="req_new",
    )
    compact = repo.to_compact_payload(previous) if previous is not None else {}
    repo.close()

    assert previous is not None
    assert compact["price"] == 1778.0
    assert compact["ma_regime"] == "mixed"


def test_analysis_snapshot_repository_create_if_missing_is_idempotent(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "analysis_snapshot_repo_idempotent.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(repo_module, "get_session", lambda: TestingSession())

    repo = AnalysisSnapshotRepository()
    row1, created1 = repo.create_if_missing(
        session_id="feishu_test_snapshot_repo_idempotent",
        request_id="req_same",
        snapshot_payload={
            "schema_version": "analysis_snapshot.v2",
            "symbol": "ETH_USDT",
            "interval": "4h",
            "timestamp": "2026-07-13T10:00:00",
            "price": 1778.0,
            "ma_regime": "mixed",
        },
    )
    row2, created2 = repo.create_if_missing(
        session_id="feishu_test_snapshot_repo_idempotent",
        request_id="req_same",
        snapshot_payload={
            "schema_version": "analysis_snapshot.v2",
            "symbol": "ETHUSDT",
            "interval": "4h",
            "timestamp": "2026-07-13T10:00:00",
            "price": 1778.0,
            "ma_regime": "mixed",
        },
    )
    repo.close()

    assert created1 is True
    assert created2 is False
    assert row1.id == row2.id
