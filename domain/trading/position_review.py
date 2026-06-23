"""Trading domain placeholder for position review logic."""

from __future__ import annotations

from typing import Any


def build_position_review_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Return normalized position-review context for future domain migration."""
    return dict(payload or {})

