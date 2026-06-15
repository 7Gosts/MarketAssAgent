"""Backward-compatible renderer imports.

Canonical modules moved to interfaces.renderers.
"""

from interfaces.renderers import BaseRenderer, FeishuRenderer, WebRenderer

__all__ = ["BaseRenderer", "FeishuRenderer", "WebRenderer"]

