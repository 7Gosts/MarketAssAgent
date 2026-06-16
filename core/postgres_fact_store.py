"""PostgreSQL FactStore 实现（Phase 2）。

使用现有 SQLAlchemy 连接体系（persistence/db.py），不引入 asyncpg。
表结构使用 SQLAlchemy Core 创建，与 FactStore 接口语义对齐。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from sqlalchemy import Column, Float, MetaData, String, Table, Text, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.fact_store import Fact
from persistence.db import get_engine


_metadata = MetaData()

# 表定义（使用 SQLAlchemy Core，便于后续 Alembic 管理）
memory_facts = Table(
    "memory_facts",
    _metadata,
    Column("id", String, primary_key=True),
    Column("thread_id", String, nullable=False, index=True),
    Column("source", String, nullable=False),
    Column("timestamp", String, nullable=False),
    Column("type", String, nullable=False, index=True),
    Column("payload_json", Text, nullable=False),
    Column("provenance_json", Text, nullable=False),
    Column("tags_json", Text, nullable=False),
)

memory_checkpoints = Table(
    "memory_checkpoints",
    _metadata,
    Column("thread_id", String, nullable=False, primary_key=True),
    Column("ck_key", String, nullable=False, primary_key=True),
    Column("value_json", Text, nullable=False),
    Column("updated_ts", Float, nullable=False),
)


class PostgresFactStore:
    """PostgreSQL 实现，与 FactStore 接口一致。"""

    def __init__(self):
        # 确保表存在（Phase 2 简化处理，后续可用 Alembic 管理）
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        engine = get_engine()
        memory_facts.metadata.create_all(engine)
        memory_checkpoints.metadata.create_all(engine)

    def write_fact(self, fact: Fact) -> str:
        if not fact.id:
            fact.id = str(uuid.uuid4())
        if not fact.timestamp:
            fact.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        engine = get_engine()
        payload_json = json.dumps(fact.payload, ensure_ascii=False)
        provenance_json = json.dumps(fact.provenance, ensure_ascii=False)
        tags_json = json.dumps(fact.tags, ensure_ascii=False)

        stmt = pg_insert(memory_facts).values(
            id=fact.id,
            thread_id=fact.thread_id,
            source=fact.source,
            timestamp=fact.timestamp,
            type=fact.type,
            payload_json=payload_json,
            provenance_json=provenance_json,
            tags_json=tags_json,
        ).on_conflict_do_update(
            index_elements=["id"],
            set_={
                "payload_json": payload_json,
                "provenance_json": provenance_json,
                "tags_json": tags_json,
            },
        )
        with engine.begin() as conn:
            conn.execute(stmt)
        return fact.id

    def get_latest_fact(self, thread_id: str, fact_type: str) -> Fact | None:
        engine = get_engine()
        stmt = (
            select(memory_facts)
            .where(memory_facts.c.thread_id == thread_id, memory_facts.c.type == fact_type)
            .order_by(memory_facts.c.timestamp.desc())
            .limit(1)
        )
        with engine.connect() as conn:
            row = conn.execute(stmt).fetchone()
        if row is None:
            return None
        return Fact(
            id=str(row.id),
            thread_id=str(row.thread_id),
            source=str(row.source),
            timestamp=str(row.timestamp),
            type=str(row.type),
            payload=json.loads(row.payload_json or "{}"),
            provenance=json.loads(row.provenance_json or "{}"),
            tags=json.loads(row.tags_json or "[]"),
        )

    def recall(self, thread_id: str, query: dict[str, Any], limit: int = 10) -> list[Fact]:
        engine = get_engine()
        stmt = select(memory_facts).where(memory_facts.c.thread_id == thread_id)

        fact_type = str(query.get("type") or "").strip()
        if fact_type:
            stmt = stmt.where(memory_facts.c.type == fact_type)

        source = str(query.get("source") or "").strip()
        if source:
            stmt = stmt.where(memory_facts.c.source == source)

        tag = str(query.get("tag") or "").strip()
        if tag:
            stmt = stmt.where(memory_facts.c.tags_json.like(f'%"{tag}"%'))

        stmt = stmt.order_by(memory_facts.c.timestamp.desc()).limit(max(1, int(limit)))

        with engine.connect() as conn:
            rows = conn.execute(stmt).fetchall()

        out: list[Fact] = []
        for row in rows:
            out.append(
                Fact(
                    id=str(row.id),
                    thread_id=str(row.thread_id),
                    source=str(row.source),
                    timestamp=str(row.timestamp),
                    type=str(row.type),
                    payload=json.loads(row.payload_json or "{}"),
                    provenance=json.loads(row.provenance_json or "{}"),
                    tags=json.loads(row.tags_json or "[]"),
                )
            )
        return out

    def set_checkpoint(self, thread_id: str, key: str, value: Any) -> None:
        engine = get_engine()
        value_json = json.dumps(value, ensure_ascii=False)
        updated_ts = time.time()

        stmt = pg_insert(memory_checkpoints).values(
            thread_id=thread_id,
            ck_key=key,
            value_json=value_json,
            updated_ts=updated_ts,
        ).on_conflict_do_update(
            index_elements=["thread_id", "ck_key"],
            set_={"value_json": value_json, "updated_ts": updated_ts},
        )
        with engine.begin() as conn:
            conn.execute(stmt)

    def get_checkpoint(self, thread_id: str, key: str) -> Any:
        engine = get_engine()
        stmt = select(memory_checkpoints.c.value_json).where(
            memory_checkpoints.c.thread_id == thread_id, memory_checkpoints.c.ck_key == key
        )
        with engine.connect() as conn:
            row = conn.execute(stmt).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row.value_json)
        except Exception:
            return None
