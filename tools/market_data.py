"""市场数据工具 — 对齐 Stock_Analysis 三条数据源链

数据源分工（与 Stock_Analysis/cli 完全一致）：
- A 股 / 美股 / 港股: tickflow (https://free-api.tickflow.org)
- 加密货币:           gate.io REST API
- 黄金/贵金属:        goldapi (https://gold-api.cn)

不再依赖 akshare。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from langchain_core.tools import tool
from config.runtime_config import get_tickflow_api_key, get_goldapi_base_url, get_goldapi_appkey
from utils.logging_utils import get_logger

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


# ── goldapi: 黄金 / 贵金属 ──

_VARIETIES_CACHE: list[dict[str, Any]] | None = None


def _gold_api_base() -> str:
    return get_goldapi_base_url()


def _gold_api_appkey() -> str:
    return get_goldapi_appkey()


def _fetch_gold_varieties() -> list[dict[str, Any]]:
    """获取贵金属品种列表"""
    url = f"{_gold_api_base()}/api/v1/gold/varieties"
    try:
        payload = _http_get_json(url)
    except Exception as e:
        logger.warning("[goldapi] varieties 请求失败: %s", e)
        return []
    if str(payload.get("success")) != "1":
        logger.warning("[goldapi] varieties 失败: %s", payload)
        return []
    result = payload.get("result")
    return result if isinstance(result, list) else []


def _get_varieties_cached() -> list[dict[str, Any]]:
    global _VARIETIES_CACHE
    if _VARIETIES_CACHE is None:
        _VARIETIES_CACHE = _fetch_gold_varieties()
    return _VARIETIES_CACHE


def _resolve_gold_id(ticker: str) -> str | None:
    """将品种代码解析为 goldid"""
    raw = ticker.strip()
    if not raw:
        return None
    if raw.isdigit() or raw.startswith(("hf_", "nf_")):
        return raw
    key = raw.upper().replace("＋", "+")
    for row in _get_varieties_cached():
        v = str(row.get("variety") or "").strip().upper()
        if v == key:
            gid = str(row.get("goldId") or "").strip()
            if gid:
                return gid
    return None


def _parse_dt_any(s: str) -> datetime:
    """解析 API 返回的时间字符串"""
    raw = (s or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        n = 19 if "H" in fmt else 10
        try:
            return datetime.strptime(raw[:n], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    day = raw[:10].replace("/", "-")
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _row_from_item(it: dict[str, Any]) -> dict[str, Any] | None:
    """从 goldapi history 单条记录抽取 OHLCV"""
    if not isinstance(it, dict):
        return None
    date_keys = (
        "timestamp", "businessDate", "bizDate", "tradeDate",
        "date", "dt", "datetime", "pubDate", "updateTime",
    )
    dt_raw = None
    for k in date_keys:
        if it.get(k):
            dt_raw = str(it[k])
            break
    if not dt_raw:
        return None
    try:
        ts = _parse_dt_any(dt_raw)
    except Exception:
        return None

    def pick_float(*keys: str) -> float | None:
        for k in keys:
            v = it.get(k)
            if v is None or v == "":
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None

    close = pick_float("close", "closePrice", "lastPrice", "last", "settle", "settlementPrice", "price", "value")
    open_ = pick_float("open", "openPrice", "openingPrice")
    high = pick_float("high", "highPrice", "maxPrice", "highPx")
    low = pick_float("low", "lowPrice", "minPrice", "lowPx")
    vol = pick_float("volume", "vol", "tradeAmount", "turnover", "amount")
    if close is None:
        return None
    if open_ is None:
        open_ = close
    if high is None:
        high = max(open_, close)
    if low is None:
        low = min(open_, close)
    if vol is None:
        vol = 0.0
    return {
        "time": ts.isoformat(),
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(vol),
    }


def _rows_from_history_result(result: Any) -> list[dict[str, Any]]:
    """解析 goldapi 返回的 result 字段"""
    if result is None:
        return []
    if isinstance(result, list):
        candidates = result
    elif isinstance(result, dict):
        for k in ("list", "rows", "records", "data", "items", "points", "dtList"):
            v = result.get(k)
            if isinstance(v, list):
                candidates = v
                break
        else:
            dt = result.get("dtList")
            if isinstance(dt, dict):
                for _k, v in dt.items():
                    if isinstance(v, list) and v:
                        candidates = v
                        break
                else:
                    candidates = []
            else:
                candidates = []
    else:
        return []

    out: list[dict[str, Any]] = []
    for it in candidates:
        if isinstance(it, dict):
            row = _row_from_item(it)
            if row:
                out.append(row)
    return out


def _aggregate_rows(rows: list[dict[str, Any]], *, interval: str) -> list[dict[str, Any]]:
    """将小时线聚合为目标周期（4h / 1d）"""
    if interval == "1h":
        out = list(rows)
        out.sort(key=lambda x: x["time"])
        return out

    from collections import defaultdict
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dt = datetime.fromisoformat(str(row.get("time") or ""))
        bucket_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        if interval == "4h":
            bucket_dt = dt.replace(hour=(dt.hour // 4) * 4, minute=0, second=0, microsecond=0)
        buckets[bucket_dt.isoformat()].append(row)

    out: list[dict[str, Any]] = []
    for key in sorted(buckets.keys()):
        xs = sorted(buckets[key], key=lambda x: str(x.get("time") or ""))
        out.append({
            "time": key,
            "open": float(xs[0]["open"]),
            "high": max(float(x["high"]) for x in xs),
            "low": min(float(x["low"]) for x in xs),
            "close": float(xs[-1]["close"]),
            "volume": sum(float(x.get("volume") or 0.0) for x in xs),
        })
    return out


def _fetch_gold_kline(symbol: str, interval: str, limit: int = 200) -> dict[str, Any]:
    """使用 goldapi 获取黄金 / 贵金属 K 线"""
    iv = (interval or "1d").strip().lower()
    if iv == "1day":
        iv = "1d"
    if iv not in ("1h", "4h", "1d"):
        iv = "1d"

    appkey = _gold_api_appkey()
    if not appkey:
        return {"error": "goldapi 缺少 appkey，请设置 GOLD_API_APPKEY", "status": "error"}

    gold_id = _resolve_gold_id(symbol)
    if not gold_id:
        return {"error": f"未找到贵金属品种映射: {symbol}（请使用 Au9999 / 1053 / hf_XAU 等代码）", "status": "error"}

    lim = max(30, min(limit, 5000))

    # 计算日期范围
    if iv == "1h":
        span_days = min(max(int(lim / 6) + 20, 30), 4000)
        fetch_limit = min(max(lim * 4, 400), 5000)
    elif iv == "4h":
        span_days = min(max(int(lim / 3) + 45, 60), 4000)
        fetch_limit = min(max(lim * 8, 400), 5000)
    else:
        span_days = min(max(int(lim * 2.5) + 30, 120), 4000)
        fetch_limit = min(max(lim * 16, 400), 5000)

    end_d = datetime.now(timezone.utc).date()
    start_d = end_d - timedelta(days=span_days)

    params = {
        "goldid": gold_id,
        "start_date": start_d.isoformat(),
        "end_date": end_d.isoformat(),
        "limit": str(fetch_limit),
        "appkey": appkey,
    }
    url = f"{_gold_api_base()}/api/v1/gold/history?{urlencode(params)}"

    try:
        payload = _http_get_json(url, timeout=45.0)
    except Exception as e:
        return {"error": f"goldapi 请求失败: {e}", "status": "error"}

    if str(payload.get("success")) != "1":
        return {"error": f"goldapi history 失败: {payload.get('msg', payload)}", "status": "error"}

    result = payload.get("result")
    rows = _rows_from_history_result(result)

    if not rows:
        return {"error": f"goldapi 未返回 {symbol} 有效数据", "status": "error"}

    # 过滤 + 聚合 + 排序
    rows = [r for r in rows if r["open"] > 0 and r["high"] > 0 and r["low"] > 0 and r["close"] > 0]
    rows = _aggregate_rows(rows, interval=iv)
    rows.sort(key=lambda x: x["time"])

    if len(rows) < 30:
        logger.warning("[goldapi] 有效 K 线不足 30 根（实际 %d），结果可能不完整", len(rows))

    return {
        "symbol": symbol,
        "interval": interval,
        "market": "gold",
        "data": rows[-lim:],
        "count": len(rows[-lim:]),
        "status": "success",
    }


# ── 主入口 ──


@tool
def fetch_market_data(symbol: str, interval: str = "1d") -> dict[str, Any]:
    """获取标的 K 线数据，支持 A 股、美股、港股、加密货币、黄金

    数据源:
    - A 股 / 美股 / 港股: tickflow
    - 加密货币: gate.io
    - 黄金 / 贵金属: goldapi

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
        return _fetch_gold_kline(symbol, interval)
    else:
        # 未知市场，尝试 tickflow
        logger.warning("未知市场类型 %s for %s，尝试 tickflow", market, symbol)
        return _fetch_tickflow_kline(symbol, interval, market=market)
