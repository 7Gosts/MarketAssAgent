"""市场数据工具 — 对齐 Stock_Analysis 三条数据源链

数据源分工（与 Stock_Analysis/cli 完全一致）：
- A 股 / 美股 / 港股: tickflow (https://free-api.tickflow.org)
- 加密货币:           gate.io REST API
- 黄金/贵金属（国内）: AKShare (futures_zh_daily_sina / futures_zh_minute_sina, AU0 沪金连续)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

from langchain_core.tools import tool
from config.runtime_config import get_tickflow_api_key
from utils.logging_utils import get_logger

import akshare as ak

logger = get_logger(__name__)


# ── 通用 HTTP ──


def _http_get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 30.0) -> Any:
    """通用 HTTP GET，返回 JSON 对象。"""
    h = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    if headers:
        h.update(headers)
    req = Request(url, headers=h)
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


# ── 类型映射 ──


def _detect_market(symbol: str) -> str:
    """根据标的代码推断市场类型（兼容 Stock_Analysis 的分类）"""
    s = symbol.upper().replace(" ", "").replace("-", "_")

    # 加密货币
    crypto_keywords = [
        "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA",
        "AVAX", "DOT", "LINK", "MATIC", "USDT", "USDC",
    ]
    if any(kw in s for kw in crypto_keywords) and "USDT" in s:
        return "crypto"

    # A 股：6 位纯数字 或 带后缀
    stripped = s.split(".")[0]
    if stripped.isdigit() and len(stripped) == 6:
        return "a_share"
    if s.endswith((".SH", ".SZ", ".BJ")):
        return "a_share"

    # 黄金
    if "AU" in s or "GOLD" in s or "XAU" in s:
        return "gold"

    # 美股：常见 ticker 或 .US 后缀
    us_tickers = {"AAPL", "NVDA", "TSLA", "MSFT", "GOOG", "AMZN", "META"}
    if stripped in us_tickers or s.endswith(".US"):
        return "us_equity"

    # 港股
    if s.endswith(".HK"):
        return "hk_equity"

    return "unknown"


# ── tickflow: A 股 / 美股 / 港股 ──


def _to_tickflow_symbol(ticker: str, market: str) -> str:
    """将本地 ticker 转为 tickflow 格式（对齐 Stock_Analysis/analysis/price_feeds.py）"""
    raw = ticker.strip().upper()
    mkt = market.strip().upper()
    if "." in raw:
        return raw
    if mkt in ("A_SHARE", "CN"):
        if raw.startswith(("6", "9")):
            return f"{raw}.SH"
        if raw.startswith(("0", "3")):
            return f"{raw}.SZ"
    if mkt in ("US_EQUITY", "US"):
        return f"{raw}.US"
    if mkt in ("HK_EQUITY", "HK"):
        return f"{raw}.HK"
    return raw


def _fetch_tickflow_kline(symbol: str, interval: str, limit: int = 200, market: str = "") -> dict[str, Any]:
    """使用 tickflow 获取 K 线（A 股 / 美股 / 港股）"""
    try:
        tf_symbol = _to_tickflow_symbol(symbol, market)
    except Exception:
        tf_symbol = symbol

    api_key = get_tickflow_api_key()
    base = "https://api.tickflow.org" if api_key else "https://free-api.tickflow.org"
    period = interval if interval in {"1d", "1w", "1M", "1Q", "1Y"} else "1d"

    query = urlencode({
        "symbol": tf_symbol,
        "period": period,
        "count": str(max(30, min(limit, 10000))),
        "adjust": "none",
    })
    url = f"{base}/v1/klines?{query}"

    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key

    try:
        payload = _http_get_json(url, headers=headers)
    except Exception as e:
        return {"error": f"tickflow 请求失败: {e}", "status": "error"}

    data = payload.get("data") or {}
    ts = data.get("timestamp") or []
    opens = data.get("open") or []
    highs = data.get("high") or []
    lows = data.get("low") or []
    closes = data.get("close") or []
    vols = data.get("volume") or []

    n = min(len(ts), len(opens), len(highs), len(lows), len(closes))
    rows: list[dict[str, Any]] = []
    for i in range(n):
        try:
            t_ms = int(ts[i])
            dt = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc)
            rows.append({
                "time": dt.isoformat(),
                "open": float(opens[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i]),
                "volume": float(vols[i]) if i < len(vols) else 0.0,
            })
        except (TypeError, ValueError):
            continue

    # 过滤无效行 + 排序
    rows = [r for r in rows if r["open"] > 0 and r["high"] > 0 and r["low"] > 0 and r["close"] > 0]
    rows.sort(key=lambda x: x["time"])

    if not rows:
        return {"error": f"tickflow 未返回 {symbol} 有效数据", "status": "error"}

    return {
        "symbol": symbol,
        "interval": interval,
        "market": _detect_market(symbol),
        "data": rows,
        "count": len(rows),
        "status": "success",
    }


# ── gate.io: 加密货币 ──


def _to_gateio_pair(ticker: str) -> str:
    """将 ticker 转为 gate.io 交易对格式"""
    raw = ticker.strip().upper().replace("-", "_")
    if "_" in raw:
        return raw
    if raw.endswith("USDT"):
        return f"{raw[:-4]}_USDT"
    return raw


def _fetch_crypto_kline(symbol: str, interval: str, limit: int = 200) -> dict[str, Any]:
    """使用 gate.io REST API 获取加密货币 K 线"""
    interval_map = {
        "1m": "1m", "5m": "5m", "15m": "15m",
        "1h": "1h", "4h": "4h", "1d": "1d", "1w": "7d",
    }
    gate_interval = interval_map.get(interval, "1d")
    pair = _to_gateio_pair(symbol)
    lim = max(30, min(limit, 1000))

    query = urlencode({"currency_pair": pair, "interval": gate_interval, "limit": str(lim)})
    url = f"https://api.gateio.ws/api/v4/spot/candlesticks?{query}"

    try:
        data = _http_get_json(url, timeout=45.0)
    except Exception as e:
        return {"error": f"加密货币数据获取失败: {e}", "status": "error"}

    if not isinstance(data, list):
        return {"error": f"gate.io 返回异常: {data!r}", "status": "error"}

    rows: list[dict[str, Any]] = []
    for c in data:
        if not isinstance(c, list) or len(c) < 7:
            continue
        try:
            ts_sec = int(c[0])
            dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
            rows.append({
                "time": dt.isoformat(),
                "open": float(c[5]),
                "high": float(c[3]),
                "low": float(c[4]),
                "close": float(c[2]),
                "volume": float(c[6]),
            })
        except (TypeError, ValueError):
            continue

    # 过滤无效行 + 排序
    rows = [r for r in rows if r["open"] > 0 and r["high"] > 0 and r["low"] > 0 and r["close"] > 0]
    rows.sort(key=lambda x: x["time"])

    if not rows:
        return {"error": f"gate.io 未返回 {symbol} 有效数据", "status": "error"}

    return {
        "symbol": symbol,
        "interval": interval,
        "market": "crypto",
        "data": rows[-lim:],
        "count": len(rows[-lim:]),
        "status": "success",
    }


# ── 黄金（国内沪金期货连续 AU0）：AKShare ──


def _fetch_au0_akshare_kline(interval: str = "1d", limit: int = 200) -> dict[str, Any]:
    """使用 AKShare 获取沪金期货连续 (AU0) K 线"""
    iv = (interval or "1d").strip().lower()
    if iv in ("1day", "1d", "daily"):
        iv = "1d"
    elif iv in ("60m", "60min", "1h"):
        iv = "60m"

    try:
        if iv == "1d":
            df = ak.futures_zh_daily_sina(symbol="AU0")
        elif iv == "60m":
            df = ak.futures_zh_minute_sina(symbol="AU0", period="60")
        else:
            logger.warning("[AKShare] 不支持的黄金周期 %s，回退到日线", interval)
            df = ak.futures_zh_daily_sina(symbol="AU0")
            iv = "1d"
    except Exception as e:
        return {"error": f"AKShare 拉取 AU0 失败: {e}", "status": "error"}

    if df is None or df.empty:
        return {"error": "AKShare 返回空数据 (AU0)", "status": "error"}

    df = df.rename(columns={
        "date": "time", "open": "open", "high": "high",
        "low": "low", "close": "close", "volume": "volume", "hold": "hold",
    })

    recent = df.tail(limit).copy()
    rows: list[dict[str, Any]] = []
    for _, r in recent.iterrows():
        rows.append({
            "time": str(r.get("time", ""))[:19].replace(" ", "T"),
            "open": float(r.get("open", 0)),
            "high": float(r.get("high", 0)),
            "low": float(r.get("low", 0)),
            "close": float(r.get("close", 0)),
            "volume": float(r.get("volume", 0)),
        })

    return {
        "symbol": "AU0",
        "interval": interval,
        "market": "gold",
        "data": rows,
        "count": len(rows),
        "status": "success",
    }


# ── 主入口 ──


@tool
def fetch_market_data(symbol: str, interval: str = "1d") -> dict[str, Any]:
    """获取标的 K 线数据，支持 A 股、美股、港股、加密货币、黄金

    数据源:
    - A 股 / 美股 / 港股: tickflow
    - 加密货币: gate.io
    - 黄金（国内沪金连续）: AKShare (AU0)

    Args:
        symbol: 标的代码 (e.g. 600519, BTCUSDT, NVDA, Au9999)
        interval: 时间周期 (1m, 5m, 15m, 1h, 4h, 1d, 1w, 1M)

    Returns:
        包含 K 线数据的字典，含 symbol, interval, market, data, count, status 字段
    """
    market = _detect_market(symbol)
    logger.info("fetch_market_data: symbol=%s, market=%s, interval=%s", symbol, market, interval)

    if market in ("a_share", "us_equity", "hk_equity"):
        return _fetch_tickflow_kline(symbol, interval, market=market)
    elif market == "crypto":
        return _fetch_crypto_kline(symbol, interval)
    elif market == "gold":
        return _fetch_au0_akshare_kline(interval=interval)
    else:
        # 未知市场，尝试 tickflow
        logger.warning("未知市场类型 %s for %s，尝试 tickflow", market, symbol)
        return _fetch_tickflow_kline(symbol, interval, market=market)
