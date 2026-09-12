from __future__ import annotations

import json

from application.presenters import WebPresenter
from application.services.envelope_builder import build_conversation_envelope


def test_chat_result_builds_markdown_text_envelope():
    envelope = build_conversation_envelope(
        result={"reply": "你好，我可以帮你看行情。"},
        reply_text="你好，我可以帮你看行情。",
        session_id="test_chat",
    )

    assert envelope.version == "1.2"
    assert envelope.raw == {}
    assert envelope.reply_text == "你好，我可以帮你看行情。"
    assert envelope.meta["session_id"] == "test_chat"
    assert "pending_turn_summary" not in envelope.model_dump(mode="json")


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

    assert envelope.meta["symbols"] == ["AU9999", "000625"]
def test_web_presenter_returns_envelope_root_only():
    envelope = build_conversation_envelope(
        result={"reply": "ok"},
        reply_text="ok",
        session_id="test_web",
    )

    payload = WebPresenter().render(envelope=envelope)

    assert set(payload.keys()) == {"envelope"}
    assert payload["envelope"]["reply_text"] == "ok"
    assert "blocks" not in payload["envelope"]
    assert "delivery_hint" not in payload["envelope"]
