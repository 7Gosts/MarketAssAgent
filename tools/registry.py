"""工具注册中心 — 统一管理所有 LangChain 工具"""

from langchain_core.tools import BaseTool
from typing import List

# 安全导入，避免因部分工具未实现导致整体失败
try:
    from .technical_analysis import analyze_market, get_key_levels, evaluate_structure, analyze_multi
except Exception as e:
    print(f"[registry] technical_analysis import failed: {e}")
    analyze_market = get_key_levels = evaluate_structure = analyze_multi = None

try:
    from .research import search_research_reports
except Exception as e:
    print(f"[registry] research import failed: {e}")
    search_research_reports = None

try:
    from .sim_account import simulate_open_position, get_journal_status
except Exception as e:
    print(f"[registry] sim_account import failed: {e}")
    simulate_open_position = get_journal_status = None

try:
    from .market_data import fetch_market_data
except Exception as e:
    print(f"[registry] market_data import failed: {e}")
    fetch_market_data = None


def get_all_tools() -> List[BaseTool]:
    """统一注册所有可用工具（供 LangGraph 使用）"""
    tools = []
    for t in [
        analyze_market, get_key_levels, evaluate_structure, analyze_multi,
        search_research_reports,
        simulate_open_position, get_journal_status,
        fetch_market_data,
    ]:
        if t is not None:
            tools.append(t)
    return tools


# 方便单独导入技术分析工具
def get_technical_tools():
    """返回技术分析相关工具子集"""
    return [t for t in [
        analyze_market, get_key_levels, evaluate_structure, analyze_multi
    ] if t is not None]