from __future__ import annotations

from pathlib import Path
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import infrastructure.persistence.paper_trading_repository as repo_module
from domain.trading.paper_trading_service import PaperTradingService
from infrastructure.persistence.models import Base
from tools.sim_account import get_journal_status, simulate_open_position


def main() -> int:
    tmp_dir = Path(tempfile.mkdtemp(prefix="marketass_paper_trading_"))
    db_path = tmp_dir / "paper_trading_smoke.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    repo_module.get_session = lambda: TestingSession()

    created = simulate_open_position.invoke(
        {
            "session_id": "smoke_session",
            "symbol": "BTC_USDT",
            "direction": "long",
            "entry_price": 62000.0,
            "stop_loss": 60500.0,
            "take_profit": 65000.0,
            "interval": "1h",
            "request_id": "req_smoke_001",
            "source_snapshot_id": "snap_smoke_001",
        }
    )
    print("[create]", created)

    service = PaperTradingService()
    filled = service.reconcile_orders(
        session_id="smoke_session",
        symbol="BTC_USDT",
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
        request_id="req_smoke_fill",
    )
    print("[fill]", filled)

    closed = service.reconcile_orders(
        session_id="smoke_session",
        symbol="BTC_USDT",
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
        request_id="req_smoke_close",
    )
    print("[close]", closed)
    service.close()

    status = get_journal_status.invoke({"session_id": "smoke_session"})
    print("[status]", status)
    print(f"[db] sqlite:///{db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
