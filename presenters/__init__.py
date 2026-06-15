"""Backward-compatible presenter imports.

Canonical presenters moved to interfaces.presenters.
"""

from interfaces.presenters import FeishuDelivery, FeishuPresenter, WebPresenter

__all__ = ["FeishuDelivery", "FeishuPresenter", "WebPresenter"]

