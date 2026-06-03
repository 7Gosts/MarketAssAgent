from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_provider_for_symbol(
    *,
    repo_root: Path | None,
    symbol: str,
    provider_hint: str | None = None,
) -> str:
    """按 market_config / catalog 将 symbol 映射到 tickflow | gateio | goldapi。"""
    sym = str(symbol or "").strip().upper()
    hint = str(provider_hint or "").strip().lower()
    if not sym:
        return hint or "tickflow"
    try:
        from app.feishu_asset_catalog import get_catalog_for_repo, normalize_provider

        root = repo_root.resolve() if repo_root else None
        catalog = get_catalog_for_repo(root)
        return normalize_provider(hint or None, symbol_upper=sym, catalog=catalog)
    except Exception:
        return hint or "tickflow"


def build_market_payload(
    *,
    symbol: str,
    interval: str,
    question: str,
    repo_root: Path | None = None,
    provider_hint: str | None = None,
    use_rag: bool = True,
    use_llm_decision: bool = True,
    with_research: bool = False,
    research_keyword: str | None = None,
) -> dict[str, Any]:
    sym = str(symbol or "").strip().upper()
    return {
        "symbol": sym,
        "provider": resolve_provider_for_symbol(
            repo_root=repo_root,
            symbol=sym,
            provider_hint=provider_hint,
        ),
        "interval": str(interval or "4h").strip().lower() or "4h",
        "question": str(question or "").strip(),
        "use_rag": bool(use_rag),
        "use_llm_decision": bool(use_llm_decision),
        "with_research": bool(with_research),
        "research_keyword": str(research_keyword or "").strip() or None,
    }


def build_market_payloads(
    symbols: list[str],
    *,
    interval: str,
    question: str,
    repo_root: Path | None = None,
    provider_hint: str | None = None,
    use_rag: bool = True,
    use_llm_decision: bool = True,
    with_research: bool = False,
    research_keyword: str | None = None,
    per_symbol_research_keyword: dict[str, str | None] | None = None,
) -> list[dict[str, Any]]:
    rk_map = per_symbol_research_keyword or {}
    out: list[dict[str, Any]] = []
    for raw_sym in symbols:
        sym = str(raw_sym or "").strip().upper()
        if not sym:
            continue
        rk = rk_map.get(sym, research_keyword)
        out.append(
            build_market_payload(
                symbol=sym,
                interval=interval,
                question=question,
                repo_root=repo_root,
                provider_hint=provider_hint,
                use_rag=use_rag,
                use_llm_decision=use_llm_decision,
                with_research=with_research,
                research_keyword=rk,
            )
        )
    return out


def normalize_route_payloads(route: dict[str, Any]) -> list[dict[str, Any]]:
    """将 route 中的 payloads / payload 归一化为 payloads 列表（宽度可为 1）。"""
    payloads = route.get("payloads")
    if isinstance(payloads, list):
        return [dict(p) for p in payloads if isinstance(p, dict)]
    pay = route.get("payload")
    if isinstance(pay, dict) and pay:
        return [dict(pay)]
    return []


def ensure_payload_providers(
    payloads: list[dict[str, Any]],
    *,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    """校正 payloads 内每个 symbol 的 provider（catalog 为唯一权威）。"""
    out: list[dict[str, Any]] = []
    for raw in payloads:
        if not isinstance(raw, dict):
            continue
        cp = dict(raw)
        sym = str(cp.get("symbol") or "").strip()
        if sym:
            cp["provider"] = resolve_provider_for_symbol(
                repo_root=repo_root,
                symbol=sym,
                provider_hint=str(cp.get("provider") or ""),
            )
        out.append(cp)
    return out
