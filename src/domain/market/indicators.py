"""Indicator and level calculation domain logic."""

from __future__ import annotations

from typing import Any

from config.runtime_config import get_ma_system

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
