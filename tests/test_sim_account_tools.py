from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.asset_catalog import clear_asset_catalog_cache
from core.memory_api import create_default_memory_api
import infrastructure.persistence.paper_trading_repository as repo_module
from infrastructure.persistence.models import Base
from tools.context_memory import set_context_memory_api
from tools.sim_account import get_journal_status, prepare_simulated_order, simulate_open_position


def _write_crypto_market_config(path: Path) -> None:
    path.write_text(
        """
{
  "default_symbols": [],
  "assets": [
    {
      "symbol": "ETH_USDT",
      "name": "Ethereum",
      "market": "CRYPTO",
      "data_symbol": "ETH_USDT",
      "research_keyword": "以太坊",
      "tags": ["加密货币", "ETH"]
    }
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_sim_account_tools_write_and_read_formal_tables(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "sim_account_tools.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(repo_module, "get_session", lambda: TestingSession())

    created = simulate_open_position.invoke(
        {
            "session_id": "feishu_sim_tool",
            "symbol": "BTC_USDT",
            "direction": "long",
            "entry_price": 62000.0,
            "stop_loss": 60500.0,
            "take_profit": 65000.0,
            "interval": "1h",
            "request_id": "req_tool_001",
            "source_snapshot_id": "snap_tool_001",
        }
    )
    status = get_journal_status.invoke({"session_id": "feishu_sim_tool"})

    assert created["status"] == "success"
    assert created["created"] is True
    assert created["order_status"] == "pending_trigger"
    assert created["idea_id"].startswith("idea_")
    assert created["order_id"].startswith("ord_")

    assert status["status"] == "success"
    assert status["total_pending"] == 1
    assert status["total_open"] == 0
    assert status["total_records"] == 1
    assert len(status["pending_orders"]) == 1
    assert status["pending_orders"][0]["symbol"] == "BTC_USDT"
    assert status["pending_orders"][0]["order_status"] == "pending_trigger"
    assert len(status["recent_events"]) == 1
    assert status["recent_events"][0]["event_type"] == "order_created"


def test_prepare_simulated_order_returns_confirm_required_for_natural_language_asset(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "market_config.json"
    _write_crypto_market_config(config_path)
    monkeypatch.setenv("MARKETASSAGENT_MARKET_CONFIG", str(config_path))
    clear_asset_catalog_cache()

    prepared = prepare_simulated_order.invoke(
        {
            "asset_text": "以太坊",
            "direction": "开多",
            "entry_price": 1786.0,
            "stop_loss": 1754.0,
            "take_profit": 1854.0,
            "interval": "1h",
        }
    )

    assert prepared["status"] == "confirm_required"
    assert prepared["symbol"] == "ETH_USDT"
    assert prepared["asset_text"] == "以太坊"
    assert prepared["candidates"][0]["symbol"] == "ETH_USDT"

    clear_asset_catalog_cache()


def test_prepare_simulated_order_allows_explicit_formal_symbol(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "market_config.json"
    _write_crypto_market_config(config_path)
    monkeypatch.setenv("MARKETASSAGENT_MARKET_CONFIG", str(config_path))
    clear_asset_catalog_cache()

    prepared = prepare_simulated_order.invoke(
        {
            "asset_text": "ETH_USDT",
            "direction": "开多",
            "entry_price": 1786.0,
            "stop_loss": 1754.0,
            "take_profit": 1854.0,
            "interval": "1h",
        }
    )

    assert prepared["status"] == "ready"
    assert prepared["symbol"] == "ETH_USDT"
    assert prepared["direction"] == "long"
    assert prepared["simulate_args"]["symbol"] == "ETH_USDT"
    assert prepared["simulate_args"]["entry_price"] == 1786.0

    clear_asset_catalog_cache()


def test_prepare_simulated_order_can_offer_recent_context_candidate(tmp_path):
    api = create_default_memory_api(repo_root=tmp_path, backend="json")
    set_context_memory_api(api)
    api.checkpoint(
        "feishu_ctx_order",
        "last_snapshot",
        {
            "symbol": "AU9999",
            "interval": "1h",
            "timestamp": "2026-07-16T10:00:00Z",
            "trend": "震荡",
        },
    )

    prepared = prepare_simulated_order.invoke(
        {
            "asset_text": "就按刚才那个",
            "session_id": "feishu_ctx_order",
            "direction": "long",
            "entry_price": 810.0,
            "stop_loss": 798.0,
            "take_profit": 836.0,
            "interval": "1h",
        }
    )

    assert prepared["status"] == "confirm_required"
    assert prepared["candidates"][0]["symbol"] == "AU9999"
    assert prepared["candidates"][0]["source"] == "last_snapshot"

    set_context_memory_api(None)


def test_simulate_open_position_blocks_natural_language_asset_before_write(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "market_config.json"
    _write_crypto_market_config(config_path)
    monkeypatch.setenv("MARKETASSAGENT_MARKET_CONFIG", str(config_path))
    clear_asset_catalog_cache()

    db_path = tmp_path / "sim_account_resolve_before_write.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(repo_module, "get_session", lambda: TestingSession())

    created = simulate_open_position.invoke(
        {
            "session_id": "feishu_direct_order",
            "symbol": "以太坊",
            "direction": "开多",
            "entry_price": 1786.0,
            "stop_loss": 1754.0,
            "take_profit": 1854.0,
            "interval": "1h",
            "request_id": "req_direct_eth",
        }
    )
    status = get_journal_status.invoke({"session_id": "feishu_direct_order", "symbol": "ETH_USDT"})

    assert created["status"] == "confirm_required"
    assert created["created"] is False
    assert created["symbol"] == "ETH_USDT"
    assert status["total_pending"] == 0

    clear_asset_catalog_cache()
