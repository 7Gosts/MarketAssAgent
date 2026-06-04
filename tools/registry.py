from langchain_core.tools import BaseTool
from typing import List

# 安全导入，避免因部分工具未实现导致整体失败
try:
    from .technical_analysis import analyze_market, get_key_levels
except Exception:
    analyze_market = get_key_levels = None

try:
    from .research import search_research_reports
except Exception:
    search_research_reports = None

try:
    from .sim_account import simulate_open_position, get_journal_status
except Exception:
    simulate_open_position = get_journal_status = None

try:
    from .market_data import fetch_market_data
except Exception:
    fetch_market_data = None


def get_all_tools() -> List[BaseTool]:
    """统一注册所有可用工具（供 LangGraph 使用）"""
    tools = []
    for t in [analyze_market, get_key_levels, search_research_reports,
              simulate_open_position, get_journal_status, fetch_market_data]:
        if t is not None:
            tools.append(t)
    return tools


# 方便单独导入技术分析工具
def get_technical_tools():
    return [t for t in [analyze_market, get_key_levels] if t is not None]
