from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar


T = TypeVar("T")


class BaseRenderer(ABC, Generic[T]):
    @abstractmethod
    def render(self, content: str) -> T:
        """Render normalized markdown content into channel-specific payload."""
        raise NotImplementedError
