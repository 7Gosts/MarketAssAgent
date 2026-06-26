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


def test_response_guidance_market_view_has_output_contract():
    text = get_response_guidance.invoke({"guidance_type": "market_view"})
    assert "结论" in text
    assert "关键位" in text
    assert "斐波那契" in text
    assert "复核" in text


def test_response_guidance_source_explain_has_evidence_boundary():
    text = get_response_guidance.invoke({"guidance_type": "source_explain"})
    assert "来源" in text
    assert "不要编造依据" in text


def test_response_guidance_research_view_enforces_boundary():
    text = get_response_guidance.invoke({"guidance_type": "research_view"})
    assert "叙事" in text
    assert "entry/stop/tp" in text


def test_response_guidance_tool_registered():
    names = {getattr(tool, "name", "") for tool in get_all_tools()}
    assert "get_response_guidance" in names
