from __future__ import annotations

from .base import BaseRenderer


class WebRenderer(BaseRenderer[str]):
    def render(self, content: str) -> str:
        # Web keeps full markdown semantics.
        return content
