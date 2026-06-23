"""技术分析工具 — 基于真实 K 线数据计算技术指标

核心工具:
- analyze_market: 统一行情分析入口（支持单标的与多标的）
- get_key_levels: 关键支撑/阻力位（基于分形方法）
- evaluate_structure: 评估市场结构（123法则、均线、量价）
- analyze_fibonacci: 斐波那契回撤与扩展分析
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
import json

from langchain_core.tools import tool
from memory.snapshot import snapshot_manager
from config.runtime_config import get_ma_system
from utils.logging_utils import get_logger


logger = get_logger(__name__)


# ── 辅助函数 ──

def _get_ma_config(symbol: str) -> dict[str, int]:
    """根据标的类型获取均线参数"""
    ma_system = get_ma_system()

    # 判断市场类型
    s = symbol.upper()
    if any(kw in s for kw in ["BTC", "ETH", "SOL", "USDT", "BNB", "XRP", "DOGE"]):
        market = "crypto"
    elif s.endswith((".SH", ".SZ")) or (s.split(".")[0].isdigit() and len(s.split(".")[0]) == 6):
        market = "equity"
    elif "AU" in s or "GOLD" in s:
        market = "gold"
    else:
        market = "default"

    return ma_system.get(market, ma_system.get("default", {"short": 20, "mid": 60, "long": 120}))


def _calculate_ma(closes: list[float], period: int) -> float | None:
    """计算简单移动平均"""
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 4)


def _determine_trend(closes: list[float], ma_values: dict[str, float | None]) -> str:
    """根据 MA 排列和价格位置判断趋势"""
    ma_short = ma_values.get("MA_short")
    ma_mid = ma_values.get("MA_mid")
    ma_long = ma_values.get("MA_long")

    if ma_short is None or ma_mid is None:
        # 数据不足，根据最近价格走势判断
        if len(closes) >= 5:
            recent_trend = closes[-5:]
            if recent_trend[-1] > recent_trend[0]:
                return "偏多"
            elif recent_trend[-1] < recent_trend[0]:
                return "偏空"
        return "震荡"

    # 多头排列: MA_short > MA_mid > MA_long, 且价格 > MA_short
    current = closes[-1]
    if ma_short > ma_mid and ma_mid > (ma_long or 0) and current > ma_short:
        return "偏多"
    # 空头排列: MA_short < MA_mid < MA_long, 且价格 < MA_short
    if ma_short < ma_mid and ma_mid < (ma_long or float("inf")) and current < ma_short:
        return "偏空"
    # 其他: 震荡
    return "震荡"


def _calculate_key_levels(
    klines: list[dict[str, Any]], left: int = 2, right: int = 2
) -> dict[str, list[float]]:
    """基于分形方法计算关键支撑/阻力位"""
    supports: list[float] = []
    resistances: list[float] = []

    highs = [k.get("high", k.get("最高", 0)) for k in klines if k.get("high") or k.get("最高")]
    lows = [k.get("low", k.get("最低", 0)) for k in klines if k.get("low") or k.get("最低")]

    if not highs or not lows:
        return {"support": [], "resistance": []}

    # 分形高点（resistance）
    for i in range(left, len(highs) - right):
        is_fractal_high = all(highs[i] >= highs[i - j] for j in range(1, left + 1)) and \
                          all(highs[i] >= highs[i + j] for j in range(1, right + 1))
        if is_fractal_high:
            resistances.append(highs[i])

    # 分形低点（support）
    for i in range(left, len(lows) - right):
        is_fractal_low = all(lows[i] <= lows[i - j] for j in range(1, left + 1)) and \
                         all(lows[i] <= lows[i + j] for j in range(1, right + 1))
        if is_fractal_low:
            supports.append(lows[i])

    # 取最近 3 个关键位
    return {
        "support": sorted(supports, reverse=True)[:3],
        "resistance": sorted(resistances)[:3],
    }


def _analyze_structure(
    klines: list[dict[str, Any]], ma_values: dict[str, float | None]
) -> dict[str, Any]:
    """量价结构分析 + 123 交易法判断"""
    parts: list[str] = []

    # MA 排列
    ma_short = ma_values.get("MA_short")
    ma_mid = ma_values.get("MA_mid")
    ma_long = ma_values.get("MA_long")

    if ma_short and ma_mid and ma_long:
        if ma_short > ma_mid > ma_long:
            parts.append("均线多头排列")
        elif ma_short < ma_mid < ma_long:
            parts.append("均线空头排列")
        else:
            parts.append("均线交叉/震荡排列")

    # 量价关系
    closes = [k.get("close", k.get("收盘", 0)) for k in klines if k.get("close") or k.get("收盘")]
    volumes = [k.get("volume", k.get("成交量", 0)) for k in klines if k.get("volume") or k.get("成交量")]

    price_up = False
    vol_up = False
    if len(closes) >= 5 and len(volumes) >= 5:
        recent_close = closes[-5:]
        recent_vol = volumes[-5:]
        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)

        price_up = recent_close[-1] > recent_close[0]
        vol_up = sum(recent_vol) / 5 > avg_vol

        if price_up and vol_up:
            parts.append("放量上涨")
        elif not price_up and vol_up:
            parts.append("放量下跌")
        elif price_up and not vol_up:
            parts.append("缩量上涨")
        else:
            parts.append("缩量下跌")

    summary = "，".join(parts) if parts else "数据不足，结构分析暂不可用"

    # 123 交易法判断（简化版）
    structure_123: dict[str, Any] = {
        "stage_1_breakout": False,
        "stage_2_pullback": False,
        "stage_3_continuation": False,
        "description": "数据不足，无法判断 123 结构",
    }

    if len(closes) >= 10 and len(volumes) >= 10:
        # 简化逻辑：
        # 1 = 突破（最近 5 根放量上涨且价格新高）
        # 2 = 回踩（突破后缩量回调但不破关键位）
        # 3 = 延续（回踩后再次放量上攻）
        highs = [k.get("high", k.get("最高", 0)) for k in klines if k.get("high") or k.get("最高")]
        if len(highs) >= 10:
            prev_high = max(highs[-10:-3])
            recent_high = max(highs[-3:])
            recent_price_up = closes[-1] > closes[-5]
            recent_vol_up = sum(volumes[-3:]) / 3 > sum(volumes[-10:-3]) / 7

            if recent_price_up and recent_vol_up and recent_high > prev_high:
                structure_123["stage_1_breakout"] = True
                structure_123["description"] = "阶段1：突破成立（放量新高）"
            elif not recent_price_up and not recent_vol_up and closes[-1] > prev_high * 0.97:
                structure_123["stage_2_pullback"] = True
                structure_123["description"] = "阶段2：回踩确认（缩量不破）"
            elif recent_price_up and recent_vol_up and closes[-1] > prev_high:
                structure_123["stage_3_continuation"] = True
                structure_123["description"] = "阶段3：延续上攻（回踩后放量）"
            else:
                structure_123["description"] = "当前未形成完整 123 结构"

    return {
        "summary": summary,
        "structure_123": structure_123,
    }


def _assess_structure_signals(
    trend: str,
    ma_values: dict[str, float | None],
    key_levels: dict[str, list],
) -> dict[str, Any]:
    """结构信号 — 描述可观测事实，不输出伪概率/百分比。"""
    ma_short = ma_values.get("MA_short")
    ma_mid = ma_values.get("MA_mid")
    ma_long = ma_values.get("MA_long")

    ma_alignment = "mixed"
    if ma_short and ma_mid and ma_long:
        if ma_short > ma_mid > ma_long:
            ma_alignment = "bullish"
        elif ma_short < ma_mid < ma_long:
            ma_alignment = "bearish"

    trend_ma_match = (
        (trend == "偏多" and ma_alignment == "bullish")
        or (trend == "偏空" and ma_alignment == "bearish")
    )

    supports = key_levels.get("support", [])
    resistances = key_levels.get("resistance", [])

    return {
        "ma_alignment": ma_alignment,
        "trend_ma_match": trend_ma_match,
        "trend_clarity": "directional" if trend in ("偏多", "偏空") else "range_bound",
        "key_levels": {
            "support_count": len(supports),
            "resistance_count": len(resistances),
        },
    }


def _structure_signal_rank(signals: dict[str, Any] | None) -> int:
    """多标的横向对比：基于结构信号排序，不用伪 confidence 分数。"""
    if not signals:
        return 0
    rank = 0
    if signals.get("trend_clarity") == "directional":
        rank += 2
    if signals.get("trend_ma_match"):
        rank += 2
    key_levels = signals.get("key_levels") or {}
    if key_levels.get("support_count", 0) >= 1 and key_levels.get("resistance_count", 0) >= 1:
        rank += 1
    return rank


def _format_structure_note(signals: dict[str, Any] | None) -> str:
    if not signals:
        return "结构信号暂不可用"
    alignment = {
        "bullish": "均线多头",
        "bearish": "均线空头",
        "mixed": "均线交叉",
    }.get(str(signals.get("ma_alignment")), "均线交叉")
    if signals.get("trend_ma_match"):
        return f"{alignment}，与趋势一致"
    if signals.get("trend_clarity") == "range_bound":
        return f"{alignment}，震荡结构"
    return f"{alignment}，与趋势不完全一致"


def _safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _round_price(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _merge_level_candidates(
    key_levels: dict[str, list[float]],
    fib_levels: dict[str, float],
) -> list[float]:
    levels: list[float] = []
    for val in key_levels.get("support", []) or []:
        fv = _safe_float(val)
        if fv is not None:
            levels.append(fv)
    for val in key_levels.get("resistance", []) or []:
        fv = _safe_float(val)
        if fv is not None:
            levels.append(fv)

    # 将 fib 回撤位并入候选层级，后续统一按当前价重分支撑/阻力。
    for key, val in (fib_levels or {}).items():
        if "retracement_" not in str(key):
            continue
        fv = _safe_float(val)
        if fv is not None:
            levels.append(fv)

    return sorted(set(levels))


def _classify_levels_by_price(
    *,
    levels: list[float],
    current_price: float,
) -> tuple[list[float], list[float]]:
    supports = sorted([x for x in levels if x <= current_price], reverse=True)
    resistances = sorted([x for x in levels if x >= current_price])
    return supports, resistances


def _normalize_key_levels_by_price(
    *,
    key_levels: dict[str, list[float]],
    current_price: float,
) -> dict[str, list[float]]:
    raw_levels: list[float] = []
    for val in key_levels.get("support", []) or []:
        fv = _safe_float(val)
        if fv is not None:
            raw_levels.append(fv)
    for val in key_levels.get("resistance", []) or []:
        fv = _safe_float(val)
        if fv is not None:
            raw_levels.append(fv)
    supports, resistances = _classify_levels_by_price(levels=sorted(set(raw_levels)), current_price=current_price)
    return {
        "support": supports[:2],
        "resistance": resistances[:2],
    }


def _extract_swings(
    klines: list[dict[str, Any]],
    *,
    left: int = 2,
    right: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    highs = [float(k.get("high", k.get("最高", 0)) or 0) for k in klines]
    lows = [float(k.get("low", k.get("最低", 0)) or 0) for k in klines]
    swing_highs: list[dict[str, Any]] = []
    swing_lows: list[dict[str, Any]] = []

    if len(klines) < left + right + 1:
        return swing_highs, swing_lows

    for i in range(left, len(klines) - right):
        h = highs[i]
        l = lows[i]
        if h > 0 and all(h >= highs[i - j] for j in range(1, left + 1)) and all(h >= highs[i + j] for j in range(1, right + 1)):
            swing_highs.append({"price": _round_price(h), "index": i})
        if l > 0 and all(l <= lows[i - j] for j in range(1, left + 1)) and all(l <= lows[i + j] for j in range(1, right + 1)):
            swing_lows.append({"price": _round_price(l), "index": i})

    return swing_highs[-8:], swing_lows[-8:]


def _is_monotonic(values: list[float], *, increasing: bool) -> bool:
    if len(values) < 3:
        return False
    if increasing:
        return all(values[i] > values[i - 1] for i in range(1, len(values)))
    return all(values[i] < values[i - 1] for i in range(1, len(values)))


def _variation_pct(values: list[float]) -> float:
    clean = [float(v) for v in values if isinstance(v, (int, float)) and v > 0]
    if len(clean) < 2:
        return 999.0
    return (max(clean) - min(clean)) / max(min(clean), 1e-9) * 100.0


def _volume_contraction(volumes: list[float]) -> bool:
    vals = [float(v) for v in volumes if isinstance(v, (int, float)) and v > 0]
    if len(vals) < 12:
        return False
    recent = vals[-10:]
    head = recent[:5]
    tail = recent[5:]
    return (sum(tail) / len(tail)) < (sum(head) / len(head)) * 0.92


def _count_touches(prices: list[float], *, target: float, tol_pct: float = 0.35) -> int:
    if target <= 0:
        return 0
    tol = target * tol_pct / 100.0
    return sum(1 for p in prices if isinstance(p, (int, float)) and abs(float(p) - target) <= tol)


def _build_market_structure_v1(
    *,
    symbol: str,
    interval: str,
    current_price: float,
    trend: str,
    swing_highs: list[dict[str, Any]],
    swing_lows: list[dict[str, Any]],
    closes: list[float],
    volumes: list[float],
) -> dict[str, Any]:
    highs_recent = [float(x["price"]) for x in swing_highs[-4:] if isinstance(x.get("price"), (int, float))]
    lows_recent = [float(x["price"]) for x in swing_lows[-4:] if isinstance(x.get("price"), (int, float))]
    lower_highs = _is_monotonic(highs_recent, increasing=False)
    higher_lows = _is_monotonic(lows_recent, increasing=True)
    higher_highs = _is_monotonic(highs_recent, increasing=True)
    lower_lows = _is_monotonic(lows_recent, increasing=False)
    highs_flat = _variation_pct(highs_recent) <= 0.9
    lows_flat = _variation_pct(lows_recent) <= 0.9
    vol_contract = _volume_contraction(volumes)

    range_high_candidates = highs_recent or [max(closes[-30:])] if closes else []
    range_low_candidates = lows_recent or [min(closes[-30:])] if closes else []
    current_high = max(range_high_candidates) if range_high_candidates else current_price
    current_low = min(range_low_candidates) if range_low_candidates else current_price
    width_pct = ((current_high - current_low) / max(current_price, 1e-9)) * 100.0 if current_price > 0 else 0.0

    structure_label = "unknown"
    confidence = 0.45
    evidence: list[str] = []
    invalid_if: list[str] = []

    if lower_highs and higher_lows and width_pct <= 9.0:
        structure_label = "triangle_convergence"
        confidence = 0.78 if vol_contract else 0.70
        evidence.append("最近 swing high 逐步下移，swing low 逐步抬高。")
        evidence.append(f"区间宽度 {width_pct:.2f}% ，呈收敛特征。")
        if vol_contract:
            evidence.append("成交量近 10 根呈收缩。")
        invalid_if.append("向上突破最近下压趋势线且放量，或向下跌破最近上升趋势线且放量。")
    elif highs_flat and lows_flat and width_pct <= 8.0:
        structure_label = "rectangle"
        confidence = 0.74 if vol_contract else 0.66
        evidence.append("最近 swing high/swing low 波动收敛在水平区间。")
        evidence.append(f"区间宽度 {width_pct:.2f}% ，符合箱体震荡特征。")
        invalid_if.append("有效放量突破箱体上沿或下沿。")
    elif higher_highs and lower_lows and width_pct >= 6.0:
        structure_label = "expanding"
        confidence = 0.67
        evidence.append("最近高点抬高且低点下移，振幅扩大。")
        invalid_if.append("振幅重新收窄并回到中轴附近。")
    elif trend in ("偏多", "偏空") and width_pct >= 4.0:
        structure_label = "trending"
        confidence = 0.63
        evidence.append(f"趋势方向为 {trend}，区间宽度 {width_pct:.2f}%。")
        invalid_if.append("趋势方向被反向突破并出现连续确认K线。")
    else:
        structure_label = "ranging"
        confidence = 0.56
        evidence.append(f"趋势为 {trend}，当前以区间震荡为主（宽度 {width_pct:.2f}%）。")
        invalid_if.append("出现连续同向放量突破。")

    close_recent = closes[-60:] if len(closes) >= 60 else closes
    battle_zones: list[dict[str, Any]] = []
    for center in [current_low, (current_low + current_high) / 2.0, current_high]:
        if center <= 0:
            continue
        touch_count = _count_touches(close_recent, target=center, tol_pct=0.35)
        if touch_count < 2:
            continue
        zone_half = center * 0.25 / 100.0
        battle_zones.append(
            {
                "low": _round_price(center - zone_half),
                "high": _round_price(center + zone_half),
                "touches": touch_count,
            }
        )

    return {
        "structure_label": structure_label,
        "confidence": round(float(confidence), 3),
        "swing_highs": swing_highs[-4:],
        "swing_lows": swing_lows[-4:],
        "current_range": {
            "high": _round_price(current_high),
            "low": _round_price(current_low),
            "width_pct": _round_price(width_pct),
        },
        "volume_contraction": bool(vol_contract),
        "battle_zones": battle_zones[:3],
        "evidence": evidence[:4],
        "invalid_if": invalid_if[:3],
        "meta": {"symbol": symbol, "interval": interval},
    }


def _build_pattern_detection_v1(
    *,
    market_structure_v1: dict[str, Any],
    levels_v2: dict[str, Any],
) -> dict[str, Any]:
    label = str(market_structure_v1.get("structure_label") or "unknown")
    conf = float(market_structure_v1.get("confidence") or 0.0)
    pattern_name = {
        "triangle_convergence": "triangle_convergence",
        "rectangle": "rectangle",
        "expanding": "expanding",
        "trending": "trend_channel_like",
        "ranging": "range_consolidation",
    }.get(label, "unknown")
    status = "active" if pattern_name != "unknown" else "unclear"
    key_levels = {
        "nearest_support": levels_v2.get("nearest_support"),
        "nearest_resistance": levels_v2.get("nearest_resistance"),
    }
    return {
        "primary_pattern": pattern_name,
        "status": status,
        "confidence": round(conf, 3),
        "evidence": list(market_structure_v1.get("evidence") or [])[:3],
        "invalid_if": list(market_structure_v1.get("invalid_if") or [])[:2],
        "key_levels": key_levels,
    }


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
    market_structure = analysis_result.get("market_structure_v1") if isinstance(analysis_result.get("market_structure_v1"), dict) else {}
    pattern_detection = analysis_result.get("pattern_detection_v1") if isinstance(analysis_result.get("pattern_detection_v1"), dict) else {}
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
            "key_levels.full_list",
            "structure_signals.key_levels",
            "market_structure_v1.swing_highs",
            "market_structure_v1.swing_lows",
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

    from .market_data import fetch_market_data

    raw = fetch_market_data.invoke({"symbol": symbol, "interval": interval})
    if "error" in raw:
        return {
            "status": "error",
            "symbol": symbol,
            "interval": interval,
            "message": raw.get("error", "数据获取失败"),
        }

    klines = raw.get("data", [])
    if not klines:
        return {
            "status": "error",
            "symbol": symbol,
            "interval": interval,
            "message": "无 K 线数据",
        }

    closes = [k.get("close", k.get("收盘", 0)) for k in klines]
    closes = [c for c in closes if c and c > 0]
    if not closes:
        return {
            "status": "error",
            "symbol": symbol,
            "interval": interval,
            "message": "收盘价数据为空",
        }

    ma_config = _get_ma_config(symbol)
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
    swing_highs, swing_lows = _extract_swings(klines)
    market_structure_v1 = _build_market_structure_v1(
        symbol=symbol,
        interval=interval,
        current_price=closes[-1],
        trend=trend,
        swing_highs=swing_highs,
        swing_lows=swing_lows,
        closes=closes,
        volumes=volumes,
    )
    pattern_detection_v1 = _build_pattern_detection_v1(
        market_structure_v1=market_structure_v1,
        levels_v2=trade_snapshot.get("levels_v2", {}),
    )

    analysis_result = {
        "symbol": symbol,
        "interval": interval,
        "timestamp": datetime.now().isoformat(),
        "current_price": closes[-1],
        "trend": trend,
        "key_levels": key_levels,
        "structure": structure_summary,
        "indicators": {
            "ma_trend": f"MA排列: {trend}",
        },
        "structure_signals": structure_signals,
        "levels_v2": trade_snapshot.get("levels_v2", {}),
        "trigger_conditions": trade_snapshot.get("trigger_conditions", {}),
        "invalidation_conditions": trade_snapshot.get("invalidation_conditions", {}),
        "risk_flags": trade_snapshot.get("risk_flags", []),
        "actionability": trade_snapshot.get("actionability", {}),
        "market_structure_v1": market_structure_v1,
        "pattern_detection_v1": pattern_detection_v1,
        "raw_insights": f"{symbol} 在 {interval} 周期呈{trend}结构，{structure_note}。",
    }
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
        "symbol": symbol,
        "interval": interval,
        "analysis": analysis_result,
        "compact_summary_v1": compact_summary_v1,
        "output_meta_v1": output_meta_v1,
        "snapshot": snapshot,
        "message": f"{symbol} {interval} 技术分析完成: {trend}，{structure_note}",
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
        return {
            "symbol": symbol,
            "support_levels": snapshot.get("key_levels", {}).get("support", []),
            "resistance_levels": snapshot.get("key_levels", {}).get("resistance", []),
            "message": "从上次分析快照中获取关键位",
        }

    # 否则重新获取数据计算
    from .market_data import fetch_market_data
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
        signals = snapshot.get("structure_signals") or {}
        return {
            "symbol": symbol,
            "structure_summary": snapshot.get("structure", "震荡"),
            "trend_strength": "中强" if snapshot.get("trend") in ("偏多", "偏空") else "中弱",
            "structure_signals": signals,
            "message": "基于 Snapshot 的结构评估",
        }

    # 否则重新获取数据
    from .market_data import fetch_market_data
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
    """横向对比多标的：按结构信号排序，不用伪 confidence。"""
    summary: list[dict[str, Any]] = []

    for sym, result in analyses.items():
        if result.get("status") == "error":
            summary.append({
                "symbol": sym,
                "trend": "N/A",
                "structure_signals": {},
                "status": "error",
            })
            continue

        analysis = result.get("analysis", result)
        signals = analysis.get("structure_signals") or {}
        summary.append({
            "symbol": sym,
            "trend": analysis.get("trend", "震荡"),
            "structure_signals": signals,
            "structure_note": _format_structure_note(signals),
            "current_price": analysis.get("current_price"),
            "_rank": _structure_signal_rank(signals),
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


# ── 斐波那契分析工具 ──

def _calculate_fib_levels(high: float, low: float) -> dict[str, float]:
    """计算斐波那契回撤与扩展水平"""
    diff = high - low
    return {
        "swing_high": round(high, 4),
        "swing_low": round(low, 4),
        "retracement_23.6%": round(high - diff * 0.236, 4),
        "retracement_38.2%": round(high - diff * 0.382, 4),
        "retracement_50.0%": round(high - diff * 0.5, 4),
        "retracement_61.8%": round(high - diff * 0.618, 4),
        "retracement_78.6%": round(high - diff * 0.786, 4),
        "extension_127.2%": round(high + diff * 0.272, 4),
        "extension_161.8%": round(high + diff * 0.618, 4),
        "extension_261.8%": round(high + diff * 1.618, 4),
    }


@tool
def analyze_fibonacci(
    symbol: str,
    interval: str = "1d",
    swing_high: float | None = None,
    swing_low: float | None = None,
) -> Dict[str, Any]:
    """斐波那契回撤与扩展分析（基于最近 swing high/low）

    Args:
        symbol: 标的代码
        interval: 时间周期
        swing_high: 可选的手动指定 swing high 价格（不指定则自动从 K 线找最近高点）
        swing_low: 可选的手动指定 swing low 价格（不指定则自动从 K 线找最近低点）

    Returns:
        斐波那契关键价位 + 当前价格所处区间说明
    """
    from .market_data import fetch_market_data

    raw = fetch_market_data.invoke({"symbol": symbol, "interval": interval})
    if "error" in raw:
        return {"symbol": symbol, "status": "error", "message": raw.get("error")}

    klines = raw.get("data", [])
    if len(klines) < 5:
        return {"symbol": symbol, "status": "error", "message": "K 线数据不足，无法计算斐波那契"}

    # 如果用户手动指定了 swing high/low，直接使用
    if swing_high is not None and swing_low is not None:
        fib = _calculate_fib_levels(swing_high, swing_low)
        current_price = klines[-1][4] if klines else None
        return {
            "symbol": symbol,
            "interval": interval,
            "fib_levels": fib,
            "current_price": current_price,
            "source": "manual_swing",
            "message": "基于手动指定的 swing high/low 计算",
        }

    # 否则自动从 K 线找最近的 swing high / low（简单策略：取最近 N 根的极值）
    recent = klines[-30:] if len(klines) > 30 else klines
    highs = [k[2] for k in recent]
    lows = [k[3] for k in recent]
    auto_high = max(highs)
    auto_low = min(lows)

    fib = _calculate_fib_levels(auto_high, auto_low)
    current_price = klines[-1][4] if klines else None

    # 判断当前价格落在哪个回撤区间
    position = "above_high"
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


def get_technical_tools():
    """返回技术分析相关工具"""
    return [analyze_market, get_key_levels, evaluate_structure, analyze_fibonacci]
