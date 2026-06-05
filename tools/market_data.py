"""市场数据工具 — 对接真实数据源（AKShare + gate.io）

支持市场类型：
- A 股: 使用 AKShare stock_zh_a_hist
- 加密货币: 使用 gate.io REST API /api/v4/spot/candlesticks
- 美股: 使用 AKShare stock_us_hist
- 黄金: 使用 AKShare（按上海金 Au9999 处理）
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from langchain_core.tools import tool


# ── 类型映射 ──

def _detect_market(symbol: str) -> str:
    """根据标的代码推断市场类型"""
    s = symbol.upper().replace(" ", "").replace("-", "_")

    # 加密货币关键字
    crypto_keywords = [
        "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA",
        "AVAX", "DOT", "LINK", "MATIC", "USDT", "USDC",
    ]
    if any(kw in s for kw in crypto_keywords) and "USDT" in s:
        return "crypto"

    # A 股：6 位纯数字 或 带后缀 600519.SH / 000001.SZ
    stripped = s.split(".")[0]
    if stripped.isdigit() and len(stripped) == 6:
        return "a_share"
    if s.endswith((".SH", ".SZ", ".BJ")):
        return "a_share"

    # 黄金
    if "AU" in s or "GOLD" in s or "XAU" in s:
        return "gold"

    # 美股：常见 ticker
    us_tickers = {"AAPL", "NVDA", "TSLA", "MSFT", "GOOG", "AMZN", "META"}
    if stripped in us_tickers:
        return "us_equity"

    return "unknown"


# ── A 股数据源 ──

def _fetch_a_share_kline(symbol: str, interval: str, limit: int = 200) -> dict[str, Any]:
    """使用 AKShare 获取 A 股 K 线"""
    try:
        import akshare as ak
        import pandas as pd
    except ImportError:
        return {"error": "akshare 未安装，请 pip install akshare", "status": "error"}

    # 去掉后缀（AKShare 用纯数字代码）
    code = symbol.split(".")[0] if "." in symbol else symbol

    # 适配 period
    period_map = {"1d": "daily", "1w": "weekly", "1M": "monthly"}
    period = period_map.get(interval, "daily")

    try:
        df = ak.stock_zh_a_hist(symbol=code, period=period, adjust="qfq")
        if df is None or df.empty:
            return {"error": f"未获取到 {symbol} 数据", "status": "error"}

        df = df.tail(limit)

        # 标准化列名
        col_map = {
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "涨跌幅": "change_pct", "涨跌额": "change", "换手率": "turnover",
        }
        df = df.rename(columns=col_map)

        records = json.loads(df.to_json(orient="records", date_format="iso"))
        return {
            "symbol": symbol,
            "interval": interval,
            "market": "a_share",
            "data": records,
            "count": len(records),
            "status": "success",
        }
    except Exception as e:
        return {"error": f"A 股数据获取失败: {e}", "status": "error"}


# ── 加密货币数据源 ──

def _fetch_crypto_kline(symbol: str, interval: str, limit: int = 200) -> dict[str, Any]:
    """使用 gate.io REST API 获取加密货币 K 线（无需 SDK）"""
    interval_map = {
        "1m": "1m", "5m": "5m", "15m": "15m",
        "1h": "1h", "4h": "4h", "1d": "1d", "1w": "7d",
    }
    gate_interval = interval_map.get(interval, "1d")

    # gate.io 使用下划线格式: BTC_USDT
    pair = symbol.upper().replace("-", "_")

    url = (
        f"https://api.gateio.ws/api/v4/spot/candlesticks"
        f"?currency_pair={pair}&interval={gate_interval}&limit={limit}"
    )

    try:
        resp = httpx.get(url, timeout=15.0)
        resp.raise_for_status()
        raw = resp.json()

        # gate 返回 [ts, vol_open, vol_high, vol_low, vol_close, close, high, low, open]
        klines = []
        for item in raw:
            klines.append({
                "timestamp": int(item[0]),
                "open": float(item[5]),
                "high": float(item[3]),
                "low": float(item[4]),
                "close": float(item[2]),
                "volume": float(item[1]),
            })

        return {
            "symbol": symbol,
            "interval": interval,
            "market": "crypto",
            "data": klines[-limit:],
            "count": len(klines[-limit:]),
            "status": "success",
        }
    except Exception as e:
        return {"error": f"加密货币数据获取失败: {e}", "status": "error"}


# ── 美股数据源 ──

def _fetch_us_equity_kline(symbol: str, interval: str, limit: int = 200) -> dict[str, Any]:
    """使用 AKShare 获取美股 K 线"""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装，请 pip install akshare", "status": "error"}

    period_map = {"1d": "daily", "1w": "weekly", "1M": "monthly"}
    period = period_map.get(interval, "daily")

    try:
        df = ak.stock_us_hist(symbol=symbol, period=period, adjust="qfq")
        if df is None or df.empty:
            return {"error": f"未获取到 {symbol} 数据", "status": "error"}

        df = df.tail(limit)

        col_map = {
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "涨跌幅": "change_pct", "涨跌额": "change", "换手率": "turnover",
        }
        df = df.rename(columns=col_map)

        records = json.loads(df.to_json(orient="records", date_format="iso"))
        return {
            "symbol": symbol,
            "interval": interval,
            "market": "us_equity",
            "data": records,
            "count": len(records),
            "status": "success",
        }
    except Exception as e:
        return {"error": f"美股数据获取失败: {e}", "status": "error"}


# ── 黄金数据源 ──

def _fetch_gold_kline(symbol: str, interval: str, limit: int = 200) -> dict[str, Any]:
    """使用 AKShare 获取黄金（Au9999）K 线"""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装，请 pip install akshare", "status": "error"}

    try:
        df = ak.spot_hist_sge(symbol="Au99.99")
        if df is None or df.empty:
            return {"error": f"未获取到黄金数据", "status": "error"}

        df = df.tail(limit)

        col_map = {
            "日期": "date", "开盘价": "open", "收盘价": "close",
            "最高价": "high", "最低价": "low", "成交量": "volume",
        }
        df = df.rename(columns=col_map)

        records = json.loads(df.to_json(orient="records", date_format="iso"))
        return {
            "symbol": symbol,
            "interval": interval,
            "market": "gold",
            "data": records,
            "count": len(records),
            "status": "success",
        }
    except Exception as e:
        return {"error": f"黄金数据获取失败: {e}", "status": "error"}


# ── 主入口 ──

@tool
def fetch_market_data(symbol: str, interval: str = "1d") -> dict[str, Any]:
    """获取标的 K 线数据，支持 A 股、加密货币、美股、黄金

    Args:
        symbol: 标的代码 (e.g. 600519, BTC_USDT, NVDA, AU9999)
        interval: 时间周期 (1m, 5m, 15m, 1h, 4h, 1d, 1w, 1M)

    Returns:
        包含 K 线数据的字典，含 symbol, interval, market, data, count, status 字段
    """
    market = _detect_market(symbol)

    if market == "a_share":
        return _fetch_a_share_kline(symbol, interval)
    elif market == "crypto":
        return _fetch_crypto_kline(symbol, interval)
    elif market == "us_equity":
        return _fetch_us_equity_kline(symbol, interval)
    elif market == "gold":
        return _fetch_gold_kline(symbol, interval)
    else:
        return {
            "error": f"暂不支持 {symbol} 对应的市场类型: {market}",
            "status": "unsupported",
            "symbol": symbol,
            "interval": interval,
        }