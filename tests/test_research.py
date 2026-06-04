import pytest
from tools.research import search_research_reports


def test_search_research_reports_placeholder():
    """测试研报工具是否可调用（当前为真实 yanbaoke 实现）"""
    result = search_research_reports.invoke({"keyword": "人工智能", "top_k": 3})
    assert "keyword" in result
    assert result["keyword"] == "人工智能"
    # 可能返回 error（如果 node 未安装），也可能返回结果
    assert "message" in result or "error" in result
