"""市场数据工具。

数据源分工：
- A 股 / 美股 / 港股: AKShare
- 加密货币: gate.io REST API
- 黄金/贵金属（国内）: AKShare (AU0 沪金连续)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import akshare as ak
import pandas as pd
from langchain_core.tools import tool
from core.asset_catalog import get_asset_catalog, register_discovered_asset
from core.asset_discovery import discover_asset_candidates
from utils.logging_utils import get_logger

logger = get_logger(__name__)
_DISCOVERY_AUTO_REGISTER_CONFIDENCE = 0.75
_SEMANTIC_PREFIX_PAT = re.compile(r"^(?:看看|看下|看一下|请问|帮我|分析|查询|查下|查一下)")
_SEMANTIC_SUFFIX_PAT = re.compile(r"(?:股份有限公司|有限责任公司|有限公司|公司|集团|股份|控股|股票|行情|股价|走势)$")
_SEMANTIC_ASCII_STOPWORDS = {"STOCK", "SHARE", "SHARES", "PRICE", "COMPANY", "CORP", "CORPORATION", "THE"}


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


def _market_from_catalog_market(market: str) -> str:
    raw = str(market or "").strip().upper()
    if raw == "CN":
        return "a_share"
    if raw == "US":
        return "us_equity"
    if raw == "HK":
        return "hk_equity"
    if raw == "CRYPTO":
        return "crypto"
    if raw in {"PM", "COMMODITY"}:
        return "gold"
    return "unknown"


def _build_catalog_candidate(symbol: str) -> dict[str, Any] | None:
    row = get_asset_catalog().get(symbol)
    if not row:
        return None
    return {
        "symbol": str(row.get("symbol") or symbol).strip().upper(),
        "name": str(row.get("name") or symbol).strip() or symbol,
        "market": str(row.get("market") or "").strip().upper(),
        "data_symbol": str(row.get("data_symbol") or symbol).strip() or symbol,
        "research_keyword": str(row.get("research_keyword") or row.get("name") or symbol).strip() or symbol,
        "aliases": list(row.get("aliases") or []),
        "tags": list(row.get("tags") or []),
        "confidence": 1.0,
    }


def _candidate_view(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": str(candidate.get("symbol") or "").strip().upper(),
        "name": str(candidate.get("name") or "").strip(),
        "market": str(candidate.get("market") or "").strip().upper(),
        "confidence": round(float(candidate.get("confidence") or 0.0), 3),
    }


def _validate_discovered_candidate(candidate: dict[str, Any], interval: str = "1d") -> dict[str, Any] | None:
    market = _market_from_catalog_market(candidate.get("market"))
    symbol = str(candidate.get("symbol") or "").strip().upper()
    if not symbol or market not in {"a_share", "us_equity", "hk_equity"}:
        return None

    payload = _fetch_stock_akshare_kline(symbol=symbol, interval=interval, limit=60, market=market)
    if payload.get("status") != "success":
        return None
    out = dict(candidate)
    out["validated_market"] = market
    out["count"] = int(payload.get("count") or 0)
    return out


def _format_resolution_candidates(candidates: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for candidate in candidates[:3]:
        symbol = str(candidate.get("symbol") or "").strip().upper()
        name = str(candidate.get("name") or "").strip()
        market = str(candidate.get("market") or "").strip().upper()
        if symbol:
            parts.append(f"{name or symbol}({symbol}, {market or 'UNKNOWN'})")
    return "、".join(parts)


def _semantic_tokens(text: str) -> set[str]:
    raw = str(text or "").strip()
    if not raw:
        return set()

    tokens: set[str] = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", raw):
        core = chunk.strip()
        while core:
            next_core = _SEMANTIC_PREFIX_PAT.sub("", core)
            next_core = _SEMANTIC_SUFFIX_PAT.sub("", next_core)
            if next_core == core:
                break
            core = next_core.strip()
        if len(core) >= 2:
            tokens.add(core)

    for token in re.findall(r"[A-Z0-9]{2,}", raw.upper()):
        if token not in _SEMANTIC_ASCII_STOPWORDS:
            tokens.add(token)
    return tokens


def _is_semantically_consistent(query: str, candidate: dict[str, Any]) -> bool:
    query_tokens = _semantic_tokens(query)
    if not query_tokens:
        return True

    candidate_values = [
        candidate.get("name"),
        candidate.get("research_keyword"),
        *(candidate.get("aliases") or []),
    ]
    candidate_tokens: set[str] = set()
    for value in candidate_values:
        raw_value = str(value or "").strip()
        if not raw_value or raw_value == str(query or "").strip():
            continue
        candidate_tokens.update(_semantic_tokens(raw_value))

    for query_token in query_tokens:
        for candidate_token in candidate_tokens:
            if query_token in candidate_token or candidate_token in query_token:
                return True
    return False


def _resolve_market_symbol_internal(query: str, interval: str = "1d", *, auto_register: bool = True) -> dict[str, Any]:
    raw = str(query or "").strip()
    if not raw:
        return {"status": "error", "message": "请提供标的名称或代码"}

    catalog = get_asset_catalog()
    exact = _build_catalog_candidate(raw.upper())
    if exact:
        return {
            "status": "success",
            "query": raw,
            "symbol": exact["symbol"],
            "market": _market_from_catalog_market(exact.get("market")),
            "source": "catalog",
            "candidate": _candidate_view(exact),
        }

    catalog_hits = catalog.resolve_symbols_from_text(raw, min_score=80)
    if len(catalog_hits) == 1:
        matched = _build_catalog_candidate(catalog_hits[0])
        if matched:
            return {
                "status": "success",
                "query": raw,
                "symbol": matched["symbol"],
                "market": _market_from_catalog_market(matched.get("market")),
                "source": "catalog_alias",
                "candidate": _candidate_view(matched),
            }
    elif len(catalog_hits) > 1:
        candidates = [_candidate_view(_build_catalog_candidate(symbol) or {"symbol": symbol}) for symbol in catalog_hits[:3]]
        return {
            "status": "clarify",
            "query": raw,
            "message": f"未能唯一确定标的，请明确其中一个：{_format_resolution_candidates(candidates)}",
            "candidates": candidates,
            "source": "catalog_alias",
        }

    candidates = discover_asset_candidates(
        query=raw,
        tradable_assets=catalog.tradable_assets_for_prompt(),
        max_candidates=3,
    )
    deduped: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()
    for candidate in candidates:
        symbol = str(candidate.get("symbol") or "").strip().upper()
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        deduped.append(candidate)

    validated: list[dict[str, Any]] = []
    for candidate in deduped:
        checked = _validate_discovered_candidate(candidate, interval=interval)
        if checked:
            aliases = [str(alias).strip() for alias in (checked.get("aliases") or []) if str(alias).strip()]
            if raw not in aliases:
                aliases.append(raw)
            checked["aliases"] = aliases
            validated.append(checked)

    if len(validated) == 1:
        winner = validated[0]
        confidence = float(winner.get("confidence") or 0.0)
        semantic_match = _is_semantically_consistent(raw, winner)
        if not semantic_match:
            return {
                "status": "clarify",
                "query": raw,
                "message": f"发现候选 {winner['symbol']}，但名称与原查询不一致，请确认是否就是它。",
                "candidates": [_candidate_view(winner)],
                "source": "discovery",
            }

        if confidence < _DISCOVERY_AUTO_REGISTER_CONFIDENCE:
            return {
                "status": "clarify",
                "query": raw,
                "message": f"发现候选 {winner['symbol']}，但置信度不足，请确认是否就是它。",
                "candidates": [_candidate_view(winner)],
                "source": "discovery",
            }

        auto_registered = False
        if auto_register:
            register_result = register_discovered_asset(winner)
            auto_registered = bool(register_result.get("registered"))
        return {
            "status": "success",
            "query": raw,
            "symbol": winner["symbol"],
            "market": winner.get("validated_market") or _market_from_catalog_market(winner.get("market")),
            "source": "discovery",
            "candidate": _candidate_view(winner),
            "auto_registered": auto_registered,
        }

    if len(validated) > 1:
        candidates_view = [_candidate_view(candidate) for candidate in validated[:3]]
        return {
            "status": "clarify",
            "query": raw,
            "message": f"发现多个可能标的，请明确其中一个：{_format_resolution_candidates(candidates_view)}",
            "candidates": candidates_view,
            "source": "discovery",
        }

    return {
        "status": "not_found",
        "query": raw,
        "message": f"未能为“{raw}”找到可验证的交易代码，请补充代码或交易所信息。",
        "source": "discovery",
    }


# ── 股票：AKShare ──


def _to_akshare_a_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if raw.startswith(("SH", "SZ", "BJ")) and len(raw) == 8:
        return raw.lower()
    if raw.endswith(".SH"):
        return f"sh{raw[:-3]}"
    if raw.endswith(".SZ"):
        return f"sz{raw[:-3]}"
    if raw.endswith(".BJ"):
        return f"bj{raw[:-3]}"
    if raw.isdigit() and len(raw) == 6:
        if raw.startswith(("6", "9")):
            return f"sh{raw}"
        if raw.startswith(("0", "3")):
            return f"sz{raw}"
        if raw.startswith(("4", "8")):
            return f"bj{raw}"
    return raw.lower()


def _to_akshare_hk_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if raw.endswith(".HK"):
        return raw[:-3].zfill(5)
    if raw.isdigit():
        return raw.zfill(5)
    return raw


def _to_akshare_us_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if raw.endswith(".US"):
        return raw[:-3]
    return raw


def _stock_resample_rule(interval: str) -> str:
    iv = str(interval or "1d").strip()
    if iv == "1w":
        return "W-FRI"
    if iv == "1M":
        return "ME"
    if iv == "1Q":
        return "QE"
    if iv == "1Y":
        return "YE"
    return ""


def _normalize_stock_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    rename_map = {
        "日期": "date",
        "date": "date",
        "Date": "date",
        "开盘": "open",
        "open": "open",
        "Open": "open",
        "最高": "high",
        "high": "high",
        "High": "high",
        "最低": "low",
        "low": "low",
        "Low": "low",
        "收盘": "close",
        "close": "close",
        "Close": "close",
        "成交量": "volume",
        "volume": "volume",
        "Volume": "volume",
    }
    frame = frame.rename(columns=rename_map)
    required = ["date", "open", "high", "low", "close"]
    if any(col not in frame.columns for col in required):
        raise RuntimeError(f"AKShare 股票数据缺少必要字段: {frame.columns.tolist()}")

    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).copy()
    for col in ["open", "high", "low", "close", "volume"]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "volume" not in frame.columns:
        frame["volume"] = 0.0
    frame = frame.dropna(subset=["open", "high", "low", "close"]).copy()
    frame = frame.sort_values("date")
    return frame[["date", "open", "high", "low", "close", "volume"]]


def _resample_stock_dataframe(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    rule = _stock_resample_rule(interval)
    if not rule:
        return df

    frame = df.copy().set_index("date")
    aggregated = frame.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })
    aggregated = aggregated.dropna(subset=["open", "high", "low", "close"]).reset_index()
    return aggregated


def _rows_from_stock_dataframe(df: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    recent = df.tail(limit).copy()
    rows: list[dict[str, Any]] = []
    for _, row in recent.iterrows():
        rows.append({
            "time": pd.Timestamp(row["date"]).to_pydatetime().replace(tzinfo=timezone.utc).isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0.0) or 0.0),
        })
    return rows


def _fetch_stock_akshare_kline(symbol: str, interval: str, limit: int = 200, market: str = "") -> dict[str, Any]:
    """使用 AKShare 获取股票 K 线（A 股 / 美股 / 港股）。"""
    market_name = str(market or _detect_market(symbol)).strip()
    iv = str(interval or "1d").strip()
    if iv not in {"1d", "1w", "1M", "1Q", "1Y"}:
        iv = "1d"

    try:
        if market_name == "a_share":
            df = ak.stock_zh_a_daily(symbol=_to_akshare_a_symbol(symbol), adjust="")
        elif market_name == "us_equity":
            df = ak.stock_us_daily(symbol=_to_akshare_us_symbol(symbol), adjust="")
        elif market_name == "hk_equity":
            df = ak.stock_hk_daily(symbol=_to_akshare_hk_symbol(symbol), adjust="")
        else:
            return {"error": f"AKShare 暂不支持的股票市场: {market_name}", "status": "error"}
    except Exception as e:
        return {"error": f"AKShare 拉取股票失败: {e}", "status": "error"}

    if df is None or df.empty:
        return {"error": f"AKShare 未返回 {symbol} 有效股票数据", "status": "error"}

    try:
        frame = _normalize_stock_dataframe(df)
        frame = _resample_stock_dataframe(frame, iv)
        rows = _rows_from_stock_dataframe(frame, limit=max(30, min(limit, 10000)))
    except Exception as e:
        return {"error": f"AKShare 股票数据整理失败: {e}", "status": "error"}

    if not rows:
        return {"error": f"AKShare 未返回 {symbol} 有效股票数据", "status": "error"}

    return {
        "symbol": symbol,
        "interval": interval,
        "market": market_name,
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


@tool
def resolve_market_symbol(text: str, interval: str = "1d", auto_register: bool = True) -> dict[str, Any]:
    """将用户输入的标的名称或代码解析为规范交易代码。

    优先查本地 market_config.json；未命中时尝试发现候选、验活并在高置信场景下自动注册。
    """
    return _resolve_market_symbol_internal(text, interval=interval, auto_register=auto_register)


# ── 主入口 ──


@tool
def fetch_market_data(symbol: str, interval: str = "1d") -> dict[str, Any]:
    """获取标的 K 线数据，支持 A 股、美股、港股、加密货币、黄金

    数据源:
    - A 股 / 美股 / 港股: AKShare
    - 加密货币: gate.io
    - 黄金（国内沪金连续）: AKShare (AU0)

    Args:
        symbol: 标的代码 (e.g. 600519, BTCUSDT, NVDA, Au9999)
        interval: 时间周期 (1m, 5m, 15m, 1h, 4h, 1d, 1w, 1M)

    Returns:
        包含 K 线数据的字典，含 symbol, interval, market, data, count, status 字段
    """
    resolution = _resolve_market_symbol_internal(symbol, interval=interval, auto_register=True)
    if resolution.get("status") != "success":
        message = str(resolution.get("message") or "标的解析失败").strip() or "标的解析失败"
        out = {
            "error": message,
            "status": "error",
            "requested_symbol": symbol,
            "resolution": resolution,
        }
        if isinstance(resolution.get("candidates"), list):
            out["candidates"] = resolution.get("candidates")
        return out

    resolved_symbol = str(resolution.get("symbol") or symbol).strip() or str(symbol).strip()
    market = str(resolution.get("market") or _detect_market(resolved_symbol)).strip() or _detect_market(resolved_symbol)
    logger.info(
        "fetch_market_data: symbol=%s resolved_symbol=%s market=%s interval=%s source=%s",
        symbol,
        resolved_symbol,
        market,
        interval,
        resolution.get("source"),
    )

    if market in ("a_share", "us_equity", "hk_equity"):
        payload = _fetch_stock_akshare_kline(resolved_symbol, interval, market=market)
    elif market == "crypto":
        payload = _fetch_crypto_kline(resolved_symbol, interval)
    elif market == "gold":
        payload = _fetch_au0_akshare_kline(interval=interval)
    else:
        payload = {"error": f"未知或暂不支持的市场类型: {market}", "status": "error"}

    payload["requested_symbol"] = symbol
    payload["resolution"] = resolution
    if market == "gold" and payload.get("status") == "success":
        payload["symbol"] = resolved_symbol
    return payload
