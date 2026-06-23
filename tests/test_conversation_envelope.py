from __future__ import annotations

import json

from interfaces.presenters.web_presenter import WebPresenter
from application.services.envelope_builder import build_conversation_envelope


def test_chat_result_builds_markdown_text_envelope():
    envelope = build_conversation_envelope(
        result={"reply": "你好，我可以帮你看行情。"},
        reply_text="你好，我可以帮你看行情。",
        session_id="test_chat",
    )

    assert envelope.version == "1.2"
    assert envelope.raw == {}
    assert envelope.delivery_hint.mode == "text"
    assert envelope.blocks == []
    assert envelope.reply_text == "你好，我可以帮你看行情。"
    assert envelope.meta["has_rich_content"] is False


def test_market_result_keeps_text_mode_and_symbols_meta():
    result = {
        "recommendation": {"text": "ETH 当前偏震荡。", "disclaimer": "风险自担。"},
        "analysis_result": {
            "symbol": "ETHUSDT",
            "interval": "1h",
            "current_price": 1666.12,
            "trend": "震荡",
            "confidence": 60,
        },
    }
    envelope = build_conversation_envelope(
        result=result,
        reply_text="ETH 当前偏震荡。",
        session_id="test_analysis",
    )

    assert envelope.delivery_hint.mode == "text"
    assert envelope.blocks == []
    assert envelope.meta["symbols"] == ["ETHUSDT"]
    assert envelope.reply_text == "ETH 当前偏震荡。"


def test_multi_market_payload_sets_symbols_meta():
    tool_payload = {
        "status": "success",
        "symbols": ["AU9999", "000625"],
        "interval": "1d",
        "analyses": {},
        "comparison": {
            "summary": [
                {"symbol": "AU9999", "trend": "偏空", "confidence": 70},
                {"symbol": "000625", "trend": "震荡", "confidence": 60},
            ]
        },
    }
    result = {
        "messages": [{"role": "tool", "content": json.dumps(tool_payload, ensure_ascii=False)}],
        "recommendation": {"text": "多标的分析完成。"},
    }

    envelope = build_conversation_envelope(
        result=result,
        reply_text="多标的分析完成。",
        session_id="test_multi",
    )

    assert envelope.delivery_hint.mode == "text"
    assert envelope.blocks == []
    assert envelope.meta["symbols"] == ["AU9999", "000625"]


def test_trade_plan_request_formats_markdown_reply():
    result = {
        "analysis_result": {
            "symbol": "BTCUSDT",
            "interval": "15m",
            "trend": "震荡",
            "confidence": 60,
        },
        "recommendation": {"text": "等待突破再做。"},
    }

    envelope = build_conversation_envelope(
        result=result,
        reply_text="【方向判断】先等突破。",
        session_id="test_trade_plan",
        user_text="给出一个合适的 btc 开单建议",
    )

    assert envelope.meta["request_style"] == "trade_plan"
    assert envelope.blocks == []
    assert envelope.reply_text.startswith("**交易计划建议**")
    assert "> 风险提示" in envelope.reply_text


def test_web_presenter_returns_envelope_root_only():
    envelope = build_conversation_envelope(
        result={"reply": "ok"},
        reply_text="ok",
        session_id="test_web",
    )

    payload = WebPresenter().render(envelope=envelope)

    assert set(payload.keys()) == {"envelope"}
    assert payload["envelope"]["reply_text"] == "ok"
