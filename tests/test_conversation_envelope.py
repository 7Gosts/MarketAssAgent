from __future__ import annotations

import json

from presenters.feishu_presenter import FeishuPresenter
from presenters.web_presenter import WebPresenter
from services.envelope_builder import build_conversation_envelope
from formatters.feishu_card import format_market_analysis_envelope_as_card


def test_chat_result_builds_text_envelope():
    envelope = build_conversation_envelope(
        result={"reply": "你好，我可以帮你看行情。"},
        reply_text="你好，我可以帮你看行情。",
        session_id="test_chat",
    )

    assert envelope.version == "1.0"
    assert envelope.raw == {}
    assert envelope.delivery_hint.mode == "text"
    assert [block.type for block in envelope.blocks] == ["text_fallback", "risk_warning"]
    assert envelope.meta["has_rich_content"] is False

    delivery = FeishuPresenter().render(envelope)
    assert delivery.kind == "text"
    assert delivery.text == "你好，我可以帮你看行情。"


def test_single_market_analysis_builds_rich_envelope():
    result = {
        "recommendation": {"text": "ETH 当前偏震荡。", "disclaimer": "风险自担。"},
        "analysis_result": {
            "symbol": "ETHUSDT",
            "interval": "1h",
            "current_price": 1666.12,
            "trend": "震荡",
            "confidence": 60,
            "key_levels": {"support": [1650], "resistance": [1674]},
            "structure": "均线震荡排列",
        },
    }
    envelope = build_conversation_envelope(
        result=result,
        reply_text="ETH 当前偏震荡。",
        session_id="test_analysis",
    )

    assert envelope.delivery_hint.mode == "rich"
    assert envelope.delivery_hint.card_style == "assistant_response"
    assert envelope.blocks[0].type == "market_analysis"
    assert envelope.blocks[0].data["is_multi"] is False
    assert envelope.blocks[0].data["symbol"] == "ETHUSDT"
    assert envelope.blocks[-1].type == "risk_warning"

    delivery = FeishuPresenter().render(envelope)
    assert delivery.kind == "card"
    assert delivery.card


def test_multi_market_tool_message_uses_market_analysis_block():
    tool_payload = {
        "status": "success",
        "symbols": ["AU9999", "000625"],
        "interval": "1d",
        "analyses": {},
        "comparison": {
            "summary": [
                {"symbol": "AU9999", "trend": "偏空", "confidence": 70},
                {"symbol": "000625", "trend": "震荡", "confidence": 60},
            ],
            "strongest": {"symbol": "AU9999", "trend": "偏空", "confidence": 70},
            "weakest": {"symbol": "000625", "trend": "震荡", "confidence": 60},
            "trend_distribution": {"偏多": 0, "偏空": 1, "震荡": 1},
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

    assert envelope.blocks[0].type == "market_analysis"
    assert envelope.blocks[0].data["is_multi"] is True
    assert envelope.blocks[0].data["symbols"] == ["AU9999", "000625"]
    assert envelope.delivery_hint.mode == "rich"


def test_same_symbol_multi_interval_stays_single_market_block():
    result = {
        "messages": [
            {
                "role": "tool",
                "content": json.dumps(
                    {
                        "status": "success",
                        "analysis": {
                            "symbol": "ETHUSDT",
                            "interval": "1h",
                            "current_price": 1722.76,
                            "trend": "偏多",
                            "confidence": 100,
                        },
                    },
                    ensure_ascii=False,
                ),
            },
            {
                "role": "tool",
                "content": json.dumps(
                    {
                        "status": "success",
                        "analysis": {
                            "symbol": "ETHUSDT",
                            "interval": "15m",
                            "current_price": 1722.76,
                            "trend": "偏多",
                            "confidence": 100,
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "recommendation": {"text": "ETH 短线偏多。"},
    }

    envelope = build_conversation_envelope(
        result=result,
        reply_text="ETH 短线偏多。",
        session_id="test_same_symbol",
    )

    assert envelope.blocks[0].type == "market_analysis"
    assert envelope.blocks[0].data["is_multi"] is False
    assert envelope.blocks[0].data["symbol"] == "ETHUSDT"
    assert envelope.blocks[0].data["interval"] == "15m"


def test_web_presenter_returns_envelope_root_only():
    envelope = build_conversation_envelope(
        result={"reply": "ok"},
        reply_text="ok",
        session_id="test_web",
    )

    payload = WebPresenter().render(envelope=envelope)

    assert set(payload.keys()) == {"envelope"}
    assert payload["envelope"]["reply_text"] == "ok"


def test_feishu_card_normalizes_markdown_report_sections():
    result = {
        "analysis_result": {
            "symbol": "ETHUSDT",
            "interval": "1h",
            "trend": "偏多",
            "confidence": 80,
        },
        "recommendation": {"text": "仅测试。"},
    }
    reply = """好的，以下是针对 ETHUSDT 短线行情的专业分析：

---

### 【行情结论】
ETHUSDT 当前短线偏多。

### 【关键点】
- **支撑位**：$1,710
- **阻力位**：$1,732
"""
    envelope = build_conversation_envelope(
        result=result,
        reply_text=reply,
        session_id="test_card_markdown",
    )

    card, error = format_market_analysis_envelope_as_card(envelope).build_safe()

    assert error is None
    rendered = "\n".join(
        item.get("text", {}).get("content", "")
        for item in card["elements"]
        if item.get("tag") == "div"
    )
    assert "好的，以下是" not in rendered
    assert "---" not in rendered
    assert "###" not in rendered
    assert "**行情结论**" in rendered
    assert "$" not in rendered


def test_trade_plan_request_marks_envelope_and_card_title():
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
    assert envelope.blocks[0].title == "BTCUSDT 15m 开单计划"
    assert envelope.blocks[0].data["request_style"] == "trade_plan"
