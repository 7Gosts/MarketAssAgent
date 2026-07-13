from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from .db import get_session
from .models import AnalysisSnapshot


def normalize_snapshot_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper().replace("_", "").replace("-", "")


def _parse_snapshot_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc)
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _list_payload(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


class AnalysisSnapshotRepository:
    def __init__(self, session: Optional[Session] = None):
        self.session = session or get_session()
        self._owns_session = session is None

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def find_existing(
        self,
        *,
        session_id: str,
        request_id: str,
        symbol: str,
        interval: str,
        snapshot_timestamp: str,
    ) -> AnalysisSnapshot | None:
        clean_session = str(session_id or "").strip()
        clean_interval = str(interval or "").strip()
        clean_request = str(request_id or "").strip()
        symbol_key = normalize_snapshot_symbol(symbol)
        if not clean_session or not clean_interval or not symbol_key:
            return None

        query = self.session.query(AnalysisSnapshot).filter(
            AnalysisSnapshot.session_id == clean_session,
            AnalysisSnapshot.symbol_key == symbol_key,
            AnalysisSnapshot.interval == clean_interval,
        )
        if clean_request:
            query = query.filter(AnalysisSnapshot.source_request_id == clean_request)
        clean_ts = str(snapshot_timestamp or "").strip()
        if clean_ts:
            query = query.filter(AnalysisSnapshot.snapshot_time == _parse_snapshot_time(clean_ts))
        return (
            query.order_by(
                AnalysisSnapshot.snapshot_time.desc(),
                AnalysisSnapshot.id.desc(),
            )
            .first()
        )

    def create(
        self,
        *,
        session_id: str,
        request_id: str,
        snapshot_payload: dict[str, Any],
        raw_snapshot: dict[str, Any] | None = None,
        snapshot_id: str | None = None,
    ) -> AnalysisSnapshot:
        symbol = str(snapshot_payload.get("symbol") or "").strip()
        interval = str(snapshot_payload.get("interval") or "").strip()
        timestamp = str(snapshot_payload.get("timestamp") or "").strip()
        trend = str(snapshot_payload.get("trend") or "").strip()
        price = snapshot_payload.get("price")
        if not symbol or not interval or not timestamp or not trend or not isinstance(price, (int, float)):
            raise ValueError("snapshot_payload 缺少 analysis_snapshots 必需字段")

        support = _list_payload(snapshot_payload.get("support"))
        resistance = _list_payload(snapshot_payload.get("resistance"))
        stance = str(snapshot_payload.get("stance") or "").strip() or None
        schema_version = str(snapshot_payload.get("schema_version") or "analysis_snapshot.v1").strip() or "analysis_snapshot.v1"
        clean_session = str(session_id or "").strip()
        clean_request = str(request_id or "").strip()
        full_raw_snapshot = dict(raw_snapshot) if isinstance(raw_snapshot, dict) else {}
        snapshot_ref = snapshot_id or f"snap_{uuid.uuid4().hex[:24]}"
        symbol_key = normalize_snapshot_symbol(symbol)
        market = str(full_raw_snapshot.get("market") or snapshot_payload.get("market") or "").strip() or None
        provider = (
            str(full_raw_snapshot.get("provider") or snapshot_payload.get("provider") or "").strip()
            or "marketassagent"
        )
        payload_json = {
            **full_raw_snapshot,
            "schema_version": schema_version,
            "symbol": symbol,
            "interval": interval,
            "timestamp": timestamp,
            "trend": trend,
            "current_price": float(price),
        }
        if stance:
            payload_json.setdefault("stance", stance)
        if support:
            payload_json.setdefault("support", support)
        if resistance:
            payload_json.setdefault("resistance", resistance)

        row = AnalysisSnapshot(
            snapshot_id=snapshot_ref,
            session_id=clean_session,
            source_request_id=clean_request,
            symbol=symbol,
            symbol_key=symbol_key,
            market=market,
            provider=provider,
            interval=interval,
            snapshot_time=_parse_snapshot_time(timestamp),
            current_price=float(price),
            trend=trend,
            stance=stance,
            support_json=support or None,
            resistance_json=resistance or None,
            payload_json=payload_json,
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def create_if_missing(
        self,
        *,
        session_id: str,
        request_id: str,
        snapshot_payload: dict[str, Any],
        raw_snapshot: dict[str, Any] | None = None,
        snapshot_id: str | None = None,
    ) -> tuple[AnalysisSnapshot, bool]:
        symbol = str(snapshot_payload.get("symbol") or "").strip()
        interval = str(snapshot_payload.get("interval") or "").strip()
        snapshot_timestamp = str(snapshot_payload.get("timestamp") or "").strip()
        existing = self.find_existing(
            session_id=session_id,
            request_id=request_id,
            symbol=symbol,
            interval=interval,
            snapshot_timestamp=snapshot_timestamp,
        )
        if existing is not None:
            return existing, False
        row = self.create(
            session_id=session_id,
            request_id=request_id,
            snapshot_payload=snapshot_payload,
            raw_snapshot=raw_snapshot,
            snapshot_id=snapshot_id,
        )
        return row, True

    def get_previous_by_context(
        self,
        *,
        session_id: str,
        symbol: str,
        interval: str,
        exclude_request_id: str = "",
        limit: int = 50,
    ) -> AnalysisSnapshot | None:
        clean_session = str(session_id or "").strip()
        clean_interval = str(interval or "").strip()
        symbol_key = normalize_snapshot_symbol(symbol)
        if not clean_session or not clean_interval or not symbol_key:
            return None

        query = self.session.query(AnalysisSnapshot).filter(
            AnalysisSnapshot.session_id == clean_session,
            AnalysisSnapshot.symbol_key == symbol_key,
            AnalysisSnapshot.interval == clean_interval,
        )
        clean_exclude = str(exclude_request_id or "").strip()
        if clean_exclude:
            query = query.filter(AnalysisSnapshot.source_request_id != clean_exclude)
        return (
            query.order_by(
                AnalysisSnapshot.snapshot_time.desc(),
                AnalysisSnapshot.id.desc(),
            )
            .limit(max(1, int(limit)))
            .first()
        )

    @staticmethod
    def _parse_json_value(raw: Any, *, default: Any) -> Any:
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(str(raw or ""))
        except Exception:
            return default

    @classmethod
    def to_compact_payload(cls, row: AnalysisSnapshot) -> dict[str, Any]:
        payload_json = cls._parse_json_value(getattr(row, "payload_json", None), default={})
        support = getattr(row, "support_json", None)
        if not isinstance(support, list):
            support = payload_json.get("support") if isinstance(payload_json.get("support"), list) else []
        resistance = getattr(row, "resistance_json", None)
        if not isinstance(resistance, list):
            resistance = payload_json.get("resistance") if isinstance(payload_json.get("resistance"), list) else []

        timestamp = ""
        if isinstance(payload_json, dict):
            timestamp = str(payload_json.get("timestamp") or "").strip()
        if not timestamp:
            timestamp = row.snapshot_time.isoformat()

        schema_version = "analysis_snapshot.v1"
        if isinstance(payload_json, dict):
            schema_version = str(payload_json.get("schema_version") or schema_version).strip() or schema_version

        out = {
            "schema_version": schema_version,
            "symbol": str(row.symbol or "").strip(),
            "interval": str(row.interval or "").strip(),
            "timestamp": timestamp,
            "price": row.current_price,
            "trend": str(row.trend or "").strip(),
            "stance": str(getattr(row, "stance", "") or "").strip(),
            "support": support[:2],
            "resistance": resistance[:2],
        }
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}

    @staticmethod
    def get_snapshot_ref(row: AnalysisSnapshot) -> str:
        return str(getattr(row, "snapshot_id", "") or "").strip()
