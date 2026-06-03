"""MarketAssAgent — 测试共享 fixtures。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def sample_snapshot() -> dict[str, Any]:
    """标准的 BTC_USDT 分析 snapshot。"""
    return {
        "symbol": "BTC_USDT",
        "interval": "4h",
        "provider": "gateio",
        "trend": "偏多",
        "last_price": 67234.5,
        "fib_zone": "0.618~0.786",
        "sma_snapshot": {"sma8": 66800, "sma20": 66500, "sma60": 64800},
        "wyckoff_123": {
            "side": "long",
            "triggered": True,
            "entry": 67000,
            "stop": 65800,
            "tp1": 69500,
            "tp2": 72000,
        },
    }


@pytest.fixture
def sample_bundle() -> dict[str, Any]:
    """完整的分析 bundle（模拟 fetch_analysis_bundle 工具返回值）。"""
    return {
        "analysis_result": {
            "symbol": "BTC_USDT",
            "interval": "4h",
            "provider": "gateio",
            "trend": "偏多",
            "last_price": 67234.5,
            "price_vs_fib_zone": "0.618~0.786",
            "ma_snapshot": {"sma8": 66800, "sma20": 66500, "sma60": 64800},
            "wyckoff_123_v1": {
                "preferred_side": "long",
                "selected_setup": {
                    "triggered": True,
                    "entry": 67000,
                    "stop": 65800,
                    "tp1": 69500,
                    "tp2": 72000,
                },
            },
            "fixed_template": {
                "综合倾向": "偏多",
                "关键位(Fib)": "0.618=65800, 0.786=68000",
                "触发条件": "突破 67000",
                "失效条件": "跌破 65800",
                "风险点": ["65800 支撑失效", "量能不足"],
                "下次复核时间": "2024-01-15 20:00",
            },
        },
        "risk_flags": ["fib_zone_near_boundary"],
        "evidence_sources": [
            {"source_type": "kline", "source_path": "/output/btc_4h_kline.json"}
        ],
        "meta": {
            "ai_overview_path": "/output/btc_4h_overview.json",
            "full_report_path": "/output/btc_4h_report.md",
            "session_dir": "/output/sessions/btc_4h",
        },
    }


@pytest.fixture
def mock_session_mgr():
    """模拟 MarketSessionManager。"""
    from memory.session_manager import SessionState

    class MockSessionMgr:
        def __init__(self):
            self.saved_snapshots: dict[str, dict] = {}
            self.saved_replies: dict[str, list[str]] = {}
            self.saved_user_messages: dict[str, list[str]] = {}
            self._state = SessionState(open_id="test_session")

        def load_session(self, session_id: str) -> SessionState:
            return self._state

        def save_snapshot(self, session_id: str, snapshot: dict, output_refs: dict | None = None):
            self.saved_snapshots[session_id] = snapshot
            self._state.last_facts_bundle = snapshot
            sym = snapshot.get("symbol")
            if sym:
                self._state.last_symbols = [sym] if isinstance(sym, str) else list(sym)
            if snapshot.get("interval"):
                self._state.last_interval = snapshot["interval"]
            if snapshot.get("provider"):
                self._state.last_provider = snapshot["provider"]
            if output_refs:
                self._state.last_output_refs = output_refs

        def save_reply(self, session_id: str, reply: str):
            self.saved_replies.setdefault(session_id, []).append(reply)

        def save_user_message(self, session_id: str, text: str):
            self.saved_user_messages.setdefault(session_id, []).append(text)

    return MockSessionMgr()