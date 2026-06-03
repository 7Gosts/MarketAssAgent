from __future__ import annotations

from pathlib import Path
from typing import Any

from app.discovery_candidates import resolve_asset_candidates
from app.discovery_execution import build_resolution_fallback, dispatch_resolved_asset_candidate


def run_asset_resolution_pipeline(*, repo_root: Path, route: dict[str, Any], request_text: str, request_context: dict[str, Any] | None, session_state: Any) -> dict[str, Any]:
    recent_messages = None
    if isinstance(request_context, dict):
        rm = request_context.get("recent_messages")
        if isinstance(rm, list):
            recent_messages = rm
    discovery, candidates = resolve_asset_candidates(
        repo_root=repo_root,
        route=route,
        request_text=request_text,
        recent_messages=recent_messages,
        session_state=session_state,
    )
    if not candidates:
        return build_resolution_fallback(
            discovery_reason=str(discovery.get("reason") or "").strip() or None,
            failures=None,
        )

    failures: list[str] = []
    for candidate in candidates[:3]:
        try:
            return dispatch_resolved_asset_candidate(
                repo_root=repo_root,
                route=route,
                request_text=request_text,
                discovery_reason=str(discovery.get("reason") or "").strip() or None,
                candidate=candidate,
            )
        except Exception as exc:
            failures.append(f"{candidate.get('symbol')}:{exc}")

    return build_resolution_fallback(
        discovery_reason=str(discovery.get("reason") or "").strip() or None,
        failures=failures,
    )


run_discovery_pipeline = run_asset_resolution_pipeline