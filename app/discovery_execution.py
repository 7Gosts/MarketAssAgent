from __future__ import annotations

from pathlib import Path
from typing import Any

from app.autonomous_assets import register_discovered_asset
from app.capabilities.quote_facts import run_quote_facts_bundle
from app.executors.analysis_facts import build_analysis_facts
from app.market_data.resolver import build_market_payload, normalize_route_payloads
from app.market_data.snapshots import (
    fetch_market_snapshots,
    merge_snapshot_facts_bundle,
    snapshot_output_refs,
)


def build_analysis_route_for_candidate(route: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    base_list = normalize_route_payloads(route)
    base = dict(base_list[0]) if base_list else dict(route.get("payload") or {})
    sym = str(candidate.get("symbol") or "").strip().upper()
    question = (
        str(base.get("question") or "").strip()
        or str(candidate.get("name") or candidate.get("symbol") or "")
    )
    rk = str(candidate.get("research_keyword") or base.get("research_keyword") or "").strip() or None
    payload = build_market_payload(
        symbol=sym,
        interval=str(base.get("interval") or "1d").strip() or "1d",
        question=question,
        provider_hint=str(candidate.get("provider") or base.get("provider") or "gateio"),
        use_rag=bool(base.get("use_rag", True)),
        use_llm_decision=bool(base.get("use_llm_decision", True)),
        with_research=bool(base.get("with_research")),
        research_keyword=rk,
    )

    task_plan = dict(route.get("task_plan") or {})
    task_plan["symbols"] = [payload["symbol"]]
    task_plan["provider"] = payload["provider"]
    task_plan["interval"] = payload["interval"]
    if payload.get("research_keyword"):
        task_plan["research_keyword"] = payload["research_keyword"]

    return {**route, "action": "analyze_multi", "payloads": [payload], "task_plan": task_plan}


def execute_resolved_asset_analysis(
    *,
    repo_root: Path,
    candidate: dict[str, Any],
    route: dict[str, Any],
    request_text: str,
    discovery_reason: str | None,
) -> dict[str, Any]:
    register_discovered_asset(repo_root=repo_root, candidate=candidate)
    analysis_route = build_analysis_route_for_candidate(route, candidate)
    payloads = analysis_route.get("payloads") if isinstance(analysis_route.get("payloads"), list) else []
    payload = payloads[0] if payloads and isinstance(payloads[0], dict) else {}
    sym = str(payload.get("symbol") or "").strip()

    cf = fetch_market_snapshots(repo_root=repo_root, payloads=payloads)
    fb = merge_snapshot_facts_bundle(
        compare_result=cf,
        user_question=request_text,
        symbols=[sym],
        trace={
            "executors": ["autonomous_discovery", "fetch_market_snapshots"],
            "task_mode": "autonomous",
            "discovery_reason": discovery_reason,
            "discovered_symbol": candidate.get("symbol"),
        },
    )
    output_refs = snapshot_output_refs(cf)
    items = cf.get("items") if isinstance(cf.get("items"), list) else []
    narrative_facts = (
        items[0].get("narrative_facts")
        if len(items) == 1 and isinstance(items[0], dict)
        else build_analysis_facts({"analysis_result": {}})
    )
    return {**fb, "_output_refs": output_refs, "_narrative_facts": narrative_facts}


def execute_resolved_asset_quote(
    *,
    repo_root: Path,
    route: dict[str, Any],
    request_text: str,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    symbol = str(candidate["symbol"])
    base = dict(route.get("payload") or {})
    if not base and isinstance(route.get("payloads"), list) and route["payloads"]:
        base = dict(route["payloads"][0])
    payload = build_market_payload(
        symbol=symbol,
        interval=str(base.get("interval") or "1d").strip() or "1d",
        question=request_text,
        provider_hint=str(candidate.get("provider") or base.get("provider") or "gateio"),
        use_rag=True,
    )
    fb = run_quote_facts_bundle(
        repo_root=repo_root,
        user_question=request_text,
        payloads=[payload],
    )
    register_discovered_asset(repo_root=repo_root, candidate=candidate)
    fb.setdefault("trace", {})["discovered_symbol"] = symbol
    return fb


def build_resolution_fallback(*, discovery_reason: str | None, failures: list[str] | None = None) -> dict[str, Any]:
    message = "我识别到这像是金融标的请求，但这轮自动探测还没成功。你可以直接给我代码，或再补一句市场/交易所。"
    if failures:
        message += f" 已尝试：{'; '.join(failures[:2])}"
    return {"kind": "chat_fallback", "message": message, "reason": discovery_reason}


def dispatch_resolved_asset_candidate(
    *,
    repo_root: Path,
    route: dict[str, Any],
    request_text: str,
    discovery_reason: str | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    task_type = str(route.get("task_type") or "analysis").strip().lower()
    if task_type == "quote":
        fb = execute_resolved_asset_quote(repo_root=repo_root, route=route, request_text=request_text, candidate=candidate)
        return {"kind": "facts_bundle", "value": fb}

    fb = execute_resolved_asset_analysis(
        repo_root=repo_root,
        candidate=candidate,
        route=route,
        request_text=request_text,
        discovery_reason=discovery_reason,
    )
    return {"kind": "facts_bundle", "value": fb, "narrative_facts": fb.get("_narrative_facts")}


finalize_discovered_asset = execute_resolved_asset_analysis
run_quote_discovery = execute_resolved_asset_quote
build_discovery_fallback = build_resolution_fallback
dispatch_discovery_candidate = dispatch_resolved_asset_candidate
