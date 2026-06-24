from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from core.asset_catalog import clear_asset_catalog_cache
from domain.market.analysis_service import _perform_market_analysis
from tools.market_data import fetch_market_data, resolve_market_symbol


def _write_market_config(path: Path) -> None:
    payload = {
        "default_symbols": ["NVDA"],
        "assets": [
            {
                "symbol": "NVDA",
                "name": "英伟达",
                "market": "US",
                "data_symbol": "NVDA",
                "research_keyword": "NVDA",
                "aliases": ["NVIDIA"],
                "tags": ["AI", "芯片"],
            },
            {
                "symbol": "000625.SZ",
                "name": "长安汽车",
                "market": "CN",
                "data_symbol": "000625.SZ",
                "research_keyword": "长安汽车",
                "aliases": [],
                "tags": ["汽车"],
            },
            {
                "symbol": "01810.HK",
                "name": "小米集团-W",
                "market": "HK",
                "data_symbol": "01810.HK",
                "research_keyword": "小米",
                "aliases": [],
                "tags": ["手机"],
            },
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sample_klines(count: int = 80) -> list[dict]:
    rows: list[dict] = []
    for idx in range(count):
        close = 100.0 + idx * 0.5
        rows.append(
            {
                "open": close - 0.2,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1000 + idx * 10,
            }
        )
    return rows


def test_resolve_market_symbol_hits_catalog_alias(tmp_path, monkeypatch):
    config_path = tmp_path / "market_config.json"
    _write_market_config(config_path)
    monkeypatch.setenv("MARKETASSAGENT_MARKET_CONFIG", str(config_path))
    clear_asset_catalog_cache()

    result = resolve_market_symbol.invoke({"text": "看看英伟达的股票", "interval": "1d"})

    assert result["status"] == "success"
    assert result["symbol"] == "NVDA"
    assert result["source"] == "catalog_alias"

    clear_asset_catalog_cache()


def test_fetch_market_data_uses_resolved_symbol(tmp_path, monkeypatch):
    config_path = tmp_path / "market_config.json"
    _write_market_config(config_path)
    monkeypatch.setenv("MARKETASSAGENT_MARKET_CONFIG", str(config_path))
    clear_asset_catalog_cache()

    calls: list[tuple[str, str, str]] = []

    def _fake_fetch(symbol: str, interval: str, limit: int = 200, market: str = "") -> dict:
        calls.append((symbol, interval, market))
        return {
            "symbol": symbol,
            "interval": interval,
            "market": market,
            "data": _sample_klines(30),
            "count": 30,
            "status": "success",
        }

    with patch("tools.market_data._fetch_stock_akshare_kline", side_effect=_fake_fetch):
        result = fetch_market_data.invoke({"symbol": "看看英伟达的股票", "interval": "1d"})

    assert result["status"] == "success"
    assert result["symbol"] == "NVDA"
    assert calls == [("NVDA", "1d", "us_equity")]

    clear_asset_catalog_cache()


def test_catalog_normalizes_common_symbol_variants(tmp_path, monkeypatch):
    from core.asset_catalog import get_asset_catalog

    config_path = tmp_path / "market_config.json"
    _write_market_config(config_path)
    monkeypatch.setenv("MARKETASSAGENT_MARKET_CONFIG", str(config_path))
    clear_asset_catalog_cache()

    catalog = get_asset_catalog()

    assert catalog.resolve_symbols_from_text("NVDA.US", min_score=80) == ["NVDA"]
    assert catalog.resolve_symbols_from_text("000625", min_score=80) == ["000625.SZ"]
    assert catalog.resolve_symbols_from_text("1810.HK", min_score=80) == ["01810.HK"]
    assert catalog.resolve_symbols_from_text("1810", min_score=80) == ["01810.HK"]

    clear_asset_catalog_cache()


def test_resolve_market_symbol_auto_registers_single_valid_discovery(tmp_path, monkeypatch):
    config_path = tmp_path / "market_config.json"
    _write_market_config(config_path)
    monkeypatch.setenv("MARKETASSAGENT_MARKET_CONFIG", str(config_path))
    clear_asset_catalog_cache()

    with patch(
        "tools.market_data.discover_asset_candidates",
        return_value=[
            {
                "symbol": "600600.SH",
                "name": "青岛啤酒",
                "market": "CN",
                "data_symbol": "600600.SH",
                "research_keyword": "青岛啤酒",
                "aliases": ["青啤"],
                "tags": [],
                "confidence": 0.91,
            }
        ],
    ), patch(
        "tools.market_data._fetch_stock_akshare_kline",
        return_value={
            "symbol": "600600.SH",
            "interval": "1d",
            "market": "a_share",
            "data": _sample_klines(30),
            "count": 30,
            "status": "success",
        },
    ):
        result = resolve_market_symbol.invoke({"text": "看看青岛啤酒的行情", "interval": "1d"})

    assert result["status"] == "success"
    assert result["symbol"] == "600600.SH"
    assert result["source"] == "discovery"
    assert result["auto_registered"] is True

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    symbols = [str(row.get("symbol") or "").strip().upper() for row in payload.get("assets", [])]
    assert "600600.SH" in symbols

    clear_asset_catalog_cache()


def test_resolve_market_symbol_blocks_auto_register_on_semantic_mismatch(tmp_path, monkeypatch):
    config_path = tmp_path / "market_config.json"
    _write_market_config(config_path)
    monkeypatch.setenv("MARKETASSAGENT_MARKET_CONFIG", str(config_path))
    clear_asset_catalog_cache()

    with patch(
        "tools.market_data.discover_asset_candidates",
        return_value=[
            {
                "symbol": "601127.SH",
                "name": "赛力斯",
                "market": "CN",
                "data_symbol": "601127.SH",
                "research_keyword": "赛力斯",
                "aliases": ["赛力斯汽车"],
                "tags": [],
                "confidence": 0.96,
            }
        ],
    ), patch(
        "tools.market_data._fetch_stock_akshare_kline",
        return_value={
            "symbol": "601127.SH",
            "interval": "1d",
            "market": "a_share",
            "data": _sample_klines(30),
            "count": 30,
            "status": "success",
        },
    ):
        result = resolve_market_symbol.invoke({"text": "华为公司 股票", "interval": "1d"})

    assert result["status"] == "clarify"
    assert result["candidates"] == [
        {"symbol": "601127.SH", "name": "赛力斯", "market": "CN", "confidence": 0.96}
    ]

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    symbols = [str(row.get("symbol") or "").strip().upper() for row in payload.get("assets", [])]
    assert "601127.SH" not in symbols

    clear_asset_catalog_cache()


def test_resolve_market_symbol_not_found_when_discovery_empty(tmp_path, monkeypatch):
    config_path = tmp_path / "market_config.json"
    _write_market_config(config_path)
    monkeypatch.setenv("MARKETASSAGENT_MARKET_CONFIG", str(config_path))
    clear_asset_catalog_cache()

    with patch("tools.market_data.discover_asset_candidates", return_value=[]):
        result = resolve_market_symbol.invoke({"text": "UNKNOWN_XYZ", "interval": "1d"})

    assert result["status"] == "not_found"
    assert result["source"] == "discovery"

    clear_asset_catalog_cache()


def test_resolve_market_symbol_returns_clarify_for_multiple_valid_candidates(tmp_path, monkeypatch):
    config_path = tmp_path / "market_config.json"
    _write_market_config(config_path)
    monkeypatch.setenv("MARKETASSAGENT_MARKET_CONFIG", str(config_path))
    clear_asset_catalog_cache()

    candidates = [
        {
            "symbol": "600600.SH",
            "name": "青岛啤酒股份",
            "market": "CN",
            "data_symbol": "600600.SH",
            "research_keyword": "青岛啤酒",
            "aliases": [],
            "tags": [],
            "confidence": 0.88,
        },
        {
            "symbol": "00168.HK",
            "name": "青岛啤酒股份",
            "market": "HK",
            "data_symbol": "00168.HK",
            "research_keyword": "青岛啤酒",
            "aliases": [],
            "tags": [],
            "confidence": 0.86,
        },
    ]

    def _fake_fetch(symbol: str, interval: str, limit: int = 200, market: str = "") -> dict:
        return {
            "symbol": symbol,
            "interval": interval,
            "market": market,
            "data": _sample_klines(30),
            "count": 30,
            "status": "success",
        }

    with patch("tools.market_data.discover_asset_candidates", return_value=candidates), patch(
        "tools.market_data._fetch_stock_akshare_kline",
        side_effect=_fake_fetch,
    ):
        result = resolve_market_symbol.invoke({"text": "青岛啤酒", "interval": "1d"})

    assert result["status"] == "clarify"
    assert len(result["candidates"]) == 2
    assert {row["symbol"] for row in result["candidates"]} == {"600600.SH", "00168.HK"}

    clear_asset_catalog_cache()


@patch("tools.market_data.fetch_market_data")
def test_perform_market_analysis_keeps_resolved_symbol(mock_fetch):
    mock_fetch.invoke.return_value = {
        "symbol": "NVDA",
        "requested_symbol": "英伟达",
        "resolution": {"status": "success", "symbol": "NVDA", "source": "catalog_alias"},
        "data": _sample_klines(),
    }

    result = _perform_market_analysis("英伟达", "1d")

    assert result["status"] == "success"
    assert result["symbol"] == "NVDA"
    assert result["analysis"]["symbol"] == "NVDA"
    assert result["analysis"]["requested_symbol"] == "英伟达"
