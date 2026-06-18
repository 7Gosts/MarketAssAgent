from __future__ import annotations

from tools.registry import get_all_tools
from tools.response_guidance import get_response_guidance


def test_response_guidance_trade_plan_contains_core_fields():
    text = get_response_guidance.invoke({"guidance_type": "trade_plan"})
    assert "入场" in text
    assert "止损" in text
    assert "止盈" in text
    assert "仓位" in text
    assert "失效" in text


def test_response_guidance_tool_registered():
    names = {getattr(tool, "name", "") for tool in get_all_tools()}
    assert "get_response_guidance" in names
