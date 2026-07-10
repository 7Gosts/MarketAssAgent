from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG_PATH = _REPO_ROOT / "runtime" / "config" / "market_config.json"
_ASCII_ALIAS_PAT = re.compile(r"^[A-Z0-9._-]+$")


def get_market_config_path() -> Path:
    override = os.getenv("MARKETASSAGENT_MARKET_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_CONFIG_PATH


def _normalize_alias(value: str) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    return re.sub(r"[^A-Z0-9\u4e00-\u9fff]+", "", raw)


def _symbol_alias_variants(*, symbol: str, market: str, data_symbol: str) -> list[str]:
    raw_symbol = str(symbol or "").strip().upper()
    raw_data_symbol = str(data_symbol or "").strip().upper()
    values = [raw_symbol, raw_data_symbol]
    out: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        item = str(value or "").strip()
        norm = _normalize_alias(item)
        if len(norm) < 2 or norm in seen:
            return
        seen.add(norm)
        out.append(item)

    for value in values:
        _add(value)

    if market == "CN":
        for value in values:
            base = value
            if "." in base:
                code, suffix = base.split(".", 1)
                _add(code)
                _add(f"{suffix}{code}")
                _add(f"{suffix.lower()}{code}")
            elif len(base) == 8 and base[:2] in {"SH", "SZ", "BJ"} and base[2:].isdigit():
                _add(base[2:])
                _add(base.lower())

    elif market == "US":
        for value in values:
            if value.endswith(".US"):
                _add(value[:-3])
            else:
                _add(f"{value}.US")

    elif market == "HK":
        for value in values:
            base = value
            if base.endswith(".HK"):
                digits = base[:-3]
            else:
                digits = base
            if digits.isdigit():
                padded = digits.zfill(5)
                trimmed = str(int(digits))
                _add(padded)
                _add(trimmed)
                _add(f"{padded}.HK")
                _add(f"{trimmed}.HK")
                _add(f"HK{padded}")
                _add(f"HK{trimmed}")
                _add(f"hk{padded}")
                _add(f"hk{trimmed}")

    elif market == "CRYPTO":
        for value in values:
            base = value.replace("-", "_").replace("/", "_")
            _add(base)
            _add(base.replace("_", ""))
            _add(base.replace("_", "-"))
            _add(base.replace("_", "/"))

    return out


def _ascii_tokens(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[A-Z0-9._-]+", str(text or "").upper()) if tok}


def _chinese_chunks(text: str) -> set[str]:
    return {
        chunk for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", str(text or ""))
        if chunk
    }


def _load_market_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"default_symbols": [], "assets": []}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"default_symbols": [], "assets": []}
    return obj if isinstance(obj, dict) else {"default_symbols": [], "assets": []}


@dataclass(frozen=True)
class AssetCatalog:
    by_symbol: dict[str, dict[str, Any]]
    alias_entries: tuple[tuple[str, str, int], ...]
    config_path: Path

    def get(self, symbol_upper: str) -> dict[str, Any] | None:
        return self.by_symbol.get(str(symbol_upper or "").strip().upper())

    def resolve_symbols_from_text(self, text: str, *, min_score: int = 80) -> list[str]:
        normalized_text = _normalize_alias(text)
        if not normalized_text:
            return []

        direct = self.get(normalized_text)
        if direct:
            return [str(direct.get("symbol") or "").strip().upper()]

        token_set = _ascii_tokens(text)
        chinese_chunks = {_normalize_alias(chunk) for chunk in _chinese_chunks(text)}
        scores: dict[str, tuple[int, int]] = {}
        for alias_norm, symbol, base_score in self.alias_entries:
            if not alias_norm or not symbol:
                continue
            matched = False
            if alias_norm == normalized_text:
                matched = True
            elif _ASCII_ALIAS_PAT.fullmatch(alias_norm):
                matched = alias_norm in token_set
            else:
                matched = alias_norm in chinese_chunks or alias_norm in normalized_text

            if matched:
                prev = scores.get(symbol)
                candidate_rank = (int(base_score), len(alias_norm))
                if prev is None or candidate_rank > prev:
                    scores[symbol] = candidate_rank

        ranked = [
            (symbol, rank)
            for symbol, rank in scores.items()
            if rank[0] >= int(min_score)
        ]
        ranked.sort(key=lambda item: (-item[1][0], -item[1][1], item[0]))
        return [symbol for symbol, _ in ranked]

    def tradable_assets_for_prompt(self) -> list[dict[str, Any]]:
        rows = list(self.by_symbol.values())
        rows.sort(key=lambda row: str(row.get("symbol") or ""))
        return [
            {
                "symbol": row.get("symbol"),
                "market": row.get("market"),
                "name": row.get("name"),
                "research_keyword": row.get("research_keyword"),
                "aliases": list(row.get("aliases") or [])[:6],
                "tags": list(row.get("tags") or [])[:6],
            }
            for row in rows
        ]


def _build_catalog(path: Path) -> AssetCatalog:
    obj = _load_market_config(path)
    assets = obj.get("assets")
    by_symbol: dict[str, dict[str, Any]] = {}
    alias_entries: list[tuple[str, str, int]] = []

    def _append_alias(symbol: str, raw_value: str, score: int) -> None:
        norm = _normalize_alias(raw_value)
        if len(norm) < 2:
            return
        alias_entries.append((norm, symbol, int(score)))

    if isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            symbol = str(asset.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            market = str(asset.get("market") or "US").strip().upper() or "US"
            name = str(asset.get("name") or symbol).strip() or symbol
            research_keyword = str(asset.get("research_keyword") or name).strip() or name
            tags = [str(tag).strip() for tag in (asset.get("tags") or []) if str(tag).strip()]
            aliases_raw = [str(alias).strip() for alias in (asset.get("aliases") or []) if str(alias).strip()]

            generated_aliases = _symbol_alias_variants(
                symbol=symbol,
                market=market,
                data_symbol=str(asset.get("data_symbol") or symbol),
            )

            merged_aliases: list[str] = []
            alias_seen: set[str] = set()
            for alias in [
                symbol,
                str(asset.get("data_symbol") or ""),
                name,
                research_keyword,
                *aliases_raw,
                *generated_aliases,
            ]:
                norm = _normalize_alias(alias)
                if len(norm) < 2 or norm in alias_seen:
                    continue
                alias_seen.add(norm)
                merged_aliases.append(str(alias).strip())

            by_symbol[symbol] = {
                "symbol": symbol,
                "market": market,
                "name": name,
                "data_symbol": str(asset.get("data_symbol") or symbol).strip() or symbol,
                "research_keyword": research_keyword,
                "aliases": merged_aliases,
                "tags": tags,
            }

            _append_alias(symbol, symbol, 120)
            _append_alias(symbol, str(asset.get("data_symbol") or ""), 115)
            _append_alias(symbol, name, 105)
            _append_alias(symbol, research_keyword, 95)
            for alias in aliases_raw:
                _append_alias(symbol, alias, 100)
            for alias in generated_aliases:
                _append_alias(symbol, alias, 90)

    alias_entries.sort(key=lambda item: (-item[2], -len(item[0]), item[1], item[0]))
    return AssetCatalog(by_symbol=by_symbol, alias_entries=tuple(alias_entries), config_path=path)


@lru_cache(maxsize=4)
def get_asset_catalog_cached(path_str: str) -> AssetCatalog:
    return _build_catalog(Path(path_str))


def get_asset_catalog() -> AssetCatalog:
    return get_asset_catalog_cached(str(get_market_config_path()))


def clear_asset_catalog_cache() -> None:
    get_asset_catalog_cached.cache_clear()


def register_discovered_asset(candidate: dict[str, Any]) -> dict[str, Any]:
    path = get_market_config_path()
    payload = _load_market_config(path)
    assets = payload.get("assets")
    if not isinstance(assets, list):
        assets = []
        payload["assets"] = assets

    symbol = str(candidate.get("symbol") or "").strip().upper()
    if not symbol:
        return {"registered": False, "reason": "missing_symbol"}

    existing = next(
        (row for row in assets if str(row.get("symbol") or "").strip().upper() == symbol),
        None,
    )
    if existing:
        return {"registered": False, "reason": "already_exists", "symbol": symbol}

    market = str(candidate.get("market") or "US").strip().upper() or "US"
    name = str(candidate.get("name") or symbol).strip() or symbol
    data_symbol = str(candidate.get("data_symbol") or symbol).strip() or symbol
    research_keyword = str(candidate.get("research_keyword") or name).strip() or name
    aliases = [str(alias).strip() for alias in (candidate.get("aliases") or []) if str(alias).strip()]
    tags = [str(tag).strip() for tag in (candidate.get("tags") or []) if str(tag).strip()]

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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    clear_asset_catalog_cache()
    return {"registered": True, "symbol": symbol, "market": market}
