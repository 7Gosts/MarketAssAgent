from __future__ import annotations

from pathlib import Path
from typing import Any

from app.executors.analysis_facts import build_analysis_facts
from app.executors.market_snapshot import run_market_snapshot
from app.market_data.resolver import resolve_provider_for_symbol


def run_multi_asset_compare(
    *,
    repo_root: Path,
    payloads: list[dict[str, Any]],
    limit: int = 180,
) -> dict[str, Any]:
    """多标的：拉齐维度的事实行，供 compare writer 排序与差异说明（不直接生成用户长文）。"""
    rows: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    merged_evidence: list[dict[str, Any]] = []
    merged_risk: list[str] = []

    for raw in payloads:
        if not isinstance(raw, dict):
            continue
        sym = str(raw.get("symbol") or "").strip()
        if not sym:
            continue
        provider = resolve_provider_for_symbol(
            repo_root=repo_root,
            symbol=sym,
            provider_hint=str(raw.get("provider") or "gateio"),
        )
        interval = str(raw.get("interval") or "4h").strip()
        question = str(raw.get("question") or "").strip() or None
        bundle = run_market_snapshot(
            repo_root=repo_root,
            symbol=sym,
            provider=provider,
            interval=interval,
            limit=limit,
            out_dir=None,
            question=question,
            rag_top_k=5,
            analysis_style="auto",
            with_research=bool(raw.get("with_research")),
            research_keyword=str(raw.get("research_keyword") or "").strip() or None,
        )
        ar = bundle.get("analysis_result") if isinstance(bundle.get("analysis_result"), dict) else {}
        narrative_facts = build_analysis_facts({"analysis_result": ar})
        wy = ar.get("wyckoff_123_v1") if isinstance(ar.get("wyckoff_123_v1"), dict) else {}
        trig = ar.get("trigger_conditions") if isinstance(ar.get("trigger_conditions"), dict) else {}
        rows.append(
            {
                "symbol": ar.get("symbol") or sym,
                "name": ar.get("name"),
                "interval": ar.get("interval") or interval,
                "last_price": ar.get("last_price"),
                "trend": ar.get("trend"),
                "fib_zone": ar.get("fib_zone"),
                "regime_label": ar.get("regime_label"),
                "regime_confidence": ar.get("regime_confidence"),
                "wyckoff_preferred_side": wy.get("preferred_side"),
                "wyckoff_aligned": wy.get("aligned"),
                "triggered": trig.get("triggered"),
                "entry": trig.get("entry"),
                "stop": trig.get("stop"),
            }
        )
        items.append(
            {
                "symbol": ar.get("symbol") or sym,
                "narrative_facts": narrative_facts,
                "output_refs": {
                    k: v
                    for k, v in {
                        "ai_overview_path": str((bundle.get("meta") or {}).get("ai_overview_path") or ""),
                        "full_report_path": str((bundle.get("meta") or {}).get("full_report_path") or ""),
                    }.items()
                    if v
                },
            }
        )
        for ev in bundle.get("evidence_sources") or []:
            if isinstance(ev, dict) and ev.get("source_path"):
                sp = str(ev.get("source_path"))
                if not any(x.get("source_path") == sp for x in merged_evidence):
                    merged_evidence.append(dict(ev))
        for rf in bundle.get("risk_flags") or []:
            s = str(rf).strip()
            if s and s not in merged_risk:
                merged_risk.append(s)
    if not merged_risk:
        merged_risk = ["normal"]
    return {
        "rows": rows,
        "items": items,
        "risk_flags": merged_risk,
        "evidence_sources": merged_evidence[:32],
    }
