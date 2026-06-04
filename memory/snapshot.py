"""MarketAssAgent — 轻量 Snapshot 提取与持久化。

提供 extract + save/load，支持追问时恢复上次分析上下文。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def extract_snapshot(raw_bundle: dict[str, Any]) -> dict[str, Any]:
    """从完整分析结果中提取轻量结构化摘要。

    返回示例：
    {
        "symbol": "BTC_USDT", "interval": "4h", "provider": "gateio",
        "trend": "偏多", "last_price": 67234.5, "fib_zone": "0.618~0.786",
        "sma_snapshot": {"sma20": 66500, "sma60": 64800},
        "wyckoff_123": {"side": "long", "triggered": True, "entry": 67000, "stop": 65800},
        "fixed_template": {...},
    }
    """
    if not isinstance(raw_bundle, dict):
        return {}

    # 分析结果可能在 analysis_result 字段内，也可能直接在顶层
    analysis = raw_bundle.get("analysis_result") or raw_bundle
    if not isinstance(analysis, dict):
        return {}

    # 固定模板
    ft = analysis.get("fixed_template")
    if not isinstance(ft, dict):
        ft = None

    # MA snapshot
    ms = analysis.get("ma_snapshot") or {}
    sma_snapshot: dict[str, Any] = {}
    for key in ("sma8", "sma20", "sma60"):
        val = ms.get(key)
        if val is not None:
            sma_snapshot[key] = val

    # Wyckoff 123
    wy = analysis.get("wyckoff_123_v1") or {}
    sel = wy.get("selected_setup") or {}
    wyckoff_123: dict[str, Any] | None = None
    if sel:
        wyckoff_123 = {
            "side": wy.get("preferred_side"),
            "triggered": sel.get("triggered"),
            "entry": sel.get("entry"),
            "stop": sel.get("stop"),
            "tp1": sel.get("tp1"),
            "tp2": sel.get("tp2"),
        }

    snapshot: dict[str, Any] = {}

    # 基本信息
    for key in ("symbol", "interval", "provider", "trend", "last_price"):
        val = analysis.get(key)
        if val is not None:
            snapshot[key] = val

    # Fib zone
    fib_zone = analysis.get("price_vs_fib_zone") or analysis.get("fib_zone")
    if fib_zone:
        snapshot["fib_zone"] = fib_zone

    if sma_snapshot:
        snapshot["sma_snapshot"] = sma_snapshot
    if wyckoff_123:
        snapshot["wyckoff_123"] = wyckoff_123
    if ft:
        snapshot["fixed_template"] = ft

    return snapshot


def snapshot_to_context_str(snapshot: dict[str, Any]) -> str:
    """将 snapshot 转为人类可读的上下文字符串，用于注入 prompt。"""
    if not snapshot:
        return ""

    lines: list[str] = []
    if snapshot.get("symbol"):
        lines.append(f"标的：{snapshot['symbol']}")
    if snapshot.get("trend"):
        lines.append(f"趋势：{snapshot['trend']}")
    if snapshot.get("last_price"):
        lines.append(f"最新价：{snapshot['last_price']}")
    if snapshot.get("fib_zone"):
        lines.append(f"Fib 区间：{snapshot['fib_zone']}")
    if snapshot.get("sma_snapshot"):
        ms = snapshot["sma_snapshot"]
        parts = [f"{k}={v}" for k, v in ms.items()]
        lines.append(f"均线：{', '.join(parts)}")
    if snapshot.get("wyckoff_123"):
        wy = snapshot["wyckoff_123"]
        if wy.get("triggered"):
            lines.append(f"123 形态：{wy.get('side', '?')} 方向已触发，entry={wy.get('entry')} stop={wy.get('stop')}")
        else:
            lines.append(f"123 形态：{wy.get('side', '?')} 方向未触发")

    return "\n".join(lines)


# === 持久化（简单 JSON 文件，供 memory/session 复用） ===

_SNAPSHOT_DIR = Path("sessions/snapshots")


def _snapshot_path(session_id: str, symbol: str) -> Path:
    safe_symbol = symbol.replace("/", "_")
    return _SNAPSHOT_DIR / f"{session_id}_{safe_symbol}.json"


def save_snapshot(session_id: str, symbol: str, snapshot: dict[str, Any]) -> None:
    """将 AnalysisSnapshot 保存到磁盘（JSON）。"""
    _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = _snapshot_path(session_id, symbol)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def load_snapshot(session_id: str, symbol: str) -> dict[str, Any] | None:
    """从磁盘加载上次的 AnalysisSnapshot（若存在）。"""
    path = _snapshot_path(session_id, symbol)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None