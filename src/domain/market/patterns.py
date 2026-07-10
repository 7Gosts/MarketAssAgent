"""Pattern detection domain logic."""

from __future__ import annotations

from typing import Any

def _build_pattern_detection_v2(
    *,
    market_structure_v2: dict[str, Any],
    levels_v2: dict[str, Any],
) -> dict[str, Any]:
    label = str(market_structure_v2.get("structure_label") or "unknown")
    conf = float(market_structure_v2.get("confidence") or 0.0)
    pattern_name = {
        "triangle_convergence": "triangle_convergence",
        "rectangle": "rectangle",
        "expanding_triangle": "expanding_triangle",
        "channel_up": "channel_up",
        "channel_down": "channel_down",
        "accumulation": "accumulation",
        "distribution": "distribution",
        "markup": "markup",
        "markdown": "markdown",
    }.get(label, "unknown")
    status = "active" if pattern_name != "unknown" else "unclear"
    key_levels = {
        "nearest_support": levels_v2.get("nearest_support"),
        "nearest_resistance": levels_v2.get("nearest_resistance"),
    }
    return {
        "primary_pattern": pattern_name,
        "status": status,
        "confidence": round(conf, 3),
        "wyckoff_phase": market_structure_v2.get("wyckoff_phase"),
        "wyckoff_phase_transition": market_structure_v2.get("wyckoff_phase_transition"),
        "wyckoff_signals": list(market_structure_v2.get("wyckoff_signals") or [])[:3],
        "multi_pattern_overlap": list(market_structure_v2.get("multi_pattern_overlap") or [])[:3],
        "evidence": list(market_structure_v2.get("evidence") or [])[:3],
        "invalid_conditions": list(market_structure_v2.get("invalid_conditions") or [])[:2],
        "key_levels": key_levels,
    }
