from __future__ import annotations

import os


def crash_if_requested(point: str) -> None:
    """Terminate immediately at an explicitly configured durability test point."""
    if os.getenv("CONTEXT_CRASH_POINT", "").strip() == point:
        os._exit(91)
