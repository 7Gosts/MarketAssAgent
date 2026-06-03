"""三市场统一行情拉取：Agent / CLI / LangGraph 工具共用。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.executors.facts_bundle import merge_facts_bundle
from app.market_data.resolver import ensure_payload_providers


def fetch_market_snapshots(
    *,
    repo_root: Path,
    payloads: list[dict[str, Any]],
    limit: int = 180,
) -> dict[str, Any]:
    """按 payloads 批量拉行情快照；provider 由 catalog 强制校正。"""
    from app.executors.multi_asset_compare import run_multi_asset_compare

    clean = ensure_payload_providers(payloads, repo_root=repo_root)
    if not clean:
        raise ValueError("fetch_market_snapshots requires at least one payload")
    return run_multi_asset_compare(repo_root=repo_root, payloads=clean, limit=limit)


def merge_snapshot_facts_bundle(
    *,
    compare_result: dict[str, Any],
    user_question: str,
    symbols: list[str],
    task_type: str = "analysis",
    response_mode: str = "analysis",
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """将 fetch_market_snapshots 结果合并为 facts_bundle（N=1 时填充 analysis_facts）。"""
    cf = compare_result
    market_facts: dict[str, Any] = {"multi_compare": {"rows": cf.get("rows")}}
    items = cf.get("items") if isinstance(cf.get("items"), list) else []
    if len(items) == 1 and isinstance(items[0], dict):
        nf = items[0].get("narrative_facts")
        if isinstance(nf, dict) and nf:
            market_facts["analysis_facts"] = nf

    base_trace = {"executors": ["fetch_market_snapshots"], "note": "digest_writer"}
    if trace:
        base_trace.update(trace)

    return merge_facts_bundle(
        task_type=task_type,
        response_mode=response_mode,
        user_question=user_question,
        symbols=symbols,
        market_facts=market_facts,
        compare_facts=cf,
        evidence_sources=cf.get("evidence_sources") or [],
        risk_flags=cf.get("risk_flags") or [],
        trace=base_trace,
    )


def snapshot_output_refs(compare_result: dict[str, Any]) -> dict[str, str]:
    """单标的时提取 output_refs 供 followup。"""
    items = compare_result.get("items") if isinstance(compare_result.get("items"), list) else []
    if len(items) != 1 or not isinstance(items[0], dict):
        return {}
    refs = items[0].get("output_refs")
    if not isinstance(refs, dict):
        return {}
    return {k: str(v) for k, v in refs.items() if v}
