from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.feishu_asset_catalog import clear_asset_catalog_cache, get_catalog_for_repo


def _market_to_provider(market: str) -> str:
    m = str(market or "").strip().upper()
    if m == "CRYPTO":
        return "gateio"
    if m in {"PM", "COMMODITY"}:
        return "goldapi"
    return "tickflow"


def _load_market_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"default_symbols": [], "assets": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"default_symbols": [], "assets": []}


def register_discovered_asset(*, repo_root: Path, candidate: dict[str, Any]) -> dict[str, Any]:
    config_path = repo_root / "config" / "market_config.json"
    payload = _load_market_config(config_path)
    assets = payload.get("assets")
    if not isinstance(assets, list):
        assets = []
        payload["assets"] = assets

    symbol = str(candidate.get("symbol") or "").strip().upper()
    if not symbol:
        return {"registered": False, "reason": "missing_symbol"}

    existing = next((row for row in assets if str(row.get("symbol") or "").strip().upper() == symbol), None)
    if existing:
        return {"registered": False, "reason": "already_exists", "symbol": symbol}

    market = str(candidate.get("market") or "US").strip().upper() or "US"
    name = str(candidate.get("name") or symbol).strip() or symbol
    data_symbol = str(candidate.get("data_symbol") or symbol).strip() or symbol
    research_keyword = str(candidate.get("research_keyword") or name).strip() or name
    aliases = [str(x).strip() for x in (candidate.get("aliases") or []) if str(x).strip()]
    tags = [str(x).strip() for x in (candidate.get("tags") or []) if str(x).strip()]

    assets.append(
        {
            "symbol": symbol,
            "name": name,
            "market": market,
            "data_symbol": data_symbol,
            "research_keyword": research_keyword,
            "aliases": aliases,
            "tags": tags,
        }
    )

    payload.setdefault("default_symbols", [])
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    clear_asset_catalog_cache()
    catalog = get_catalog_for_repo(repo_root)
    return {
        "registered": True,
        "symbol": symbol,
        "market": market,
        "provider": _market_to_provider(market),
        "catalog_size": len(catalog.by_symbol),
    }
