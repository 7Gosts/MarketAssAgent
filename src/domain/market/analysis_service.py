"""Market analysis orchestration and native agent tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from config.runtime_config import get_postgres_dsn
from infrastructure.persistence.analysis_snapshot_repository import AnalysisSnapshotRepository
from utils.logging_utils import get_logger

from .facts import MarketFacts
from .indicators import (
    _calculate_fib_levels,
    _calculate_key_levels,
    _calculate_ma,
    _determine_trend,
    _get_ma_config,
    _merge_level_candidates,
    _classify_levels_by_price,
    _normalize_key_levels_by_price,
    _round_price,
    _safe_float,
)
from .structure import (
    _assess_structure_signals,
    _detect_swing_highs_v2,
    _detect_swing_lows_v2,
)

logger = get_logger(__name__)


def _extract_snapshot_key_levels(snapshot: dict[str, Any]) -> dict[str, list[Any]]:
    key_levels = snapshot.get("key_levels") if isinstance(snapshot.get("key_levels"), dict) else {}
    support: list[Any] = []
    resistance: list[Any] = []

    raw_support = key_levels.get("support")
    raw_resistance = key_levels.get("resistance")
    if isinstance(raw_support, list):
        support.extend(raw_support[:2])
    if isinstance(raw_resistance, list):
        resistance.extend(raw_resistance[:2])

    support_levels = snapshot.get("support_levels") if isinstance(snapshot.get("support_levels"), list) else []
    resistance_levels = snapshot.get("resistance_levels") if isinstance(snapshot.get("resistance_levels"), list) else []
    nearest_support = support_levels[0] if support_levels else None
    nearest_resistance = resistance_levels[0] if resistance_levels else None
    if nearest_support not in (None, "") and nearest_support not in support:
        support.insert(0, nearest_support)
    if nearest_resistance not in (None, "") and nearest_resistance not in resistance:
        resistance.insert(0, nearest_resistance)

    out = {
        "support": support[:2],
        "resistance": resistance[:2],
    }
    return {k: v for k, v in out.items() if v}


def _build_analysis_snapshot_payload_from_result(result: dict[str, Any]) -> dict[str, Any]:
    analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
    symbol = str(result.get("symbol") or analysis.get("symbol") or "").strip()
    interval = str(result.get("interval") or analysis.get("interval") or "").strip()
    timestamp = str(analysis.get("timestamp") or "").strip()
    ma_regime = str(analysis.get("ma_regime") or "").strip()
    price = analysis.get("current_price")
    if not symbol or not interval or not timestamp or not ma_regime or not isinstance(price, (int, float)):
        return {}

    key_levels = _extract_snapshot_key_levels(analysis)
    payload: dict[str, Any] = {
        "schema_version": "analysis_snapshot.v2",
        "symbol": symbol,
        "interval": interval,
        "timestamp": timestamp,
        "price": price,
        "ma_regime": ma_regime,
    }
    support = key_levels.get("support")
    resistance = key_levels.get("resistance")
    if support:
        payload["support"] = support[:2]
    if resistance:
        payload["resistance"] = resistance[:2]
    return payload


def _iter_analysis_snapshot_result_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    items = result.get("items") if isinstance(result.get("items"), list) else []
    return [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("status") == "success"
        and isinstance(item.get("analysis"), dict)
    ]


def _persist_analysis_snapshots(
    result: dict[str, Any],
    *,
    session_id: str,
    request_id: str,
) -> None:
    clean_session_id = str(session_id or "").strip()
    if not clean_session_id or not get_postgres_dsn():
        return

    repo: AnalysisSnapshotRepository | None = None
    try:
        repo = AnalysisSnapshotRepository()
        for row_result in _iter_analysis_snapshot_result_rows(result):
            snapshot_payload = _build_analysis_snapshot_payload_from_result(row_result)
            if not snapshot_payload:
                continue
            analysis = row_result.get("analysis") if isinstance(row_result.get("analysis"), dict) else {}
            saved, created = repo.create_if_missing(
                session_id=clean_session_id,
                request_id=request_id,
                snapshot_payload=snapshot_payload,
                raw_snapshot=analysis,
            )
            logger.info(
                "[analysis-snapshot] write db source=analyze_market session_id=%s request_id=%s snapshot_id=%s symbol=%s interval=%s action=%s",
                clean_session_id,
                request_id or "-",
                repo.get_snapshot_ref(saved) or "-",
                str(snapshot_payload.get("symbol") or "-"),
                str(snapshot_payload.get("interval") or "-"),
                "inserted" if created else "skip_existing",
            )
    except Exception as e:
        logger.warning(
            "[analysis-snapshot] write db failed source=analyze_market session_id=%s request_id=%s error=%s",
            clean_session_id,
            request_id or "-",
            e,
        )
    finally:
        if repo is not None:
            repo.close()


def _normalize_analysis_requests(requests: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in requests or []:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        interval = str(item.get("interval") or "").strip() or "1d"
        if not symbol:
            continue
        normalized.append({"symbol": symbol, "interval": interval})
    return normalized


def _build_analysis_request_key(symbol: str, interval: str) -> str:
    return f"{str(symbol or '').strip().upper()}@{str(interval or '').strip()}"


def _resolve_fib_position(*, current_price: float | None, fib_levels: dict[str, float]) -> str:
    if current_price is None:
        return "unknown"
    if not fib_levels:
        return "unknown"

    swing_high = _safe_float(fib_levels.get("swing_high"))
    swing_low = _safe_float(fib_levels.get("swing_low"))
    r236 = _safe_float(fib_levels.get("retracement_23.6%"))
    r382 = _safe_float(fib_levels.get("retracement_38.2%"))
    r500 = _safe_float(fib_levels.get("retracement_50.0%"))
    r618 = _safe_float(fib_levels.get("retracement_61.8%"))
    if None in (swing_high, swing_low, r236, r382, r500, r618):
        return "unknown"

    if current_price >= swing_high:
        return "above_swing_high"
    if current_price <= swing_low:
        return "below_swing_low"
    if current_price >= r236:
        return "0% ~ 23.6%"
    if current_price >= r382:
        return "23.6% ~ 38.2%"
    if current_price >= r500:
        return "38.2% ~ 50.0%"
    if current_price >= r618:
        return "50.0% ~ 61.8%"
    return "61.8% ~ 100%"


def _build_fib_v1(*, fib_levels: dict[str, float], current_price: float | None) -> dict[str, Any]:
    if not isinstance(fib_levels, dict) or not fib_levels:
        return {}
    levels = {
        "23.6%": _round_price(_safe_float(fib_levels.get("retracement_23.6%"))),
        "38.2%": _round_price(_safe_float(fib_levels.get("retracement_38.2%"))),
        "50.0%": _round_price(_safe_float(fib_levels.get("retracement_50.0%"))),
        "61.8%": _round_price(_safe_float(fib_levels.get("retracement_61.8%"))),
    }
    return {
        "swing_high": _round_price(_safe_float(fib_levels.get("swing_high"))),
        "swing_low": _round_price(_safe_float(fib_levels.get("swing_low"))),
        "levels": {k: v for k, v in levels.items() if v is not None},
        "current_zone": _resolve_fib_position(current_price=current_price, fib_levels=fib_levels),
    }


def _build_recent_klines_v1(
    *,
    klines: list[dict[str, Any]],
    lookback: int = 3,
) -> dict[str, Any]:
    rows = [k for k in klines if isinstance(k, dict)]
    if len(rows) < 2:
        return {"bars": [], "summary": []}

    recent = rows[-lookback:]
    all_volumes = [
        float(v)
        for v in [x.get("volume", x.get("成交量")) for x in rows]
        if isinstance(v, (int, float)) and float(v) > 0
    ]
    vol_base = sum(all_volumes[-20:]) / max(len(all_volumes[-20:]), 1) if all_volumes else 0.0

    bars: list[dict[str, Any]] = []
    summaries: list[str] = []

    for idx, bar in enumerate(recent):
        o = _safe_float(bar.get("open", bar.get("开盘")))
        h = _safe_float(bar.get("high", bar.get("最高")))
        l = _safe_float(bar.get("low", bar.get("最低")))
        c = _safe_float(bar.get("close", bar.get("收盘")))
        v = _safe_float(bar.get("volume", bar.get("成交量")))
        if None in (o, h, l, c):
            continue

        change_pct = ((c - o) / o * 100.0) if o else 0.0
        range_pct = ((h - l) / l * 100.0) if l else 0.0
        vol_ratio = (v / vol_base) if (v is not None and vol_base > 0) else None

        event = "inside"
        if idx > 0:
            prev = recent[idx - 1]
            prev_h = _safe_float(prev.get("high", prev.get("最高")))
            prev_l = _safe_float(prev.get("low", prev.get("最低")))
            if prev_h is not None and c > prev_h:
                event = "break_up"
            elif prev_l is not None and c < prev_l:
                event = "break_down"
            elif prev_h is not None and prev_l is not None and prev_l <= c <= prev_h:
                event = "inside"

        direction = "up" if c > o else ("down" if c < o else "flat")
        vol_tag = "normal"
        if vol_ratio is not None:
            if vol_ratio >= 1.2:
                vol_tag = "expanded"
            elif vol_ratio <= 0.8:
                vol_tag = "contracted"

        bars.append(
            {
                "open": _round_price(o),
                "high": _round_price(h),
                "low": _round_price(l),
                "close": _round_price(c),
                "change_pct": _round_price(change_pct),
                "range_pct": _round_price(range_pct),
                "direction": direction,
                "event": event,
                "volume_ratio": _round_price(vol_ratio),
                "volume_tag": vol_tag,
            }
        )
        summaries.append(
            f"最近K线{idx+1}: {direction} {change_pct:.2f}%, event={event}, volume={vol_tag}"
        )

    return {"bars": bars, "summary": summaries}


def _level_price_key(value: float | None) -> float | None:
    rounded = _round_price(value)
    return float(rounded) if rounded is not None else None


def _fib_source_label(key: str) -> str:
    raw = str(key or "")
    if raw.startswith("retracement_"):
        return f"斐波那契 {raw.replace('retracement_', '')} 回撤位"
    if raw == "swing_high":
        return "斐波那契摆动高点"
    if raw == "swing_low":
        return "斐波那契摆动低点"
    if raw.startswith("extension_"):
        return f"斐波那契 {raw.replace('extension_', '')} 扩展位"
    return "斐波那契位"


def _build_level_source_index(
    *,
    key_levels: dict[str, list[float]],
    fib_levels: dict[str, float],
) -> dict[float, list[dict[str, str]]]:
    out: dict[float, list[dict[str, str]]] = {}

    def add(price: Any, *, source_type: str, source_label: str) -> None:
        fv = _level_price_key(_safe_float(price))
        if fv is None:
            return
        item = {"source_type": source_type, "source_label": source_label}
        bucket = out.setdefault(fv, [])
        if item not in bucket:
            bucket.append(item)

    for val in key_levels.get("support", []) or []:
        add(val, source_type="fractal_level", source_label="分形关键位")
    for val in key_levels.get("resistance", []) or []:
        add(val, source_type="fractal_level", source_label="分形关键位")

    for key, val in (fib_levels or {}).items():
        if "retracement_" not in str(key):
            continue
        add(val, source_type="fib_retracement", source_label=_fib_source_label(str(key)))

    return out


def _level_detail(
    *,
    price: float | None,
    role: str,
    source_index: dict[float, list[dict[str, str]]],
) -> dict[str, Any] | None:
    price_key = _level_price_key(price)
    if price_key is None:
        return None
    sources = source_index.get(price_key) or [{"source_type": "level", "source_label": "关键位"}]
    return {
        "price": price_key,
        "role": role,
        "sources": sources,
        "primary_source": sources[0]["source_label"],
    }


def _extract_level_candidates(
    rows: list[dict[str, Any]],
    *,
    left: int = 2,
    right: int = 2,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for i, row in enumerate(rows):
        h = _safe_float(row.get("high", row.get("最高")))
        l = _safe_float(row.get("low", row.get("最低")))
        if h is None or l is None:
            continue

        if left <= i < len(rows) - right:
            is_high = all(
                h >= (_safe_float(rows[i - j].get("high", rows[i - j].get("最高"))) or h)
                for j in range(1, left + 1)
            ) and all(
                h >= (_safe_float(rows[i + j].get("high", rows[i + j].get("最高"))) or h)
                for j in range(1, right + 1)
            )
            if is_high:
                candidates.append({"price": h, "kind": "pivot_high", "source": "fractal"})

            is_low = all(
                l <= (_safe_float(rows[i - j].get("low", rows[i - j].get("最低"))) or l)
                for j in range(1, left + 1)
            ) and all(
                l <= (_safe_float(rows[i + j].get("low", rows[i + j].get("最低"))) or l)
                for j in range(1, right + 1)
            )
            if is_low:
                candidates.append({"price": l, "kind": "pivot_low", "source": "fractal"})

    # 最近 K 线的高低点能反映正在发生的反复测试，不等完全形成分形。
    for row in rows[-12:]:
        h = _safe_float(row.get("high", row.get("最高")))
        l = _safe_float(row.get("low", row.get("最低")))
        if h is not None:
            candidates.append({"price": h, "kind": "recent_high", "source": "recent_12"})
        if l is not None:
            candidates.append({"price": l, "kind": "recent_low", "source": "recent_12"})

    return candidates


def _cluster_level_candidates(
    candidates: list[dict[str, Any]],
    *,
    current_price: float,
) -> list[dict[str, Any]]:
    rows = [
        {**c, "price": _safe_float(c.get("price"))}
        for c in candidates
        if _safe_float(c.get("price")) is not None
    ]
    rows.sort(key=lambda x: float(x["price"]))
    if not rows:
        return []

    max_gap = max(current_price * 0.012, 1.0)
    max_width = max(current_price * 0.012, 1.0)
    split_rows = {
        "support": [x for x in rows if float(x["price"]) <= current_price],
        "resistance": [x for x in rows if float(x["price"]) >= current_price],
    }

    zones: list[dict[str, Any]] = []
    for role, role_rows in split_rows.items():
        if not role_rows:
            continue
        clusters: list[list[dict[str, Any]]] = [[role_rows[0]]]
        for item in role_rows[1:]:
            cluster = clusters[-1]
            prices_now = [float(x["price"]) for x in cluster]
            next_price = float(item["price"])
            would_width = max(max(prices_now), next_price) - min(min(prices_now), next_price)
            prev = cluster[-1]
            if abs(next_price - float(prev["price"])) <= max_gap and would_width <= max_width:
                cluster.append(item)
            else:
                clusters.append([item])

        for cluster in clusters:
            zones.append(_build_level_zone(cluster=cluster, role=role))
    return [z for z in zones if z]


def _build_level_zone(*, cluster: list[dict[str, Any]], role: str) -> dict[str, Any]:
    if not cluster:
        return {}

    prices = [float(x["price"]) for x in cluster]
    unique_prices = sorted({_level_price_key(x) for x in prices if _level_price_key(x) is not None})
    if len(unique_prices) < 2 and len(cluster) < 2:
        return {}

    low = min(prices)
    high = max(prices)
    center = sum(prices) / len(prices)
    touches = len(cluster)
    strength = "strong" if touches >= 5 else ("medium" if touches >= 3 else "weak")
    return {
        "low": _round_price(low),
        "high": _round_price(high),
        "center": _round_price(center),
        "role": role,
        "touches": touches,
        "strength": strength,
        "source": "pivot_cluster_50",
        "source_labels": sorted({str(x.get("source")) for x in cluster if x.get("source")}),
        "sample_prices": unique_prices[:8],
    }


def _build_level_zones_v1(
    *,
    klines: list[dict[str, Any]],
    current_price: float,
    fib_levels: dict[str, float],
    lookback: int = 50,
) -> dict[str, Any]:
    rows = [k for k in klines if isinstance(k, dict)]
    if len(rows) < 8:
        return {"lookback_bars": len(rows), "support_zones": [], "resistance_zones": []}

    recent = rows[-lookback:]
    candidates = _extract_level_candidates(recent)
    recent_highs = [_safe_float(k.get("high", k.get("最高"))) for k in recent]
    recent_lows = [_safe_float(k.get("low", k.get("最低"))) for k in recent]
    recent_highs = [x for x in recent_highs if x is not None]
    recent_lows = [x for x in recent_lows if x is not None]
    if recent_highs and recent_lows:
        low_bound, high_bound = min(recent_lows), max(recent_highs)
        for key, val in (fib_levels or {}).items():
            fv = _safe_float(val)
            if fv is not None and low_bound <= fv <= high_bound:
                candidates.append({"price": fv, "kind": str(key), "source": _fib_source_label(str(key))})

    zones = _cluster_level_candidates(candidates, current_price=current_price)
    support_zones = sorted(
        [z for z in zones if z.get("role") == "support"],
        key=lambda z: float(z.get("center") or 0),
        reverse=True,
    )
    resistance_zones = sorted(
        [z for z in zones if z.get("role") == "resistance"],
        key=lambda z: float(z.get("center") or 0),
    )
    return {
        "lookback_bars": len(recent),
        "support_zones": support_zones[:3],
        "resistance_zones": resistance_zones[:3],
    }


def _build_level_facts(
    *,
    current_price: float,
    key_levels: dict[str, list[float]],
    fib_levels: dict[str, float],
) -> dict[str, Any]:
    all_levels = _merge_level_candidates(key_levels, fib_levels)
    supports_all, resistances_all = _classify_levels_by_price(levels=all_levels, current_price=current_price)
    nearest_support = supports_all[0] if supports_all else None
    nearest_resistance = resistances_all[0] if resistances_all else None

    source_index = _build_level_source_index(key_levels=key_levels, fib_levels=fib_levels)
    support_details = [
        x
        for x in (
            _level_detail(price=price, role="support", source_index=source_index)
            for price in supports_all[:2]
        )
        if x is not None
    ]
    resistance_details = [
        x
        for x in (
            _level_detail(price=price, role="resistance", source_index=source_index)
            for price in resistances_all[:2]
        )
        if x is not None
    ]

    return {
        "nearest_support": _round_price(nearest_support),
        "nearest_resistance": _round_price(nearest_resistance),
        "support_levels": [_round_price(x) for x in supports_all[:2]],
        "resistance_levels": [_round_price(x) for x in resistances_all[:2]],
        "nearest_support_detail": support_details[0] if support_details else None,
        "nearest_resistance_detail": resistance_details[0] if resistance_details else None,
        "level_details": {
            "support": support_details,
            "resistance": resistance_details,
        },
        "distance_to_support_pct": _round_price(((current_price - nearest_support) / current_price * 100) if nearest_support else None),
        "distance_to_resistance_pct": _round_price(((nearest_resistance - current_price) / current_price * 100) if nearest_resistance else None),
    }


def _ma_regime(trend: str) -> str:
    return {"偏多": "bullish", "偏空": "bearish"}.get(str(trend), "mixed")


def _build_price_vs_ma(*, current_price: float, ma_values: dict[str, float | None]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for label, key in (("short", "MA_short"), ("mid", "MA_mid"), ("long", "MA_long")):
        value = ma_values.get(key)
        if value is None:
            continue
        position = "above" if current_price > value else ("below" if current_price < value else "equal")
        out[label] = {
            "position": position,
            "distance_pct": _round_price((current_price - value) / value * 100) if value else None,
        }
    return out


def _build_ma_slopes_pct(*, closes: list[float], ma_config: dict[str, int]) -> dict[str, float]:
    out: dict[str, float] = {}
    for label in ("short", "mid", "long"):
        period = int(ma_config[label])
        current = _calculate_ma(closes, period)
        previous = _calculate_ma(closes[:-1], period)
        if current is None or previous in (None, 0):
            continue
        out[label] = _round_price((current - previous) / previous * 100) or 0.0
    return out


def _sequence_direction(values: list[float]) -> str:
    if len(values) < 2:
        return "insufficient"
    if values[-1] > values[-2]:
        return "higher"
    if values[-1] < values[-2]:
        return "lower"
    return "equal"


def _build_swing_facts(klines: list[dict[str, Any]]) -> tuple[str, dict[str, list[float]]]:
    swing_highs = _detect_swing_highs_v2(klines)
    swing_lows = _detect_swing_lows_v2(klines)
    highs = [float(item["price"]) for item in swing_highs[-3:] if isinstance(item.get("price"), (int, float))]
    lows = [float(item["price"]) for item in swing_lows[-3:] if isinstance(item.get("price"), (int, float))]
    high_direction = _sequence_direction(highs)
    low_direction = _sequence_direction(lows)
    if high_direction == "higher" and low_direction == "higher":
        label = "higher_highs_higher_lows"
    elif high_direction == "lower" and low_direction == "lower":
        label = "lower_highs_lower_lows"
    elif low_direction == "higher":
        label = "higher_lows"
    elif high_direction == "lower":
        label = "lower_highs"
    elif high_direction == "insufficient" and low_direction == "insufficient":
        label = "insufficient"
    else:
        label = "mixed"
    return label, {"recent_highs": highs, "recent_lows": lows}


def _build_volume_facts(klines: list[dict[str, Any]]) -> tuple[str, float | None]:
    volumes = [
        float(value)
        for value in (row.get("volume", row.get("成交量")) for row in klines if isinstance(row, dict))
        if isinstance(value, (int, float)) and float(value) > 0
    ]
    if len(volumes) < 6:
        return "unavailable", None
    recent = sum(volumes[-3:]) / 3
    baseline_values = volumes[-23:-3]
    baseline = sum(baseline_values) / len(baseline_values) if baseline_values else 0.0
    if baseline <= 0:
        return "unavailable", None
    ratio = recent / baseline
    state = "expanding" if ratio >= 1.2 else ("contracting" if ratio <= 0.8 else "stable")
    return state, _round_price(ratio)


def _build_range_position(*, klines: list[dict[str, Any]], current_price: float) -> float | None:
    recent = [row for row in klines[-30:] if isinstance(row, dict)]
    highs = [_safe_float(row.get("high", row.get("最高"))) for row in recent]
    lows = [_safe_float(row.get("low", row.get("最低"))) for row in recent]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    if not highs or not lows:
        return None
    high = max(highs)
    low = min(lows)
    if high <= low:
        return None
    return round(max(0.0, min(1.0, (current_price - low) / (high - low))), 4)


# ── 核心工具 ──

def _perform_market_analysis(
    symbol: str,
    interval: str = "1d",
    force_refresh: bool = False,
    request_key: str | None = None,
) -> Dict[str, Any]:
    """内部完整分析，供统一行情分析工具复用。"""
    logger.info("开始分析 %s %s 周期", symbol, interval)

    from tools.market_data import fetch_market_data

    raw = fetch_market_data(symbol=symbol, interval=interval)
    resolved_symbol = str(raw.get("symbol") or symbol).strip() or symbol
    item_key = request_key or _build_analysis_request_key(resolved_symbol, interval)
    if "error" in raw:
        return {
            "status": "error",
            "request_key": item_key,
            "symbol": resolved_symbol,
            "interval": interval,
            "message": raw.get("error", "数据获取失败"),
        }

    klines = raw.get("data", [])
    if not klines:
        return {
            "status": "error",
            "request_key": item_key,
            "symbol": resolved_symbol,
            "interval": interval,
            "message": "无 K 线数据",
        }

    closes = [k.get("close", k.get("收盘", 0)) for k in klines]
    closes = [float(c) for c in closes if isinstance(c, (int, float)) and c > 0]
    if not closes:
        return {
            "status": "error",
            "request_key": item_key,
            "symbol": resolved_symbol,
            "interval": interval,
            "message": "收盘价数据为空",
        }

    ma_config = _get_ma_config(resolved_symbol, market=str(raw.get("market") or ""))
    ma_values: dict[str, float | None] = {}
    for name, period in [
        ("MA_short", ma_config["short"]),
        ("MA_mid", ma_config["mid"]),
        ("MA_long", ma_config["long"]),
    ]:
        ma_values[name] = _calculate_ma(closes, period)

    trend = _determine_trend(closes, ma_values)
    raw_key_levels = _calculate_key_levels(klines)
    key_levels = _normalize_key_levels_by_price(
        key_levels=raw_key_levels,
        current_price=closes[-1],
    )
    structure_signals = _assess_structure_signals(trend, ma_values, key_levels)
    recent_for_fib = klines[-30:] if len(klines) > 30 else klines
    fib_highs = [k.get("high", k.get("最高", 0)) for k in recent_for_fib if k.get("high") or k.get("最高")]
    fib_lows = [k.get("low", k.get("最低", 0)) for k in recent_for_fib if k.get("low") or k.get("最低")]
    fib_levels = _calculate_fib_levels(max(fib_highs), min(fib_lows)) if fib_highs and fib_lows else {}
    fib_v1 = _build_fib_v1(fib_levels=fib_levels, current_price=closes[-1])
    level_facts = _build_level_facts(
        current_price=closes[-1],
        key_levels=key_levels,
        fib_levels=fib_levels,
    )
    recent_klines_v1 = _build_recent_klines_v1(klines=klines, lookback=3)
    recent_candles = list(recent_klines_v1.get("bars") or [])[:3]
    level_zones_v1 = _build_level_zones_v1(
        klines=klines,
        current_price=closes[-1],
        fib_levels=fib_levels,
        lookback=50,
    )

    swing_structure, swing_points = _build_swing_facts(klines)
    volume_state, volume_ratio = _build_volume_facts(klines)
    facts = MarketFacts(
        symbol=resolved_symbol,
        interval=interval,
        timestamp=datetime.now().isoformat(),
        current_price=closes[-1],
        ma_regime=_ma_regime(trend),
        ma_alignment=structure_signals.get("ma_alignment", "mixed"),
        ma_periods=dict(ma_config),
        ma_values={
            "short": ma_values["MA_short"],
            "mid": ma_values["MA_mid"],
            "long": ma_values["MA_long"],
        },
        price_vs_ma=_build_price_vs_ma(current_price=closes[-1], ma_values=ma_values),
        ma_slopes_pct=_build_ma_slopes_pct(closes=closes, ma_config=ma_config),
        swing_structure=swing_structure,
        swing_points=swing_points,
        support_levels=level_facts.get("support_levels", []),
        resistance_levels=level_facts.get("resistance_levels", []),
        distance_to_levels_pct={
            "to_support_pct": level_facts.get("distance_to_support_pct"),
            "to_resistance_pct": level_facts.get("distance_to_resistance_pct"),
        },
        level_details=level_facts.get("level_details", {}),
        volume_state=volume_state,
        volume_ratio=volume_ratio,
        recent_candles=recent_candles,
        range_position=_build_range_position(klines=klines, current_price=closes[-1]),
        fib_v1=fib_v1,
        level_zones_v1=level_zones_v1,
        requested_symbol=symbol if resolved_symbol != symbol else None,
        resolution=raw.get("resolution") if isinstance(raw.get("resolution"), dict) else None,
        request_key=item_key,
    )

    return {
        "status": "success",
        "request_key": item_key,
        "symbol": resolved_symbol,
        "interval": interval,
        "analysis": facts.model_dump(mode="json", exclude_none=True),
        "message": f"{resolved_symbol} {interval} 行情事实计算完成",
    }


def analyze_market(
    symbol: str | None = None,
    interval: str = "1d",
    force_refresh: bool = False,
    requests: list[dict[str, Any]] | None = None,
    session_id: str = "",
    request_id: str = "",
) -> Dict[str, Any]:
    """【核心工具】统一行情分析入口 — 支持单标的与多请求

    Args:
        symbol: 单标的代码 (e.g. BTCUSDT, 600519.SH, NVDA, AU9999)
        interval: 单标的时间周期 (1m, 5m, 15m, 1h, 4h, 1d, 1w)。
            用户未指定周期时，调用方必须默认加密货币=4h，股票/港股/美股/黄金/其他非加密=1d。
        force_refresh: 是否强制刷新数据
        requests: 多请求列表
            例如：[{"symbol": "SOLUSDT", "interval": "1h"}, {"symbol": "SOLUSDT", "interval": "4h"}]

    Returns:
        返回统一批量 schema：status/items/message。单标的是 items 中的一个元素。
    """
    normalized_requests = _normalize_analysis_requests(
        requests if requests else [{"symbol": symbol, "interval": interval}]
    )
    if not normalized_requests:
        return {
            "status": "error",
            "items": [],
            "message": "请提供 symbol，或提供 requests 进行多请求分析",
        }
    if len(normalized_requests) > 10:
        return {"status": "error", "items": [], "message": "一次最多分析 10 个请求"}

    items: list[dict[str, Any]] = []
    for item in normalized_requests:
        request_key = _build_analysis_request_key(item["symbol"], item["interval"])
        items.append(
            _perform_market_analysis(
                item["symbol"],
                item["interval"],
                force_refresh=force_refresh,
                request_key=request_key,
            )
        )

    success_count = len([item for item in items if item.get("status") == "success"])
    result = {
        "status": "success" if success_count else "error",
        "items": items,
        "message": (
            f"已完成 {success_count} 个分析请求"
            if success_count == len(items)
            else f"已完成 {success_count}/{len(items)} 个分析请求"
        ),
    }
    if not success_count:
        result["message"] = "行情分析失败"

    _persist_analysis_snapshots(result, session_id=session_id, request_id=request_id)
    return result


def get_key_levels(symbol: str, interval: str = "1d") -> Dict[str, Any]:
    """获取关键支撑/阻力位（基于分形方法）

    Args:
        symbol: 标的代码
        interval: 时间周期

    Returns:
        支撑位和阻力位列表
    """
    from tools.market_data import fetch_market_data
    symbol_clean = str(symbol or "").strip()
    raw = fetch_market_data(symbol=symbol_clean, interval=interval)
    resolved_symbol = str(raw.get("symbol") or symbol_clean).strip() or symbol_clean
    if "error" in raw:
        return {"symbol": resolved_symbol, "support_levels": [], "resistance_levels": [],
                "message": f"数据获取失败: {raw.get('error')}"}

    klines = raw.get("data", [])
    if not klines:
        return {
            "symbol": resolved_symbol,
            "support_levels": [],
            "resistance_levels": [],
            "message": "无 K 线数据",
        }

    key_levels = _calculate_key_levels(klines)
    closes = [k.get("close", k.get("收盘", 0)) for k in klines]
    closes = [float(c) for c in closes if isinstance(c, (int, float)) and c > 0]
    if closes:
        key_levels = _normalize_key_levels_by_price(
            key_levels=key_levels,
            current_price=closes[-1],
        )

    return {
        "symbol": resolved_symbol,
        "support_levels": key_levels.get("support", []),
        "resistance_levels": key_levels.get("resistance", []),
        "message": "基于实时数据计算的关键位",
    }


def evaluate_structure(symbol: str, snapshot: Optional[Dict] = None) -> Dict[str, Any]:
    """评估当前市场结构（123法则、均线、量价等）

    Args:
        symbol: 标的代码
        snapshot: 可选的 AnalysisSnapshot 数据（追问场景使用）

    Returns:
        结构分析摘要
    """
    if snapshot:
        return {
            "symbol": symbol,
            "ma_regime": str(snapshot.get("ma_regime") or "mixed"),
            "swing_structure": str(snapshot.get("swing_structure") or "insufficient"),
            "swing_points": snapshot.get("swing_points") if isinstance(snapshot.get("swing_points"), dict) else {},
            "message": "基于 Snapshot 的结构事实",
        }

    # 否则重新获取数据
    from tools.market_data import fetch_market_data
    raw = fetch_market_data(symbol=symbol, interval="1d")
    if "error" in raw:
        return {"symbol": symbol, "structure_summary": "数据获取失败",
                "message": raw.get("error")}

    klines = raw.get("data", [])
    closes = [k.get("close", k.get("收盘", 0)) for k in klines if k.get("close") or k.get("收盘")]
    closes = [c for c in closes if c and c > 0]

    ma_config = _get_ma_config(symbol, market=str(raw.get("market") or ""))
    ma_values = {}
    for name, period in [("MA_short", ma_config["short"]), ("MA_mid", ma_config["mid"]), ("MA_long", ma_config["long"])]:
        ma_values[name] = _calculate_ma(closes, period)

    trend = _determine_trend(closes, ma_values)
    swing_structure, swing_points = _build_swing_facts(klines)

    return {
        "symbol": symbol,
        "ma_regime": _ma_regime(trend),
        "swing_structure": swing_structure,
        "swing_points": swing_points,
        "message": "基于真实数据的结构事实计算完成",
    }

def analyze_fibonacci(
    symbol: str,
    interval: str = "1d",
    swing_high: float | None = None,
    swing_low: float | None = None,
) -> Dict[str, Any]:
    """斐波那契回撤与扩展分析（基于最近 swing high/low）

    仅在用户明确要求「斐波那契/回撤位/扩展位」时使用；常规行情分析优先 analyze_market。
    """
    from tools.market_data import fetch_market_data

    raw = fetch_market_data(symbol=symbol, interval=interval)
    if "error" in raw:
        return {"symbol": symbol, "status": "error", "message": raw.get("error")}

    klines = raw.get("data", [])
    if len(klines) < 5:
        return {"symbol": symbol, "status": "error", "message": "K 线数据不足，无法计算斐波那契"}

    if swing_high is not None and swing_low is not None:
        fib = _calculate_fib_levels(swing_high, swing_low)
        current_price = klines[-1].get("close") if isinstance(klines[-1], dict) else None
        return {
            "symbol": symbol,
            "interval": interval,
            "fib_levels": fib,
            "current_price": current_price,
            "source": "manual_swing",
            "message": "基于手动指定的 swing high/low 计算",
        }

    recent = klines[-30:] if len(klines) > 30 else klines
    highs = [float(k.get("high", 0)) for k in recent if isinstance(k, dict) and k.get("high")]
    lows = [float(k.get("low", 0)) for k in recent if isinstance(k, dict) and k.get("low")]
    if not highs or not lows:
        return {"symbol": symbol, "status": "error", "message": "K 线高低点数据不足"}

    auto_high = max(highs)
    auto_low = min(lows)
    fib = _calculate_fib_levels(auto_high, auto_low)
    current_price = float(klines[-1].get("close", 0)) if isinstance(klines[-1], dict) else None

    position = "unknown"
    if current_price:
        position = _resolve_fib_position(current_price=current_price, fib_levels=fib)

    return {
        "symbol": symbol,
        "interval": interval,
        "fib_levels": fib,
        "current_price": current_price,
        "current_position": position,
        "source": "auto_swing_from_klines",
        "message": f"基于最近 {len(recent)} 根 K 线的极值计算斐波那契水平",
    }
