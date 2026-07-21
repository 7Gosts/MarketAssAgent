from core.prompt import SYSTEM_PROMPT


def test_prompt_contract_uses_market_specific_default_intervals():
    assert "加密货币用 4h" in SYSTEM_PROMPT
    assert "黄金和其他非加密标的用 1d" in SYSTEM_PROMPT
    assert "只有用户明确说 1h" in SYSTEM_PROMPT


def test_prompt_contract_requires_previous_snapshot_after_single_market_analysis():
    assert "每次调用 analyze_market 完成单标的行情分析后" in SYSTEM_PROMPT
    assert "必须调用 get_previous_analysis_snapshot" in SYSTEM_PROMPT
    assert "暂无同标的同周期历史快照可比" in SYSTEM_PROMPT
