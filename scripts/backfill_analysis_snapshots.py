#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "runtime", ROOT / "src", ROOT):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from infrastructure.persistence.analysis_snapshot_repository import AnalysisSnapshotRepository
from infrastructure.persistence.db import init_db
from utils.logging_utils import get_logger
from utils.runtime_paths import get_output_dir


logger = get_logger(__name__)
_SKIP_SESSION_PREFIXES = ("s_ctx_", "smoke_", "test_", "verify_")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把历史 analysis_snapshot facts 回填到 PostgreSQL analysis_snapshots。")
    parser.add_argument(
        "--facts-path",
        default=str(get_output_dir() / "memory_facts.jsonl"),
        help="memory_facts.jsonl 路径",
    )
    parser.add_argument(
        "--include-test-sessions",
        action="store_true",
        help="默认跳过 s_ctx_/test_/smoke_/verify_ 这类测试 session；传此参数则一起导入。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计和打印，不实际写库。",
    )
    return parser.parse_args()


def _should_skip_session(session_id: str, *, include_test_sessions: bool) -> bool:
    if include_test_sessions:
        return False
    clean = str(session_id or "").strip()
    return clean.startswith(_SKIP_SESSION_PREFIXES)


def _load_analysis_snapshot_facts(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"未找到 memory_facts 文件: {path}")

    rows: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[analysis-snapshot-backfill] 跳过坏行 line=%s", lineno)
            continue
        if not isinstance(obj, dict):
            continue
        if str(obj.get("type") or "").strip() != "analysis_snapshot":
            continue
        rows.append(obj)
    return rows


def _candidate_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
    return (
        str(row.get("thread_id") or "").strip(),
        str(provenance.get("request_id") or "").strip(),
        str(payload.get("symbol") or "").strip(),
        str(payload.get("interval") or "").strip(),
        str(payload.get("timestamp") or "").strip(),
    )


def main() -> int:
    args = _parse_args()
    facts_path = Path(str(args.facts_path)).expanduser().resolve()
    logger.info(
        "[analysis-snapshot-backfill] start facts_path=%s dry_run=%s include_test_sessions=%s",
        facts_path,
        "yes" if args.dry_run else "no",
        "yes" if args.include_test_sessions else "no",
    )

    rows = _load_analysis_snapshot_facts(facts_path)
    logger.info("[analysis-snapshot-backfill] loaded analysis_snapshot facts count=%s", len(rows))

    seen: set[tuple[str, str, str, str, str]] = set()
    candidates: list[dict[str, Any]] = []
    skipped_test_sessions = 0
    skipped_input_duplicates = 0
    skipped_invalid = 0

    for row in rows:
        thread_id = str(row.get("thread_id") or "").strip()
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if not thread_id or not payload:
            skipped_invalid += 1
            continue
        if _should_skip_session(thread_id, include_test_sessions=bool(args.include_test_sessions)):
            skipped_test_sessions += 1
            continue
        if not all(str(payload.get(k) or "").strip() for k in ("symbol", "interval", "timestamp")):
            skipped_invalid += 1
            continue
        key = _candidate_key(row)
        if key in seen:
            skipped_input_duplicates += 1
            continue
        seen.add(key)
        candidates.append(row)

    logger.info(
        "[analysis-snapshot-backfill] prepared candidates=%s skipped_test_sessions=%s skipped_input_duplicates=%s skipped_invalid=%s",
        len(candidates),
        skipped_test_sessions,
        skipped_input_duplicates,
        skipped_invalid,
    )

    if args.dry_run:
        logger.info("[analysis-snapshot-backfill] dry_run complete")
        return 0

    init_db()
    repo = AnalysisSnapshotRepository()
    inserted = 0
    skipped_existing = 0
    try:
        for row in candidates:
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
            thread_id = str(row.get("thread_id") or "").strip()
            request_id = str(provenance.get("request_id") or "").strip()
            raw_snapshot = dict(payload)
            raw_snapshot["backfill_meta"] = {
                "source": "memory_facts.jsonl",
                "fact_id": str(row.get("id") or "").strip(),
                "fact_timestamp": str(row.get("timestamp") or "").strip(),
            }

            saved, created = repo.create_if_missing(
                session_id=thread_id,
                request_id=request_id,
                snapshot_payload=payload,
                raw_snapshot=raw_snapshot,
            )
            snapshot_ref = repo.get_snapshot_ref(saved) or "-"
            action = "inserted" if created else "skip_existing"
            if created:
                inserted += 1
            else:
                skipped_existing += 1
            logger.info(
                "[analysis-snapshot-backfill] %s session_id=%s request_id=%s snapshot_id=%s symbol=%s interval=%s timestamp=%s",
                action,
                thread_id,
                request_id or "-",
                snapshot_ref,
                str(payload.get("symbol") or "-"),
                str(payload.get("interval") or "-"),
                str(payload.get("timestamp") or "-"),
            )
    finally:
        repo.close()

    logger.info(
        "[analysis-snapshot-backfill] complete inserted=%s skipped_existing=%s candidates=%s",
        inserted,
        skipped_existing,
        len(candidates),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
