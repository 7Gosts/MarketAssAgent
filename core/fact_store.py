from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Fact:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    thread_id: str = ""
    source: str = "unknown"
    timestamp: str = ""
    type: str = "generic"
    payload: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "source": self.source,
            "timestamp": self.timestamp,
            "type": self.type,
            "payload": self.payload,
            "provenance": self.provenance,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fact:
        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            thread_id=str(data.get("thread_id") or ""),
            source=str(data.get("source") or "unknown"),
            timestamp=str(data.get("timestamp") or ""),
            type=str(data.get("type") or "generic"),
            payload=data.get("payload") if isinstance(data.get("payload"), dict) else {},
            provenance=data.get("provenance") if isinstance(data.get("provenance"), dict) else {},
            tags=[str(x) for x in data.get("tags") or []] if isinstance(data.get("tags"), list) else [],
        )


class FactStore(Protocol):
    """FactStore 接口：facts + checkpoints 持久化。"""

    def write_fact(self, fact: Fact) -> str: ...
    def get_latest_fact(self, thread_id: str, fact_type: str) -> Fact | None: ...
    def recall(self, thread_id: str, query: dict[str, Any], limit: int = 10) -> list[Fact]: ...
    def set_checkpoint(self, thread_id: str, key: str, value: Any) -> None: ...
    def get_checkpoint(self, thread_id: str, key: str) -> Any: ...
