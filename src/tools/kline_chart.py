"""Generate local K-line chart images."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from domain.market.indicators import _get_ma_config
from tools.market_data import fetch_market_data
from utils.runtime_paths import get_output_dir


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        open_price = _safe_float(row.get("open"))
        high = _safe_float(row.get("high"))
        low = _safe_float(row.get("low"))
        close = _safe_float(row.get("close"))
        volume = _safe_float(row.get("volume")) or 0.0
        if None in (open_price, high, low, close):
            continue
        if min(open_price, high, low, close) <= 0:
            continue
        cleaned.append({
            "time": str(row.get("time") or row.get("date") or ""),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
    return cleaned[-limit:]


def _moving_average(values: list[float], period: int) -> list[float | None]:
    if period <= 0:
        return [None for _ in values]
    out: list[float | None] = []
    window_sum = 0.0
    for idx, value in enumerate(values):
        window_sum += value
        if idx >= period:
            window_sum -= values[idx - period]
        out.append(window_sum / period if idx + 1 >= period else None)
    return out


def _time_label(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:16]
    if dt.hour or dt.minute:
        return dt.strftime("%m-%d %H:%M")
    return dt.strftime("%m-%d")


def _safe_filename(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.@-]+", "_", str(value or "").strip())
    return text.strip("._") or "chart"


def generate_kline_chart(symbol: str, interval: str = "1d", bars: int = 120) -> dict[str, Any]:
    """Generate a local PNG K-line chart with configured moving averages."""
    clean_symbol = str(symbol or "").strip()
    clean_interval = str(interval or "1d").strip() or "1d"
    if not clean_symbol:
        return {"status": "error", "message": "请提供 symbol"}

    try:
        bar_count = int(bars)
    except (TypeError, ValueError):
        bar_count = 120
    bar_count = max(30, min(bar_count, 240))

    raw = fetch_market_data(symbol=clean_symbol, interval=clean_interval)
    if raw.get("status") == "error" or raw.get("error"):
        return {
            "status": "error",
            "symbol": raw.get("symbol") or clean_symbol,
            "interval": clean_interval,
            "message": raw.get("error") or raw.get("message") or "行情数据获取失败",
        }

    rows = _clean_rows(raw.get("data") if isinstance(raw.get("data"), list) else [], bar_count)
    if len(rows) < 10:
        return {
            "status": "error",
            "symbol": raw.get("symbol") or clean_symbol,
            "interval": clean_interval,
            "message": "有效 K 线数量不足，无法生成图表",
        }

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except Exception as exc:
        return {
            "status": "error",
            "symbol": raw.get("symbol") or clean_symbol,
            "interval": clean_interval,
            "message": f"matplotlib 不可用: {exc}",
        }

    resolved_symbol = str(raw.get("symbol") or clean_symbol).strip() or clean_symbol
    market = str(raw.get("market") or "").strip()
    ma_config = _get_ma_config(resolved_symbol, market=market)
    closes = [float(row["close"]) for row in rows]
    x_values = list(range(len(rows)))

    fig, (price_ax, volume_ax) = plt.subplots(
        2,
        1,
        figsize=(12, 7),
        sharex=True,
        gridspec_kw={"height_ratios": [4, 1]},
    )
    fig.patch.set_facecolor("white")

    candle_width = 0.62
    up_color = "#d64b4b"
    down_color = "#2f9e6b"
    neutral_color = "#6b7280"
    for idx, row in enumerate(rows):
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        color = up_color if close >= open_price else down_color
        body_bottom = min(open_price, close)
        body_height = max(abs(close - open_price), max(high - low, high * 0.0001) * 0.012)
        price_ax.vlines(idx, low, high, color=color, linewidth=1.0, alpha=0.95)
        price_ax.add_patch(
            Rectangle(
                (idx - candle_width / 2, body_bottom),
                candle_width,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.8,
                alpha=0.9,
            )
        )
        volume_ax.bar(idx, float(row["volume"]), color=color if close != open_price else neutral_color, width=candle_width, alpha=0.45)

    ma_colors = {"short": "#2563eb", "mid": "#f59e0b", "long": "#7c3aed"}
    for label in ("short", "mid", "long"):
        period = int(ma_config.get(label) or 0)
        if period <= 0:
            continue
        ma_values = _moving_average(closes, period)
        plot_x = [x for x, value in zip(x_values, ma_values) if value is not None]
        plot_y = [value for value in ma_values if value is not None]
        if plot_x and plot_y:
            price_ax.plot(plot_x, plot_y, color=ma_colors[label], linewidth=1.25, label=f"MA{period}")

    latest = rows[-1]
    title = f"{resolved_symbol} {clean_interval} K-line  Close {float(latest['close']):.4g}"
    price_ax.set_title(title, loc="left", fontsize=12)
    price_ax.grid(True, axis="y", alpha=0.22)
    price_ax.legend(loc="upper left", ncols=3, fontsize=9, frameon=False)
    price_ax.margins(x=0.01)
    volume_ax.grid(True, axis="y", alpha=0.16)
    volume_ax.set_ylabel("Volume")

    tick_step = max(1, len(rows) // 8)
    tick_positions = list(range(0, len(rows), tick_step))
    if tick_positions[-1] != len(rows) - 1:
        tick_positions.append(len(rows) - 1)
    volume_ax.set_xticks(tick_positions)
    volume_ax.set_xticklabels([_time_label(rows[idx]["time"]) for idx in tick_positions], rotation=25, ha="right")

    output_dir = get_output_dir() / "charts"
    output_dir.mkdir(parents=True, exist_ok=True)
    latest_time = _safe_filename(str(latest.get("time") or "latest"))
    filename = (
        f"kline_{_safe_filename(resolved_symbol)}_"
        f"{_safe_filename(clean_interval)}_{len(rows)}_{latest_time}.png"
    )
    image_path = output_dir / filename
    fig.tight_layout()
    fig.savefig(image_path, dpi=150)
    plt.close(fig)

    return {
        "status": "success",
        "symbol": resolved_symbol,
        "interval": clean_interval,
        "market": market,
        "bars": len(rows),
        "ma_periods": dict(ma_config),
        "image_path": str(image_path),
        "message": "K 线图已生成",
    }
