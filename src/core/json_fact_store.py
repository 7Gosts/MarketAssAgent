"""JSON/JSONL FactStore — 本地轻量 memory 后端（默认）。"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from core.fact_store import Fact


class JsonFactStore:
    """Append-only JSONL facts for explicit user-profile preferences."""

    def __init__(self, *, facts_path: Path):
        self.facts_path = facts_path
        self.facts_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write_fact(self, fact: Fact) -> str:
        with self._lock:
            line = json.dumps(fact.to_dict(), ensure_ascii=False)
            with open(self.facts_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return fact.id

    def _load_all_facts(self) -> list[Fact]:
        if not self.facts_path.is_file():
            return []
        facts: list[Fact] = []
        for line in self.facts_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    facts.append(Fact.from_dict(data))
            except json.JSONDecodeError:
                continue
        return facts

    def _filter_facts(self, thread_id: str, query: dict[str, Any]) -> list[Fact]:
        fact_type = str(query.get("type") or "").strip()
        source = str(query.get("source") or "").strip()
        tag = str(query.get("tag") or "").strip()

        matched: list[Fact] = []
        for fact in self._load_all_facts():
            if fact.thread_id != thread_id:
                continue
            if fact_type and fact.type != fact_type:
                continue
            if source and fact.source != source:
                continue
            if tag and tag not in fact.tags:
                continue
            matched.append(fact)

        matched.sort(key=lambda f: f.timestamp, reverse=True)
        return matched

    def get_latest_fact(self, thread_id: str, fact_type: str) -> Fact | None:
        with self._lock:
            facts = self._filter_facts(thread_id, {"type": fact_type})
        return facts[0] if facts else None

    def recall(self, thread_id: str, query: dict[str, Any], limit: int = 10) -> list[Fact]:
        with self._lock:
            facts = self._filter_facts(thread_id, query)
        return facts[: max(1, int(limit))]
