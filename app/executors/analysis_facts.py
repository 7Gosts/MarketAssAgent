from __future__ import annotations

from typing import Any


def build_analysis_facts(result: dict[str, Any]) -> dict[str, Any]:
    analysis = result.get("analysis_result") if isinstance(result.get("analysis_result"), dict) else {}
    narrative_facts: dict[str, Any] = {}
    for key in ("symbol", "name", "provider", "interval", "trend", "last_price", "fib_zone", "regime_label"):
        if key in analysis and analysis.get(key) is not None:
            narrative_facts[key] = analysis.get(key)
    if isinstance(analysis.get("fixed_template"), dict):
        narrative_facts["fixed_template"] = analysis.get("fixed_template")
    if isinstance(analysis.get("wyckoff_123_v1"), dict):
        wy = analysis.get("wyckoff_123_v1")
        narrative_facts["wyckoff_123_v1"] = {
            k: wy[k] for k in ("background", "preferred_side", "aligned", "selected_setup", "setups") if k in wy
        }
    if isinstance(analysis.get("ma_snapshot"), dict):
        narrative_facts["ma_snapshot"] = analysis.get("ma_snapshot")
    return narrative_facts
