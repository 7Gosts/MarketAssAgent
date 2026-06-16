from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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


class SQLiteFactStore:
    """Minimal durable store for facts + checkpoints (Phase A)."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_thread_ts ON facts(thread_id, timestamp DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_facts_thread_type ON facts(thread_id, type)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id TEXT NOT NULL,
                    ck_key TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    updated_ts REAL NOT NULL,
                    PRIMARY KEY(thread_id, ck_key)
                )
                """
            )

    def write_fact(self, fact: Fact) -> str:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO facts
                (id, thread_id, source, timestamp, type, payload_json, provenance_json, tags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.id,
                    fact.thread_id,
                    fact.source,
                    fact.timestamp,
                    fact.type,
                    json.dumps(fact.payload, ensure_ascii=False),
                    json.dumps(fact.provenance, ensure_ascii=False),
                    json.dumps(fact.tags, ensure_ascii=False),
                ),
            )
        return fact.id

    def get_latest_fact(self, thread_id: str, fact_type: str) -> Fact | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM facts
                WHERE thread_id = ? AND type = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (thread_id, fact_type),
            ).fetchone()
        if row is None:
            return None
        return Fact(
            id=str(row["id"]),
            thread_id=str(row["thread_id"]),
            source=str(row["source"]),
            timestamp=str(row["timestamp"]),
            type=str(row["type"]),
            payload=_parse_json_dict(row["payload_json"]),
            provenance=_parse_json_dict(row["provenance_json"]),
            tags=_parse_json_list(row["tags_json"]),
        )

    def recall(self, thread_id: str, query: dict[str, Any], limit: int = 10) -> list[Fact]:
        sql = "SELECT * FROM facts WHERE thread_id = ?"
        params: list[Any] = [thread_id]
        fact_type = str(query.get("type") or "").strip()
        source = str(query.get("source") or "").strip()
        tag = str(query.get("tag") or "").strip()

        if fact_type:
            sql += " AND type = ?"
            params.append(fact_type)
        if source:
            sql += " AND source = ?"
            params.append(source)
        if tag:
            sql += " AND tags_json LIKE ?"
            params.append(f'%"{tag}"%')

        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(max(1, int(limit)))

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        out: list[Fact] = []
        for row in rows:
            out.append(
                Fact(
                    id=str(row["id"]),
                    thread_id=str(row["thread_id"]),
                    source=str(row["source"]),
                    timestamp=str(row["timestamp"]),
                    type=str(row["type"]),
                    payload=_parse_json_dict(row["payload_json"]),
                    provenance=_parse_json_dict(row["provenance_json"]),
                    tags=_parse_json_list(row["tags_json"]),
                )
            )
        return out

    def set_checkpoint(self, thread_id: str, key: str, value: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoints
                (thread_id, ck_key, value_json, updated_ts)
                VALUES (?, ?, ?, ?)
                """,
                (thread_id, key, json.dumps(value, ensure_ascii=False), time.time()),
            )

    def get_checkpoint(self, thread_id: str, key: str) -> Any:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT value_json FROM checkpoints
                WHERE thread_id = ? AND ck_key = ?
                """,
                (thread_id, key),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(str(row["value_json"]))
        except json.JSONDecodeError:
            return None


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    try:
        val = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return val if isinstance(val, dict) else {}


def _parse_json_list(raw: Any) -> list[str]:
    try:
        val = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(val, list):
        return []
    return [str(x) for x in val]
