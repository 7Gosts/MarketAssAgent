"""Market analysis orchestration and LangChain tools."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
import json

from langchain_core.tools import tool

from infrastructure.memory.snapshot import snapshot_manager
from utils.logging_utils import get_logger

from .indicators import (
    _analyze_structure,
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
from .patterns import _build_pattern_detection_v2
from .structure import (
    _assess_structure_signals,
    _build_market_structure_v2,
    _detect_swing_highs_v2,
    _detect_swing_lows_v2,
    _format_structure_note,
    _structure_signal_rank,
)

logger = get_logger(__name__)


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


def _build_trade_snapshot_v1(
    *,
    current_price: float,
    trend: str,
    key_levels: dict[str, list[float]],
    fib_levels: dict[str, float],
    structure_signals: dict[str, Any],
) -> dict[str, Any]:
    all_levels = _merge_level_candidates(key_levels, fib_levels)
    supports_all, resistances_all = _classify_levels_by_price(levels=all_levels, current_price=current_price)
    nearest_support = supports_all[0] if supports_all else None
    nearest_resistance = resistances_all[0] if resistances_all else None

    second_support = supports_all[1] if len(supports_all) > 1 else None
    second_resistance = resistances_all[1] if len(resistances_all) > 1 else None

    levels_v2 = {
        "nearest_support": _round_price(nearest_support),
        "nearest_resistance": _round_price(nearest_resistance),
        "support_levels": [_round_price(x) for x in supports_all[:2]],
        "resistance_levels": [_round_price(x) for x in resistances_all[:2]],
        "distance_to_support_pct": _round_price(((current_price - nearest_support) / current_price * 100) if nearest_support else None),
        "distance_to_resistance_pct": _round_price(((nearest_resistance - current_price) / current_price * 100) if nearest_resistance else None),
    }

    trigger: dict[str, Any] = {
        "side": "wait",
        "entry": None,
        "stop": None,
        "tp1": None,
        "tp2": None,
        "triggered": False,
    }
    invalidation: dict[str, Any] = {"stop": None, "time_stop_rule": "若 3 根同周期K线未延续则失效"}

    if trend == "偏多":
        entry = nearest_support or current_price
        stop = second_support or (entry * 0.985 if entry else None)
        tp1 = nearest_resistance
        tp2 = second_resistance
        trigger.update(
            {
                "side": "long",
                "entry": _round_price(entry),
                "stop": _round_price(stop),
                "tp1": _round_price(tp1),
                "tp2": _round_price(tp2),
                "triggered": bool(structure_signals.get("trend_ma_match")),
            }
        )
        invalidation["stop"] = _round_price(stop)
    elif trend == "偏空":
        entry = nearest_resistance or current_price
        stop = second_resistance or (entry * 1.015 if entry else None)
        tp1 = nearest_support
        tp2 = second_support
        trigger.update(
            {
                "side": "short",
                "entry": _round_price(entry),
                "stop": _round_price(stop),
                "tp1": _round_price(tp1),
                "tp2": _round_price(tp2),
                "triggered": bool(structure_signals.get("trend_ma_match")),
            }
        )
        invalidation["stop"] = _round_price(stop)

    risk_flags: list[str] = []
    if str(structure_signals.get("trend_clarity")) == "range_bound":
        risk_flags.append("regime:range_bound")
    if not bool(structure_signals.get("trend_ma_match")):
        risk_flags.append("signal:trend_ma_mismatch")
    if not levels_v2["nearest_support"] or not levels_v2["nearest_resistance"]:
        risk_flags.append("levels:insufficient")
    if not risk_flags:
        risk_flags.append("normal")

    actionability = {
        "can_trade_now": bool(trigger.get("triggered")) and trend in ("偏多", "偏空"),
        "bias": "long" if trend == "偏多" else ("short" if trend == "偏空" else "wait"),
        "why": "趋势与均线一致，且存在可执行价位" if bool(trigger.get("triggered")) else "结构未充分确认，优先等待触发",
        "wait_condition": "等待价格触及最近关键位并出现同向确认",
    }

    return {
        "levels_v2": levels_v2,
        "trigger_conditions": trigger,
        "invalidation_conditions": invalidation,
        "risk_flags": risk_flags,
        "actionability": actionability,
    }


def _to_compact_summary_v1(analysis_result: dict[str, Any]) -> dict[str, Any]:
    levels_v2 = analysis_result.get("levels_v2") if isinstance(analysis_result.get("levels_v2"), dict) else {}
    actionability = analysis_result.get("actionability") if isinstance(analysis_result.get("actionability"), dict) else {}
    risk_flags = analysis_result.get("risk_flags") if isinstance(analysis_result.get("risk_flags"), list) else []
    market_structure = (
        analysis_result.get("market_structure_v2")
        if isinstance(analysis_result.get("market_structure_v2"), dict)
        else (
            analysis_result.get("market_structure_v1")
            if isinstance(analysis_result.get("market_structure_v1"), dict)
            else {}
        )
    )
    pattern_detection = (
        analysis_result.get("pattern_detection_v2")
        if isinstance(analysis_result.get("pattern_detection_v2"), dict)
        else (
            analysis_result.get("pattern_detection_v1")
            if isinstance(analysis_result.get("pattern_detection_v1"), dict)
            else {}
        )
    )
    summary = {
        "symbol": analysis_result.get("symbol"),
        "interval": analysis_result.get("interval"),
        "timestamp": analysis_result.get("timestamp"),
        "current_price": analysis_result.get("current_price"),
        "trend": analysis_result.get("trend"),
        "nearest_support": levels_v2.get("nearest_support"),
        "nearest_resistance": levels_v2.get("nearest_resistance"),
        "bias": actionability.get("bias"),
        "can_trade_now": actionability.get("can_trade_now"),
        "wait_condition": actionability.get("wait_condition"),
        "risk_flags": risk_flags[:3],
        "structure_label": market_structure.get("structure_label"),
        "pattern_name": pattern_detection.get("primary_pattern"),
        "pattern_confidence": pattern_detection.get("confidence"),
        "range_width_pct": ((market_structure.get("current_range") or {}).get("width_pct") if isinstance(market_structure.get("current_range"), dict) else None),
        "top_evidence": (market_structure.get("evidence") or [])[:2],
        "summary_line": analysis_result.get("raw_insights"),
        # 这组字段保留在 full analysis 中，但默认不建议写入记忆主干。
        "omit_candidates": [
            "market_structure_v2.swing_highs",
            "market_structure_v2.swing_lows",
            "market_structure_v2.battle_zones",
            "pattern_detection_v2.invalid_conditions",
            "trigger_conditions.tp2",
            "invalidation_conditions.time_stop_rule",
        ],
    }
    return {k: v for k, v in summary.items() if v not in (None, "", [], {})}


def _safe_json_len(obj: Any) -> int:
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str))
    except Exception:
        return 0


def _build_output_meta_v1(*, analysis_result: dict[str, Any], compact_summary_v1: dict[str, Any]) -> dict[str, Any]:
    return {
        "analysis_field_count": len(analysis_result.keys()),
        "compact_field_count": len(compact_summary_v1.keys()),
        "analysis_chars": _safe_json_len(analysis_result),
        "compact_chars": _safe_json_len(compact_summary_v1),
        "compression_ratio": round(
            (_safe_json_len(compact_summary_v1) / max(_safe_json_len(analysis_result), 1)),
            4,
        ),
    }


# ── 核心工具 ──

def _perform_market_analysis(
    symbol: str,
    interval: str = "1d",
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """内部完整分析，供统一行情分析工具复用。"""
    logger.info("开始分析 %s %s 周期", symbol, interval)

    from tools.market_data import fetch_market_data

    raw = fetch_market_data.invoke({"symbol": symbol, "interval": interval})
    resolved_symbol = str(raw.get("symbol") or symbol).strip() or symbol
    if "error" in raw:
        return {
            "status": "error",
            "symbol": resolved_symbol,
            "interval": interval,
            "message": raw.get("error", "数据获取失败"),
        }

    klines = raw.get("data", [])
    if not klines:
        return {
            "status": "error",
            "symbol": resolved_symbol,
            "interval": interval,
            "message": "无 K 线数据",
        }

    highs_all = [k.get("high", k.get("最高", 0)) for k in klines]
    lows_all = [k.get("low", k.get("最低", 0)) for k in klines]
    closes = [k.get("close", k.get("收盘", 0)) for k in klines]
    highs_all = [float(v) for v in highs_all if isinstance(v, (int, float)) and v > 0]
    lows_all = [float(v) for v in lows_all if isinstance(v, (int, float)) and v > 0]
    closes = [float(c) for c in closes if isinstance(c, (int, float)) and c > 0]
    if not closes:
        return {
            "status": "error",
            "symbol": resolved_symbol,
            "interval": interval,
            "message": "收盘价数据为空",
        }

    ma_config = _get_ma_config(resolved_symbol)
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
    structure_result = _analyze_structure(klines, ma_values)
    structure_summary = (
        structure_result.get("summary", "") if isinstance(structure_result, dict) else str(structure_result)
    )
    structure_signals = _assess_structure_signals(trend, ma_values, key_levels)
    structure_note = _format_structure_note(structure_signals)

    recent_for_fib = klines[-30:] if len(klines) > 30 else klines
    fib_highs = [k.get("high", k.get("最高", 0)) for k in recent_for_fib if k.get("high") or k.get("最高")]
    fib_lows = [k.get("low", k.get("最低", 0)) for k in recent_for_fib if k.get("low") or k.get("最低")]
    fib_levels = _calculate_fib_levels(max(fib_highs), min(fib_lows)) if fib_highs and fib_lows else {}
    trade_snapshot = _build_trade_snapshot_v1(
        current_price=closes[-1],
        trend=trend,
        key_levels=key_levels,
        fib_levels=fib_levels,
        structure_signals=structure_signals,
    )
    volumes = [k.get("volume", k.get("成交量", 0)) for k in klines if k.get("volume") or k.get("成交量")]
    volumes = [float(v) for v in volumes if isinstance(v, (int, float)) and v > 0]
    swing_highs = _detect_swing_highs_v2(klines, window=5)
    swing_lows = _detect_swing_lows_v2(klines, window=5)
    latest_bar = klines[-1] if isinstance(klines[-1], dict) else {}
    last_high = _safe_float(latest_bar.get("high", latest_bar.get("最高")))
    last_low = _safe_float(latest_bar.get("low", latest_bar.get("最低")))
    last_close = _safe_float(latest_bar.get("close", latest_bar.get("收盘")))
    market_structure_v2 = _build_market_structure_v2(
        symbol=resolved_symbol,
        interval=interval,
        current_price=closes[-1],
        trend=trend,
        swing_highs=swing_highs,
        swing_lows=swing_lows,
        closes=closes,
        highs=highs_all,
        lows=lows_all,
        volumes=volumes,
        last_high=last_high,
        last_low=last_low,
        last_close=last_close,
    )
    pattern_detection_v2 = _build_pattern_detection_v2(
        market_structure_v2=market_structure_v2,
        levels_v2=trade_snapshot.get("levels_v2", {}),
    )
    recent_klines_v1 = _build_recent_klines_v1(klines=klines, lookback=3)
    recent_kline_summary = list(recent_klines_v1.get("summary") or [])
    if not (market_structure_v2.get("evidence") or []) and recent_kline_summary:
        market_structure_v2["evidence"] = recent_kline_summary[:2]

    analysis_result = {
        "symbol": resolved_symbol,
        "interval": interval,
        "timestamp": datetime.now().isoformat(),
        "current_price": closes[-1],
        "trend": trend,
        "levels_v2": trade_snapshot.get("levels_v2", {}),
        "trigger_conditions": trade_snapshot.get("trigger_conditions", {}),
        "invalidation_conditions": trade_snapshot.get("invalidation_conditions", {}),
        "risk_flags": trade_snapshot.get("risk_flags", []),
        "actionability": trade_snapshot.get("actionability", {}),
        "market_structure_v2": market_structure_v2,
        "pattern_detection_v2": pattern_detection_v2,
        "recent_klines_v1": recent_klines_v1,
        "raw_insights": f"{resolved_symbol} 在 {interval} 周期呈{trend}结构，{structure_note}。",
    }
    if resolved_symbol != symbol:
        analysis_result["requested_symbol"] = symbol
    if isinstance(raw.get("resolution"), dict):
        analysis_result["resolution"] = raw.get("resolution")
    compact_summary_v1 = _to_compact_summary_v1(analysis_result)
    output_meta_v1 = _build_output_meta_v1(
        analysis_result=analysis_result,
        compact_summary_v1=compact_summary_v1,
    )

    snapshot = snapshot_manager.save_snapshot(
        session_id="default",
        snapshot_data=analysis_result,
    )

    return {
        "status": "success",
        "symbol": resolved_symbol,
        "interval": interval,
        "analysis": analysis_result,
        "compact_summary_v1": compact_summary_v1,
        "output_meta_v1": output_meta_v1,
        "snapshot": snapshot,
        "message": f"{resolved_symbol} {interval} 技术分析完成: {trend}，{structure_note}",
    }


def _analyze_multiple_markets(
    symbol_interval_map: dict[str, str],
    *,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """统一处理多标的行情分析。"""
    if not symbol_interval_map:
        return {"status": "error", "message": "未提供标的列表"}

    if len(symbol_interval_map) > 10:
        return {"status": "error", "message": "一次最多分析 10 个标的"}

    results: dict[str, Any] = {}
    for sym, interval in symbol_interval_map.items():
        sym_upper = str(sym or "").strip().upper()
        interval_clean = str(interval or "").strip() or "1d"
        if not sym_upper:
            continue
        results[sym_upper] = _perform_market_analysis(
            sym_upper,
            interval_clean,
            force_refresh=force_refresh,
        )

    if not results:
        return {"status": "error", "message": "标的列表为空或格式无效"}

    comparison = _compare_symbols(results)
    comparison_brief_v1 = _build_comparison_brief_v1(comparison)
    analyses_chars = _safe_json_len(results)
    brief_chars = _safe_json_len(comparison_brief_v1)

    return {
        "status": "success",
        "symbols": list(results.keys()),
        "analyses": results,
        "comparison": comparison,
        "comparison_brief_v1": comparison_brief_v1,
        "output_meta_v1": {
            "symbol_count": len(results),
            "analyses_chars": analyses_chars,
            "comparison_brief_chars": brief_chars,
            "compression_ratio": round(brief_chars / max(analyses_chars, 1), 4),
        },
        "message": f"已完成 {len(results)} 个标的的混合周期对比分析",
    }


@tool
def analyze_market(
    symbol: str | None = None,
    interval: str = "1d",
    force_refresh: bool = False,
    symbol_interval_map: dict[str, str] | None = None,
) -> Dict[str, Any]:
    """【核心工具】统一行情分析入口 — 支持单标的与多标的

    Args:
        symbol: 单标的代码 (e.g. BTCUSDT, 600519.SH, NVDA, AU9999)
        interval: 单标的时间周期 (1m, 5m, 15m, 1h, 4h, 1d, 1w)
        force_refresh: 是否强制刷新数据
        symbol_interval_map: 多标的与周期映射
            例如：{"ETHUSDT": "4h", "SOLUSDT": "4h", "AU9999": "1d"}

    Returns:
        单标的时返回详细分析结果 + Snapshot；
        多标的时返回每个标的的分析结果 + 横向对比
    """
    if symbol_interval_map:
        return _analyze_multiple_markets(
            symbol_interval_map,
            force_refresh=force_refresh,
        )

    symbol_clean = str(symbol or "").strip()
    if not symbol_clean:
        return {
            "status": "error",
            "message": "请提供 symbol，或提供 symbol_interval_map 进行多标的分析",
        }
    return _perform_market_analysis(symbol_clean, interval, force_refresh=force_refresh)


@tool
def get_key_levels(symbol: str, interval: str = "1d") -> Dict[str, Any]:
    """获取关键支撑/阻力位（基于分形方法）

    Args:
        symbol: 标的代码
        interval: 时间周期

    Returns:
        支撑位和阻力位列表
    """
    # 先尝试从 snapshot 获取（追问场景）
    snapshot = snapshot_manager.get_latest_snapshot("default")
    if snapshot and snapshot.get("symbol") == symbol:
        levels_v2 = snapshot.get("levels_v2", {}) if isinstance(snapshot.get("levels_v2"), dict) else {}
        return {
            "symbol": symbol,
            "support_levels": levels_v2.get("support_levels", []) or [],
            "resistance_levels": levels_v2.get("resistance_levels", []) or [],
            "message": "从上次分析快照中获取关键位",
        }

    # 否则重新获取数据计算
    from tools.market_data import fetch_market_data
    raw = fetch_market_data.invoke({"symbol": symbol, "interval": interval})
    if "error" in raw:
        return {"symbol": symbol, "support_levels": [], "resistance_levels": [],
                "message": f"数据获取失败: {raw.get('error')}"}

    klines = raw.get("data", [])
    key_levels = _calculate_key_levels(klines)

    return {
        "symbol": symbol,
        "support_levels": key_levels.get("support", []),
        "resistance_levels": key_levels.get("resistance", []),
        "message": "基于分形方法计算的关键位",
    }


@tool
def evaluate_structure(symbol: str, snapshot: Optional[Dict] = None) -> Dict[str, Any]:
    """评估当前市场结构（123法则、均线、量价等）

    Args:
        symbol: 标的代码
        snapshot: 可选的 AnalysisSnapshot 数据（追问场景使用）

    Returns:
        结构分析摘要
    """
    # 如果提供了 snapshot，直接使用
    if snapshot:
        market_structure = snapshot.get("market_structure_v2") if isinstance(snapshot.get("market_structure_v2"), dict) else {}
        pattern = snapshot.get("pattern_detection_v2") if isinstance(snapshot.get("pattern_detection_v2"), dict) else {}
        evidence = list(market_structure.get("evidence") or [])[:2] if isinstance(market_structure, dict) else []
        return {
            "symbol": symbol,
            "structure_summary": str(market_structure.get("structure_label") or snapshot.get("trend") or "震荡"),
            "trend_strength": "中强" if snapshot.get("trend") in ("偏多", "偏空") else "中弱",
            "wyckoff_phase": market_structure.get("wyckoff_phase"),
            "primary_pattern": pattern.get("primary_pattern"),
            "evidence": evidence,
            "message": "基于 Snapshot 的结构评估",
        }

    # 否则重新获取数据
    from tools.market_data import fetch_market_data
    raw = fetch_market_data.invoke({"symbol": symbol, "interval": "1d"})
    if "error" in raw:
        return {"symbol": symbol, "structure_summary": "数据获取失败",
                "message": raw.get("error")}

    klines = raw.get("data", [])
    closes = [k.get("close", k.get("收盘", 0)) for k in klines if k.get("close") or k.get("收盘")]
    closes = [c for c in closes if c and c > 0]

    ma_config = _get_ma_config(symbol)
    ma_values = {}
    for name, period in [("MA_short", ma_config["short"]), ("MA_mid", ma_config["mid"]), ("MA_long", ma_config["long"])]:
        ma_values[name] = _calculate_ma(closes, period)

    structure_result = _analyze_structure(klines, ma_values)
    structure_summary = structure_result.get("summary", "") if isinstance(structure_result, dict) else str(structure_result)
    trend = _determine_trend(closes, ma_values)

    return {
        "symbol": symbol,
        "structure_summary": structure_summary,
        "structure_123": structure_result.get("structure_123", {}) if isinstance(structure_result, dict) else {},
        "trend": trend,
        "trend_strength": "中强" if trend in ("偏多", "偏空") else "中弱",
        "message": "基于真实数据的结构评估",
    }

def _compare_symbols(analyses: dict[str, dict]) -> dict[str, Any]:
    """横向对比多标的：基于 v2 结构字段排序。"""
    summary: list[dict[str, Any]] = []

    for sym, result in analyses.items():
        if result.get("status") == "error":
            summary.append({
                "symbol": sym,
                "trend": "N/A",
                "structure_label": "unknown",
                "status": "error",
            })
            continue

        analysis = result.get("analysis", result)
        market_structure = analysis.get("market_structure_v2") if isinstance(analysis.get("market_structure_v2"), dict) else {}
        pattern = analysis.get("pattern_detection_v2") if isinstance(analysis.get("pattern_detection_v2"), dict) else {}
        actionability = analysis.get("actionability") if isinstance(analysis.get("actionability"), dict) else {}
        phase = str(market_structure.get("wyckoff_phase") or "")
        primary_pattern = str(pattern.get("primary_pattern") or "")
        confidence = float(pattern.get("confidence") or market_structure.get("confidence") or 0.0)
        rank = confidence
        if bool(actionability.get("can_trade_now")):
            rank += 0.12
        if phase in {"markup", "accumulation"}:
            rank += 0.04
        elif phase in {"markdown", "distribution"}:
            rank += 0.02

        summary.append({
            "symbol": sym,
            "trend": analysis.get("trend", "震荡"),
            "structure_label": str(market_structure.get("structure_label") or "unknown"),
            "primary_pattern": primary_pattern or "unknown",
            "wyckoff_phase": phase or None,
            "confidence": round(confidence, 3),
            "current_price": analysis.get("current_price"),
            "_rank": rank,
        })

    valid = [s for s in summary if s.get("status") != "error"]
    strongest = max(valid, key=lambda x: x.get("_rank", 0)) if valid else None
    weakest = min(valid, key=lambda x: x.get("_rank", 0)) if valid else None

    for row in summary:
        row.pop("_rank", None)

    return {
        "summary": summary,
        "strongest": strongest,
        "weakest": weakest,
        "trend_distribution": {
            "偏多": len([s for s in valid if s.get("trend") == "偏多"]),
            "偏空": len([s for s in valid if s.get("trend") == "偏空"]),
            "震荡": len([s for s in valid if s.get("trend") == "震荡"]),
        },
    }


def _build_comparison_brief_v1(comparison: dict[str, Any]) -> dict[str, Any]:
    strongest = comparison.get("strongest") if isinstance(comparison.get("strongest"), dict) else {}
    weakest = comparison.get("weakest") if isinstance(comparison.get("weakest"), dict) else {}
    trend_dist = comparison.get("trend_distribution") if isinstance(comparison.get("trend_distribution"), dict) else {}
    return {
        "strongest_symbol": strongest.get("symbol"),
        "strongest_trend": strongest.get("trend"),
        "weakest_symbol": weakest.get("symbol"),
        "weakest_trend": weakest.get("trend"),
        "trend_distribution": trend_dist,
        "brief": (
            f"最强: {strongest.get('symbol') or 'N/A'}({strongest.get('trend') or 'N/A'}), "
            f"最弱: {weakest.get('symbol') or 'N/A'}({weakest.get('trend') or 'N/A'})"
        ),
    }


@tool
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

    raw = fetch_market_data.invoke({"symbol": symbol, "interval": interval})
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
        if current_price >= fib["swing_high"]:
            position = "above_swing_high"
        elif current_price <= fib["swing_low"]:
            position = "below_swing_low"
        elif current_price >= fib["retracement_23.6%"]:
            position = "0% ~ 23.6%"
        elif current_price >= fib["retracement_38.2%"]:
            position = "23.6% ~ 38.2%"
        elif current_price >= fib["retracement_50.0%"]:
            position = "38.2% ~ 50.0%"
        elif current_price >= fib["retracement_61.8%"]:
            position = "50.0% ~ 61.8%"
        else:
            position = "61.8% ~ 100%"

    return {
        "symbol": symbol,
        "interval": interval,
        "fib_levels": fib,
        "current_price": current_price,
        "current_position": position,
        "source": "auto_swing_from_klines",
        "message": f"基于最近 {len(recent)} 根 K 线的极值计算斐波那契水平",
    }
