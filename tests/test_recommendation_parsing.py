import re
from core.agent import MarketReActAgent
from unittest.mock import MagicMock


def test_recommendation_parsing_long():
    """测试多头方向解析"""
    text = "若价格回踩 62000 支撑位，可考虑轻仓试多，止损设在 60500。"
    direction = "long" if re.search(r'(试多|做多|看涨|多头|long)', text, re.IGNORECASE) else "short"
    assert direction == "long"


def test_recommendation_parsing_short():
    """测试空头方向解析"""
    text = "若价格跌破 62000，建议做空，止损 63500。"
    direction = "long" if re.search(r'(试多|做多|看涨|多头|long)', text, re.IGNORECASE) else "short"
    assert direction == "short"


def test_recommendation_no_trading_keywords():
    """测试没有交易关键词的情况"""
    text = "BTC 目前处于震荡区间，建议观望。"
    has_trading = bool(re.search(r'(若价格|若突破|可考虑|建议)', text, re.IGNORECASE))
    assert has_trading is False
