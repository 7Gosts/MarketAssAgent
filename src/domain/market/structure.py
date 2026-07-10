"""Market structure domain logic (swing / wyckoff / structure)."""

from __future__ import annotations

from typing import Any

from .indicators import (
    _round_price,
    _safe_float,
)

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

def _detect_swing_highs_v2(
    klines: list[dict[str, Any]],
    *,
    window: int = 5,
    min_strength: float = 0.015,
) -> list[dict[str, Any]]:
    highs = [float(k.get("high", k.get("最高", 0)) or 0) for k in klines]
    lows = [float(k.get("low", k.get("最低", 0)) or 0) for k in klines]
    vols = [float(k.get("volume", k.get("成交量", 0)) or 0) for k in klines]
    out: list[dict[str, Any]] = []
    if len(klines) < window * 2 + 1:
        return out
    for i in range(window, len(klines) - window):
        left_highs = highs[i - window : i]
        right_highs = highs[i + 1 : i + window + 1]
        segment_lows = lows[i - window : i + window + 1]
        cur_high = highs[i]
        if cur_high <= 0 or not left_highs or not right_highs:
            continue
        if not (cur_high > max(left_highs) and cur_high > max(right_highs)):
            continue
        segment_low = min(v for v in segment_lows if v > 0) if any(v > 0 for v in segment_lows) else cur_high
        strength = (cur_high - segment_low) / max(cur_high, 1e-9)
        if strength < float(min_strength):
            continue
        out.append(
            {
                "price": _round_price(cur_high),
                "index": i,
                "strength": round(float(strength), 3),
                "volume": _round_price(vols[i]),
            }
        )
    return out


def _detect_swing_lows_v2(
    klines: list[dict[str, Any]],
    *,
    window: int = 5,
    min_strength: float = 0.015,
) -> list[dict[str, Any]]:
    highs = [float(k.get("high", k.get("最高", 0)) or 0) for k in klines]
    lows = [float(k.get("low", k.get("最低", 0)) or 0) for k in klines]
    vols = [float(k.get("volume", k.get("成交量", 0)) or 0) for k in klines]
    out: list[dict[str, Any]] = []
    if len(klines) < window * 2 + 1:
        return out
    for i in range(window, len(klines) - window):
        left_lows = lows[i - window : i]
        right_lows = lows[i + 1 : i + window + 1]
        segment_highs = highs[i - window : i + window + 1]
        cur_low = lows[i]
        if cur_low <= 0 or not left_lows or not right_lows:
            continue
        if not (cur_low < min(left_lows) and cur_low < min(right_lows)):
            continue
        segment_high = max(v for v in segment_highs if v > 0) if any(v > 0 for v in segment_highs) else cur_low
        strength = (segment_high - cur_low) / max(segment_high, 1e-9)
        if strength < float(min_strength):
            continue
        out.append(
            {
                "price": _round_price(cur_low),
                "index": i,
                "strength": round(float(strength), 3),
                "volume": _round_price(vols[i]),
            }
        )
    return out


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


def _analyze_volume_trend_v2(
    *,
    volumes: list[float],
    closes: list[float],
) -> str:
    vals = [float(v) for v in volumes if isinstance(v, (int, float)) and v > 0]
    if len(vals) < 12:
        return "neutral"
    recent = vals[-12:]
    head = recent[:6]
    tail = recent[6:]
    head_avg = sum(head) / len(head)
    tail_avg = sum(tail) / len(tail)
    vol_increasing = tail_avg > head_avg * 1.08
    vol_decreasing = tail_avg < head_avg * 0.92
    px_up = len(closes) >= 8 and closes[-1] > closes[-8]
    px_down = len(closes) >= 8 and closes[-1] < closes[-8]
    if (px_up and vol_decreasing) or (px_down and vol_increasing):
        return "divergence"
    if vol_increasing:
        return "increasing"
    if vol_decreasing:
        return "decreasing"
    return "neutral"


def _detect_spring_upthrust_v2(
    *,
    last_high: float | None,
    last_low: float | None,
    last_close: float | None,
    range_high: float,
    range_low: float,
) -> bool:
    if last_close is None:
        return False
    spring = (last_low is not None) and (last_low < range_low) and (last_close > range_low)
    upthrust = (last_high is not None) and (last_high > range_high) and (last_close < range_high)
    return bool(spring or upthrust)


def _detect_wyckoff_signals_v2(
    *,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
) -> dict[str, Any]:
    signals: dict[str, Any] = {
        "spring_detected": False,
        "upthrust_detected": False,
        "accumulation_signs": 0,
        "distribution_signs": 0,
        "signals": [],
        "evidence": [],
        "confidence": 0.0,
        "phase": None,
        "phase_transition": None,
    }
    if len(highs) < 30 or len(lows) < 30 or len(closes) < 30:
        return signals

    # recent/prior 切片与 pandas 版逻辑对齐：recent[-20:], prior[-60:-20]
    prior_start = max(len(highs) - 60, 0)
    prior_end = max(len(highs) - 20, 0)
    prior_highs = highs[prior_start:prior_end]
    prior_lows = lows[prior_start:prior_end]
    prior_closes = closes[prior_start:prior_end]
    prior_volumes = volumes[prior_start:prior_end]

    recent_highs = highs[-20:]
    recent_lows = lows[-20:]
    recent_closes = closes[-20:]
    recent_volumes = volumes[-20:]
    if not prior_highs or not prior_lows or not prior_closes:
        return signals

    earlier_high = max(prior_highs)
    earlier_low = min(prior_lows)
    recent_high = max(recent_highs)
    recent_low = min(recent_lows)
    last_close = closes[-1]
    confidence = 0.0

    if recent_low < earlier_low * 0.995 and last_close > earlier_low * 1.01:
        signals["spring_detected"] = True
        signals["accumulation_signs"] = int(signals["accumulation_signs"]) + 1
        signals["signals"].append("spring")
        signals["evidence"].append(
            f"出现 Spring：低点假突破（{recent_low:.2f} < {earlier_low:.2f}），随后快速拉回。"
        )
        confidence += 0.35

    if recent_high > earlier_high * 1.005 and last_close < earlier_high * 0.99:
        signals["upthrust_detected"] = True
        signals["distribution_signs"] = int(signals["distribution_signs"]) + 1
        signals["signals"].append("upthrust")
        signals["evidence"].append(
            f"出现 Upthrust：高点假突破（{recent_high:.2f} > {earlier_high:.2f}），随后回落。"
        )
        confidence += 0.35

    prior_close_avg = sum(prior_closes) / max(len(prior_closes), 1)
    prior_volume_avg = (
        sum(prior_volumes) / max(len(prior_volumes), 1)
        if prior_volumes
        else 0.0
    )
    recent_volume_avg = (
        sum(recent_volumes) / max(len(recent_volumes), 1)
        if recent_volumes
        else 0.0
    )
    if last_close > prior_close_avg and prior_volume_avg > 0 and recent_volume_avg < prior_volume_avg:
        signals["distribution_signs"] = int(signals["distribution_signs"]) + 1
        signals["evidence"].append("价升量缩，偏 Distribution 特征。")
        confidence += 0.2

    phase = _determine_wyckoff_phase_v2(
        highs=highs,
        lows=lows,
        closes=closes,
        signal_tags=list(signals["signals"]),
    )
    phase_transition = _detect_wyckoff_phase_transition_v2(
        highs=highs,
        lows=lows,
        closes=closes,
        current_phase=phase,
        signal_tags=list(signals["signals"]),
    )

    signals["confidence"] = round(min(confidence, 1.0), 3)
    signals["phase"] = phase
    signals["phase_transition"] = phase_transition

    return signals


def _determine_wyckoff_phase_v2(
    *,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    signal_tags: list[str],
) -> str | None:
    if not highs or not lows or not closes:
        return None
    last_low = lows[-1] if lows[-1] > 0 else 1e-9
    recent_range = (highs[-1] - lows[-1]) / last_low
    mean_window = closes[-20:] if len(closes) >= 20 else closes
    rolling_mean = sum(mean_window) / max(len(mean_window), 1)

    if "spring" in signal_tags and recent_range < 0.03:
        return "accumulation"
    if "upthrust" in signal_tags:
        return "distribution"
    if closes[-1] > rolling_mean * 1.02:
        return "markup"
    if closes[-1] < rolling_mean * 0.98:
        return "markdown"
    if recent_range < 0.04:
        return "accumulation"
    return None


def _detect_wyckoff_phase_transition_v2(
    *,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    current_phase: str | None,
    signal_tags: list[str],
) -> str | None:
    if not current_phase or len(closes) < 40 or len(highs) < 40 or len(lows) < 40:
        return None

    prior_slice_close = closes[-40:-20]
    if not prior_slice_close:
        return None
    prior_close = closes[-21]
    prior_mean = sum(prior_slice_close) / max(len(prior_slice_close), 1)
    prior_low = lows[-21] if lows[-21] > 0 else 1e-9
    prior_range = (highs[-21] - lows[-21]) / prior_low

    if prior_close > prior_mean * 1.02:
        prior_phase = "markup"
    elif prior_close < prior_mean * 0.98:
        prior_phase = "markdown"
    elif prior_range < 0.04:
        prior_phase = "accumulation"
    else:
        prior_phase = None

    if prior_phase and prior_phase != current_phase:
        return f"{prior_phase}_to_{current_phase}"
    if current_phase == "accumulation" and "spring" in signal_tags and closes[-1] > prior_mean:
        return "accumulation_to_markup_watch"
    if current_phase == "distribution" and "upthrust" in signal_tags and closes[-1] < prior_mean:
        return "distribution_to_markdown_watch"
    return None


def _build_market_structure_v2(
    *,
    symbol: str,
    interval: str,
    current_price: float,
    trend: str,
    swing_highs: list[dict[str, Any]],
    swing_lows: list[dict[str, Any]],
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    last_high: float | None,
    last_low: float | None,
    last_close: float | None,
) -> dict[str, Any]:
    highs_recent = [float(x["price"]) for x in swing_highs[-5:] if isinstance(x.get("price"), (int, float))]
    lows_recent = [float(x["price"]) for x in swing_lows[-5:] if isinstance(x.get("price"), (int, float))]
    lower_highs = _is_monotonic(highs_recent, increasing=False)
    higher_lows = _is_monotonic(lows_recent, increasing=True)
    higher_highs = _is_monotonic(highs_recent, increasing=True)
    lower_lows = _is_monotonic(lows_recent, increasing=False)
    highs_flat = _variation_pct(highs_recent) <= 1.2
    lows_flat = _variation_pct(lows_recent) <= 1.2
    volume_trend = _analyze_volume_trend_v2(volumes=volumes, closes=closes)

    recent_closes = closes[-40:] if len(closes) >= 40 else closes
    recent_highs = highs_recent or [max(recent_closes)] if recent_closes else [current_price]
    recent_lows = lows_recent or [min(recent_closes)] if recent_closes else [current_price]
    range_high = max(recent_highs)
    range_low = min(recent_lows)
    width_pct = ((range_high - range_low) / max(range_low, 1e-9)) * 100.0 if range_low > 0 else 0.0
    duration_bars = min(len(closes), 40)

    structure_label = "unknown"
    confidence = 0.42
    wyckoff_phase: str | None = None
    overlap: list[dict[str, Any]] = []
    evidence: list[str] = []
    invalid_conditions: list[str] = []

    duration = min(len(closes), 40)
    close_recent = closes[-duration:] if len(closes) >= duration else closes
    half = max(duration // 2, 1)
    head = close_recent[:half]
    tail = close_recent[-half:]
    initial_width_pct = _variation_pct(head) if head else 0.0
    current_width_pct = _variation_pct(tail) if tail else 0.0
    top_tests = _count_touches(close_recent, target=range_high, tol_pct=0.45) if close_recent else 0
    low_tests = _count_touches(close_recent, target=range_low, tol_pct=0.45) if close_recent else 0
    test_count = top_tests + low_tests

    triangle_candidate = lower_highs and higher_lows and width_pct <= 12.0
    rectangle_candidate = highs_flat and lows_flat and width_pct <= 10.0
    expanding_candidate = higher_highs and lower_lows and width_pct >= 8.0
    channel_up_candidate = higher_highs and higher_lows
    channel_down_candidate = lower_highs and lower_lows

    if triangle_candidate:
        tri_conf = 0.78 if volume_trend in {"decreasing", "divergence"} else 0.70
        evidence.append(
            f"价格在最近 {duration} 根K线内形成收敛区间，宽度从 {initial_width_pct:.2f}% 收窄至 {current_width_pct:.2f}%。"
        )
        evidence.append(
            f"高点序列逐步降低、低点序列逐步抬高（有效高点 {len(swing_highs)} 个，有效低点 {len(swing_lows)} 个）。"
        )
        if volume_trend in {"decreasing", "divergence"}:
            evidence.append(f"成交量呈收缩/背离特征（volume_trend={volume_trend}）。")
        overlap.append(
            {
                "pattern": "triangle_convergence",
                "confidence": round(float(tri_conf), 3),
                "reason": "高低点收敛 + 量能收缩/背离",
            }
        )
        invalid_conditions.append("放量突破收敛上沿/下沿且连续收盘确认。")

    if rectangle_candidate:
        rect_conf = 0.72 if volume_trend in {"decreasing", "neutral"} else 0.64
        evidence.append(
            f"价格在 {range_low:.2f} ~ {range_high:.2f} 区间内反复测试边界（合计测试 {test_count} 次）。"
        )
        overlap.append(
            {
                "pattern": "rectangle",
                "confidence": round(float(rect_conf), 3),
                "reason": "水平支撑阻力 + 多次边界测试",
            }
        )
        invalid_conditions.append("放量有效突破箱体边界。")

    if expanding_candidate:
        overlap.append(
            {
                "pattern": "expanding_triangle",
                "confidence": 0.66,
                "reason": "高点抬升且低点下移，振幅扩大",
            }
        )
        evidence.append("高点抬高且低点下移，价格振幅扩大。")
        invalid_conditions.append("振幅重新收窄并回归中轴。")

    if channel_up_candidate:
        overlap.append(
            {
                "pattern": "channel_up",
                "confidence": 0.64,
                "reason": "swing highs / lows 同步上移",
            }
        )
        evidence.append("高低点同步上移，偏上行通道。")

    if channel_down_candidate:
        overlap.append(
            {
                "pattern": "channel_down",
                "confidence": 0.64,
                "reason": "swing highs / lows 同步下移",
            }
        )
        evidence.append("高低点同步下移，偏下行通道。")

    if not overlap:
        if trend == "偏多":
            overlap.append({"pattern": "markup", "confidence": 0.58, "reason": "趋势偏多但通道尚不清晰"})
            invalid_conditions.append("趋势反向并跌破关键支撑。")
        elif trend == "偏空":
            overlap.append({"pattern": "markdown", "confidence": 0.58, "reason": "趋势偏空但通道尚不清晰"})
            invalid_conditions.append("趋势反向并突破关键阻力。")
        else:
            overlap.append({"pattern": "unknown", "confidence": 0.45, "reason": "结构混合，方向未确认"})
            invalid_conditions.append("出现方向性放量突破并连续确认。")
            evidence.append(f"当前为混合结构，区间宽度 {width_pct:.2f}%。")

    overlap = sorted(overlap, key=lambda x: float(x.get("confidence") or 0.0), reverse=True)

    # Wyckoff phase（简化版）
    close_recent = closes[-duration_bars:] if len(closes) >= duration_bars else closes
    battle_zones: list[dict[str, Any]] = []
    for center in [range_low, (range_low + range_high) / 2.0, range_high]:
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

    spring_upthrust_detected = _detect_spring_upthrust_v2(
        last_high=last_high,
        last_low=last_low,
        last_close=last_close,
        range_high=range_high,
        range_low=range_low,
    )
    wyckoff_signals = _detect_wyckoff_signals_v2(
        highs=highs,
        lows=lows,
        closes=closes,
        volumes=volumes,
    )
    spring_upthrust_detected = spring_upthrust_detected or bool(wyckoff_signals.get("spring_detected")) or bool(wyckoff_signals.get("upthrust_detected"))
    wyckoff_phase = wyckoff_signals.get("phase")
    wyckoff_phase_transition = wyckoff_signals.get("phase_transition")
    overlap_patterns = {str(item.get("pattern") or "") for item in overlap}
    if wyckoff_phase and wyckoff_phase not in overlap_patterns:
        overlap.append(
            {
                "pattern": wyckoff_phase,
                "confidence": 0.6,
                "reason": "Wyckoff 阶段识别补充",
            }
        )
    evidence.extend(list(wyckoff_signals.get("evidence") or []))
    overlap = sorted(overlap, key=lambda x: float(x.get("confidence") or 0.0), reverse=True)
    structure_label = str(overlap[0].get("pattern") or "unknown")
    confidence = float(overlap[0].get("confidence") or 0.45)

    return {
        "structure_label": structure_label,
        "confidence": round(float(confidence), 3),
        "wyckoff_phase": wyckoff_phase,
        "wyckoff_phase_transition": wyckoff_phase_transition,
        "wyckoff_signals": list(wyckoff_signals.get("signals") or []),
        "wyckoff_confidence": wyckoff_signals.get("confidence"),
        "spring_upthrust_detected": spring_upthrust_detected,
        "swing_highs": swing_highs[-5:],
        "swing_lows": swing_lows[-5:],
        "current_range": {
            "high": _round_price(range_high),
            "low": _round_price(range_low),
            "width_pct": _round_price(width_pct),
            "duration_bars": duration_bars,
            "initial_width_pct": _round_price(initial_width_pct),
            "current_width_pct": _round_price(current_width_pct),
        },
        "volume_trend": volume_trend,
        "battle_zones": battle_zones[:3],
        "multi_pattern_overlap": overlap[:3],
        "evidence": evidence[:6],
        "invalid_conditions": invalid_conditions[:3],
        "meta": {"symbol": symbol, "interval": interval},
    }

