from core.supervisor import supervisor_node
from core.state import AgentState


def test_supervisor_generates_recommendation():
    """测试 supervisor 是否正确生成带免责声明的 recommendation"""
    state: AgentState = {
        "messages": [{"role": "ai", "content": "BTC 目前呈现多头结构。"}],
        "session_id": "test",
        "current_symbol": "BTC_USDT",
        "current_interval": "4h",
        "last_snapshot": None,
        "analysis_result": None,
        "risk_assessment": None,
        "recommendation": None,
        "intent": None,
        "next": None,
        "metadata": {},
        "error": None,
    }

    result = supervisor_node(state)
    assert "recommendation" in result
    assert "disclaimer" in result["recommendation"]
    assert "仅供技术分析" in result["recommendation"]["disclaimer"]
