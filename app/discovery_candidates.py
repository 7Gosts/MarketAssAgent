from __future__ import annotations

from pathlib import Path
from typing import Any

from app.feishu_asset_catalog import get_catalog_for_repo
from tools.llm.client import discover_market_targets


def normalize_discovery_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    market = str(candidate.get("market") or "").strip().upper()
    provider = str(candidate.get("provider") or "").strip().lower()
    if provider not in {"tickflow", "gateio", "goldapi"}:
        if market == "CRYPTO":
            provider = "gateio"
        elif market in {"PM", "COMMODITY"}:
            provider = "goldapi"
        else:
            provider = "tickflow"
    return {
        "symbol": str(candidate.get("symbol") or "").strip().upper(),
        "name": str(candidate.get("name") or candidate.get("symbol") or "").strip(),
        "market": market or ("CRYPTO" if provider == "gateio" else ("PM" if provider == "goldapi" else "US")),
        "provider": provider,
        "research_keyword": str(candidate.get("research_keyword") or candidate.get("name") or candidate.get("symbol") or "").strip(),
        "aliases": [str(x).strip() for x in (candidate.get("aliases") or []) if str(x).strip()],
        "tags": [str(x).strip() for x in (candidate.get("tags") or []) if str(x).strip()],
        "confidence": float(candidate.get("confidence") or 0),
    }


def resolve_asset_candidates(
    *,
    repo_root: Path,
    route: dict[str, Any],
    request_text: str,
    recent_messages: list[dict[str, Any]] | None,
    session_state: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    catalog = get_catalog_for_repo(repo_root)
    discovery = discover_market_targets(
        text=str(((route.get("payload") or {}).get("query_text") or request_text)),
        recent_messages=recent_messages,
        conversation_context={
            "last_action": getattr(session_state, "last_action", None),
            "last_task_type": getattr(session_state, "last_task_type", None),
            "last_symbols": list(getattr(session_state, "last_symbols", []) or []),
        },
        tradable_assets=catalog.tradable_assets_for_prompt(),
    )
    candidates_raw = discovery.get("candidates") if isinstance(discovery.get("candidates"), list) else []
    candidates = [normalize_discovery_candidate(c) for c in candidates_raw if isinstance(c, dict)]
    candidates = [c for c in candidates if c.get("symbol") and c.get("provider")]
    candidates.sort(key=lambda item: float(item.get("confidence") or 0), reverse=True)
    return discovery, candidates


discover_market_candidates = resolve_asset_candidates