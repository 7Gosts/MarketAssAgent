"""技术分析工具 — 基于真实 K 线数据计算技术指标

核心工具:
- analyze_market: 全面技术分析（基于真实数据 + Snapshot 保存）
- get_key_levels: 关键支撑/阻力位（基于分形方法）
- evaluate_structure: 评估市场结构（123法则、均线、量价）
- analyze_multi: 多标的对比分析
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

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
) -> str:
    """量价结构分析"""
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

    return "，".join(parts) if parts else "数据不足，结构分析暂不可用"


def _calculate_confidence(
    trend: str, ma_values: dict[str, float | None], key_levels: dict[str, list]
) -> int:
    """计算置信度 0-100"""
    score = 50  # 基础分

    # MA 一致性加分
    ma_short = ma_values.get("MA_short")
    ma_mid = ma_values.get("MA_mid")
    ma_long = ma_values.get("MA_long")

    if ma_short and ma_mid and ma_long:
        if trend == "偏多" and ma_short > ma_mid > ma_long:
            score += 20
        elif trend == "偏空" and ma_short < ma_mid < ma_long:
            score += 20
        else:
            score -= 10  # 趋势与 MA 不一致

    # 关键位数量加分
    supports = key_levels.get("support", [])
    resistances = key_levels.get("resistance", [])
    if len(supports) >= 2:
        score += 10
    if len(resistances) >= 2:
        score += 10

    # 趋势清晰度加分
    if trend in ("偏多", "偏空"):
        score += 10

    return max(0, min(100, score))


# ── 核心工具 ──

@tool
def analyze_market(symbol: str, interval: str = "1d", force_refresh: bool = False) -> Dict[str, Any]:
    """【核心工具】全面技术分析 — 基于真实 K 线数据

    Args:
        symbol: 标的代码 (e.g. BTCUSDT, 600519.SH, NVDA, AU9999)
        interval: 时间周期 (1m, 5m, 15m, 1h, 4h, 1d, 1w)
        force_refresh: 是否强制刷新数据

    Returns:
        详细分析结果 + AnalysisSnapshot
    """
    logger.info("开始分析 %s %s 周期", symbol, interval)

    # 1. 获取真实数据
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

    # 2. 提取价格序列
    closes = [k.get("close", k.get("收盘", 0)) for k in klines]
    closes = [c for c in closes if c and c > 0]

    if not closes:
        return {
            "status": "error",
            "symbol": symbol,
            "interval": interval,
            "message": "收盘价数据为空",
        }

    # 3. 计算 MA
    ma_config = _get_ma_config(symbol)
    ma_values: dict[str, float | None] = {}
    for name, period in [("MA_short", ma_config["short"]), ("MA_mid", ma_config["mid"]), ("MA_long", ma_config["long"])]:
        ma_values[name] = _calculate_ma(closes, period)

    # 简化 key 的显示名
    ma_display: dict[str, Any] = {}
    for k, v in ma_values.items():
        period = ma_config[k.replace("MA_", "").replace("short", "short").replace("mid", "mid").replace("long", "long")]
        display_key = f"MA{period}"
        ma_display[display_key] = v

    # 4. 判断趋势
    trend = _determine_trend(closes, ma_values)

    # 5. 计算关键位
    key_levels = _calculate_key_levels(klines)

    # 6. 量价结构分析
    structure = _analyze_structure(klines, ma_values)

    # 7. 置信度
    confidence = _calculate_confidence(trend, ma_values, key_levels)

    # 8. 构建分析结果
    analysis_result = {
        "symbol": symbol,
        "interval": interval,
        "timestamp": datetime.now().isoformat(),
        "current_price": closes[-1],
        "trend": trend,
        "key_levels": key_levels,
        "structure": structure,
        "indicators": {
            "ma_values": ma_display,
            "ma_trend": f"MA排列: {trend}",
        },
        "confidence": confidence,
        "raw_insights": f"{symbol} 在 {interval} 周期呈{trend}结构，置信度{confidence}%。",
    }

    # 9. 保存 Snapshot（解决追问上下文丢失的核心机制）
    snapshot = snapshot_manager.save_snapshot(
        session_id="default",
        snapshot_data=analysis_result,
    )

    return {
        "status": "success",
        "symbol": symbol,
        "interval": interval,
        "analysis": analysis_result,
        "snapshot": snapshot,
        "message": f"{symbol} {interval} 技术分析完成: {trend}，置信度{confidence}%",
    }


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
        return {
            "symbol": symbol,
            "structure_summary": snapshot.get("structure", "震荡"),
            "trend_strength": "中强" if snapshot.get("trend") in ("偏多", "偏空") else "中弱",
            "confidence": snapshot.get("confidence", 60),
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

    structure = _analyze_structure(klines, ma_values)
    trend = _determine_trend(closes, ma_values)

    return {
        "symbol": symbol,
        "structure_summary": structure,
        "trend": trend,
        "trend_strength": "中强" if trend in ("偏多", "偏空") else "中弱",
        "message": "基于真实数据的结构评估",
    }


@tool
def analyze_multi(symbols: str, interval: str = "1d") -> Dict[str, Any]:
    """同时分析多个标的技术面

    Args:
        symbols: 逗号分隔的标的列表 (e.g. "BTC_USDT,ETH_USDT,SOL_USDT")
        interval: 统一时间周期

    Returns:
        多标的分析结果 + 横向对比
    """
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        return {"status": "error", "message": "未提供标的列表"}

    if len(symbol_list) > 10:
        return {"status": "error", "message": "一次最多分析 10 个标的"}

    results: dict[str, Any] = {}
    for sym in symbol_list:
        results[sym] = analyze_market.invoke({"symbol": sym, "interval": interval})

    # 横向对比
    comparison = _compare_symbols(results)

    return {
        "status": "success",
        "symbols": symbol_list,
        "interval": interval,
        "analyses": results,
        "comparison": comparison,
        "message": f"已完成 {len(symbol_list)} 个标的的对比分析",
    }


def _compare_symbols(analyses: dict[str, dict]) -> dict[str, Any]:
    """横向对比多标的的趋势强度和置信度"""
    summary: list[dict[str, Any]] = []

    for sym, result in analyses.items():
        if result.get("status") == "error":
            summary.append({"symbol": sym, "trend": "N/A", "confidence": 0, "status": "error"})
            continue

        analysis = result.get("analysis", result)
        summary.append({
            "symbol": sym,
            "trend": analysis.get("trend", "震荡"),
            "confidence": analysis.get("confidence", 0),
            "current_price": analysis.get("current_price"),
        })

    # 找最强/最弱
    valid = [s for s in summary if s.get("status") != "error"]
    strongest = max(valid, key=lambda x: x.get("confidence", 0)) if valid else None
    weakest = min(valid, key=lambda x: x.get("confidence", 0)) if valid else None

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


def get_technical_tools():
    """返回技术分析相关工具"""
    return [analyze_market, get_key_levels, evaluate_structure, analyze_multi]
