"""飞书机器人：从 market_config.json 构建可交易标的索引（symbol → provider / 研报关键词）。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


_ASCII_ALIAS_PAT = re.compile(r"^[A-Z0-9_]+$")


def _normalize_alias(value: str) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    return re.sub(r"[^A-Z0-9\u4e00-\u9fff]+", "", raw)


def _extract_name_fragments(value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    parts = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", raw)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        norm = _normalize_alias(part)
        if len(norm) < 2 or norm in seen:
            continue
        seen.add(norm)
        out.append(part)
    return out


def _digit_alias_variants(keyword: str, raw_values: list[str]) -> list[str]:
    kw = str(keyword or "").strip()
    if not kw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for digits in re.findall(r"\d+", str(raw or "")):
            variants = [digits]
            if len(digits) >= 4 and len(set(digits)) == 1:
                variants.append(digits[:-1])
            for variant in variants:
                alias = f"{kw}{variant}"
                norm = _normalize_alias(alias)
                if len(norm) < 3 or norm in seen:
                    continue
                seen.add(norm)
                out.append(alias)
    return out


def _ascii_tokens(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[A-Z0-9_]+", str(text or "").upper()) if tok}


def _market_to_provider(market: str) -> str:
    m = (market or "").strip().upper()
    if m == "CRYPTO":
        return "gateio"
    if m == "PM":
        return "goldapi"
    return "tickflow"


@dataclass(frozen=True)
class FeishuAssetCatalog:
    """by_symbol 的 key 为 symbol.upper()。"""

    by_symbol: dict[str, dict[str, Any]]
    alias_entries: tuple[tuple[str, str, int], ...]
    config_path: Path

    def get(self, symbol_upper: str) -> dict[str, Any] | None:
        return self.by_symbol.get(symbol_upper.strip().upper())

    def provider_for(self, symbol_upper: str) -> str | None:
        row = self.get(symbol_upper)
        if not row:
            return None
        return str(row.get("provider") or "").strip().lower() or None

    def research_keyword_for(self, symbol_upper: str) -> str | None:
        row = self.get(symbol_upper)
        if not row:
            return None
        kw = row.get("research_keyword")
        if isinstance(kw, str) and kw.strip():
            return kw.strip()
        name = row.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None

    def name_for(self, symbol_upper: str) -> str | None:
        row = self.get(symbol_upper)
        if not row:
            return None
        name = row.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None

    def resolve_symbols_from_text(self, text: str, *, min_score: int = 80) -> list[str]:
        normalized_text = _normalize_alias(text)
        if not normalized_text:
            return []

        direct = self.get(normalized_text)
        if direct:
            return [str(direct.get("symbol") or "").strip().upper()]

        token_set = _ascii_tokens(text)
        scores: dict[str, int] = {}
        for alias_norm, symbol, base_score in self.alias_entries:
            if not alias_norm or not symbol:
                continue

            matched = False
            bonus = 0
            if _ASCII_ALIAS_PAT.fullmatch(alias_norm):
                if alias_norm == normalized_text or alias_norm in token_set:
                    matched = True
                    bonus = 25
                elif len(alias_norm) >= 4 and alias_norm in normalized_text:
                    matched = True
                    bonus = 10
            else:
                if alias_norm == normalized_text:
                    matched = True
                    bonus = 25
                elif alias_norm in normalized_text:
                    matched = True
                    bonus = min(15, max(1, len(alias_norm)))

            if not matched:
                continue
            scores[symbol] = max(scores.get(symbol, 0), int(base_score) + int(bonus))

        ranked = [
            (symbol, score)
            for symbol, score in scores.items()
            if score >= int(min_score)
        ]
        ranked.sort(key=lambda item: (-item[1], item[0]))
        return [symbol for symbol, _ in ranked]

    def tradable_assets_for_prompt(self) -> list[dict[str, Any]]:
        """供路由 LLM 的精简列表（稳定排序）。"""
        rows = list(self.by_symbol.values())
        rows.sort(key=lambda x: str(x.get("symbol") or ""))
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "symbol": r.get("symbol"),
                    "market": r.get("market"),
                    "provider": r.get("provider"),
                    "name": r.get("name"),
                    "research_keyword": r.get("research_keyword"),
                    "tags": list(r.get("tags") or []),
                    "aliases": list(r.get("aliases") or [])[:6],
                }
            )
        return out

    @property
    def allowed_symbols(self) -> frozenset[str]:
        return frozenset(self.by_symbol.keys())


def _load_market_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_feishu_asset_catalog(*, config_path: Path | None = None) -> FeishuAssetCatalog:
    root = Path(__file__).resolve().parents[1]
    path = (config_path or (root / "config" / "market_config.json")).resolve()
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
        for a in assets:
            if not isinstance(a, dict):
                continue
            sym = str(a.get("symbol") or "").strip().upper()
            if not sym:
                continue
            market = str(a.get("market") or "").strip().upper() or "US"
            provider = _market_to_provider(market)
            name = str(a.get("name") or sym).strip() or sym
            research_keyword = (
                str(a.get("research_keyword")).strip()
                if isinstance(a.get("research_keyword"), str) and str(a.get("research_keyword")).strip()
                else None
            )
            tags = [str(t).strip() for t in (a.get("tags") or []) if str(t).strip()]
            aliases_raw = [str(it).strip() for it in (a.get("aliases") or []) if str(it).strip()]

            generated_aliases: list[str] = []
            generated_aliases.extend(_extract_name_fragments(name))
            if research_keyword:
                generated_aliases.extend(_extract_name_fragments(research_keyword))
            generated_aliases.extend(
                _digit_alias_variants(research_keyword or "", [sym, a.get("data_symbol") or "", name])
            )

            merged_aliases: list[str] = []
            alias_seen: set[str] = set()
            for alias in [sym, a.get("data_symbol") or "", name, research_keyword or "", *aliases_raw, *generated_aliases, *tags]:
                norm = _normalize_alias(str(alias or ""))
                if len(norm) < 2 or norm in alias_seen:
                    continue
                alias_seen.add(norm)
                merged_aliases.append(str(alias).strip())

            by_symbol[sym] = {
                "symbol": sym,
                "market": market,
                "provider": provider,
                "name": name,
                "research_keyword": research_keyword,
                "tags": tags,
                "aliases": merged_aliases,
            }

            _append_alias(sym, sym, 120)
            _append_alias(sym, str(a.get("data_symbol") or ""), 115)
            _append_alias(sym, name, 105)
            if research_keyword:
                _append_alias(sym, research_keyword, 95)
            for alias in aliases_raw:
                _append_alias(sym, alias, 100)
            for alias in generated_aliases:
                _append_alias(sym, alias, 90)
            for tag in tags:
                _append_alias(sym, tag, 70)

    alias_entries.sort(key=lambda item: (-item[2], -len(item[0]), item[1], item[0]))
    return FeishuAssetCatalog(by_symbol=by_symbol, alias_entries=tuple(alias_entries), config_path=path)


@lru_cache(maxsize=4)
def get_feishu_asset_catalog_cached(config_path_str: str) -> FeishuAssetCatalog:
    return load_feishu_asset_catalog(config_path=Path(config_path_str))


def clear_asset_catalog_cache() -> None:
    get_feishu_asset_catalog_cached.cache_clear()


def get_catalog_for_repo(repo_root: Path | None = None) -> FeishuAssetCatalog:
    root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
    return get_feishu_asset_catalog_cached(str(root / "config" / "market_config.json"))


# 裸代码 → 完整交易对（仅当 catalog 中存在该对时落地）
_BARE_CRYPTO: dict[str, str] = {
    "BTC": "BTC_USDT",
    "ETH": "ETH_USDT",
    "SOL": "SOL_USDT",
}


def canonical_tradable_symbol(raw: str, catalog: FeishuAssetCatalog) -> str | None:
    v = (raw or "").strip().upper()
    if not v:
        return None
    if v in catalog.by_symbol:
        return v
    mapped = _BARE_CRYPTO.get(v)
    if mapped and mapped in catalog.by_symbol:
        return mapped
    resolved = catalog.resolve_symbols_from_text(v, min_score=85)
    if len(resolved) == 1:
        return resolved[0]
    return None


def canonical_tradable_symbol_list(values: Any, catalog: FeishuAssetCatalog) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for it in values:
        c = canonical_tradable_symbol(str(it or ""), catalog)
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def normalize_provider(value: str | None, *, symbol_upper: str, catalog: FeishuAssetCatalog) -> str:
    """校验 LLM 给出的 provider；非法或与标的表不一致时以 catalog 为准。"""
    expected = catalog.provider_for(symbol_upper) or "tickflow"
    p = str(value or "").strip().lower()
    if p not in {"tickflow", "gateio", "goldapi"}:
        return expected
    if p != expected:
        return expected
    return p
