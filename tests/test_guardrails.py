"""测试 core/guardrails.py + core/supervisor.py — 禁止口径、条件语气、免责声明。"""

from __future__ import annotations


def test_forbidden_claims_is_tuple_of_strings():
    """FORBIDDEN_CLAIMS 应为字符串 tuple。"""
    from core.guardrails import FORBIDDEN_CLAIMS
    assert isinstance(FORBIDDEN_CLAIMS, tuple)
    assert len(FORBIDDEN_CLAIMS) > 0
    for item in FORBIDDEN_CLAIMS:
        assert isinstance(item, str)


def test_supervisor_catches_forbidden_claims():
    """supervisor_node 应检测并替换禁止口径。"""
    from core.nodes import supervisor_node
    from langchain_core.messages import AIMessage

    state = {
        "messages": [AIMessage(content="主力资金净流入明显，应该买入")],
    }
    result = supervisor_node(state)
    reply = result["final_reply"]
    # 应被替换为 "[已移除不当表述:...]" 标记
    assert "[已移除不当表述" in reply
    # "应该买入" 应被条件语气替换
    assert "可考虑逢低关注" in reply


def test_supervisor_appends_disclaimer():
    """supervisor_node 应追加免责声明。"""
    from core.nodes import supervisor_node
    from langchain_core.messages import AIMessage

    state = {
        "messages": [AIMessage(content="市场趋势偏多")],
    }
    result = supervisor_node(state)
    reply = result["final_reply"]
    assert "仅供技术分析与程序化演示" in reply
    assert result["has_disclaimer"] is True


def test_supervisor_no_duplicate_disclaimer():
    """supervisor_node 不应重复追加免责声明。"""
    from core.nodes import supervisor_node
    from langchain_core.messages import AIMessage

    disclaimer = "仅供技术分析与程序化演示，不构成投资建议。"
    state = {
        "messages": [AIMessage(content=f"市场趋势偏多\n{disclaimer}")],
    }
    result = supervisor_node(state)
    reply = result["final_reply"]
    # 免责声明只出现一次
    assert reply.count(disclaimer) == 1


def test_supervisor_conditional_language():
    """supervisor_node 应将绝对表述替换为条件语气。"""
    from core.nodes import supervisor_node
    from langchain_core.messages import AIMessage

    state = {
        "messages": [AIMessage(content="应该买入，建议开多")],
    }
    result = supervisor_node(state)
    reply = result["final_reply"]
    assert "应该买入" not in reply
    assert "可考虑逢低关注" in reply
    assert "建议开多" not in reply
    assert "若结构触发可考虑小仓试探" in reply


def test_supervisor_sets_final_reply_and_disclaimer():
    """supervisor_node 应设置 final_reply 和 has_disclaimer。"""
    from core.nodes import supervisor_node
    from langchain_core.messages import AIMessage

    state = {
        "messages": [AIMessage(content="测试回复")],
    }
    result = supervisor_node(state)
    assert "final_reply" in result
    assert "has_disclaimer" in result
    assert result["has_disclaimer"] is True


def test_supervisor_handles_list_content():
    """supervisor_node 应处理多部分消息内容。"""
    from core.nodes import supervisor_node
    from langchain_core.messages import AIMessage

    state = {
        "messages": [AIMessage(content=[{"text": "分析结果"}])],
    }
    result = supervisor_node(state)
    assert "分析结果" in result["final_reply"]