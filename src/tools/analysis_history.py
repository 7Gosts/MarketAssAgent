"""Business analysis snapshot lookup backed by the trading database."""

from __future__ import annotations

from typing import Any

from config.runtime_config import get_postgres_dsn
from infrastructure.persistence.analysis_snapshot_repository import AnalysisSnapshotRepository
from utils.logging_utils import get_logger


logger = get_logger(__name__)


def _normalize_symbol_for_match(symbol: Any) -> str:
    return str(symbol or "").strip().upper().replace("_", "").replace("-", "")


def _safe_limit(value: int, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, minimum), maximum)


def _load_previous_analysis_snapshot_from_db(
    *,
    session_id: str,
    symbol: str,
    interval: str,
    exclude_request_id: str,
    limit: int,
) -> dict[str, Any] | None:
    repo: AnalysisSnapshotRepository | None = None
    try:
        repo = AnalysisSnapshotRepository()
        row = repo.get_previous_by_context(
            session_id=session_id,
            symbol=symbol,
            interval=interval,
            exclude_request_id=exclude_request_id,
            limit=limit,
        )
        return repo.to_compact_payload(row) if row is not None else None
    except Exception as exc:
        logger.warning(
            "[analysis-snapshot] read failed session_id=%s symbol=%s interval=%s error=%s",
            session_id,
            symbol,
            interval,
            type(exc).__name__,
        )
        return None
    finally:
        if repo is not None:
            repo.close()


def get_previous_analysis_snapshot(
    session_id: str,
    symbol: str,
    interval: str,
    exclude_request_id: str = "",
    limit: int = 50,
    request_id: str = "",
) -> dict[str, Any]:
    """Read the previous business snapshot for the same session, symbol and interval."""
    symbol_key = _normalize_symbol_for_match(symbol)
    interval_key = str(interval or "").strip()
    if not symbol_key or not interval_key:
        return {
            "status": "error",
            "session_id": session_id,
            "snapshot": {},
            "error": "symbol and interval are required",
        }
    if not get_postgres_dsn():
        return {
            "status": "error",
            "session_id": session_id,
            "snapshot": {},
            "error": "PostgreSQL not configured",
        }

    snapshot = _load_previous_analysis_snapshot_from_db(
        session_id=session_id,
        symbol=symbol,
        interval=interval_key,
        exclude_request_id=str(exclude_request_id or request_id).strip(),
        limit=_safe_limit(limit, default=50, minimum=1, maximum=200),
    )
    if snapshot:
        return {"status": "success", "session_id": session_id, "snapshot": snapshot}
    return {"status": "not_found", "session_id": session_id, "snapshot": {}}
