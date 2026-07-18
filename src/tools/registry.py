"""工具注册中心 — 统一管理所有 LangChain 工具"""

from langchain_core.tools import BaseTool
from typing import List
from utils.logging_utils import get_logger


logger = get_logger(__name__)

# 安全导入，避免因部分工具未实现导致整体失败
try:
    from domain.market.analysis_service import (
        analyze_fibonacci,
        analyze_market,
        evaluate_structure,
        get_key_levels,
    )
except Exception as e:
    logger.warning("[registry] technical_analysis import failed: %s", e)
    analyze_fibonacci = analyze_market = get_key_levels = evaluate_structure = None

try:
    from .research import search_research_reports
except Exception as e:
    logger.warning("[registry] research import failed: %s", e)
    search_research_reports = None

try:
    from .sim_account import (
        get_journal_status,
        prepare_simulated_order,
        reconcile_paper_orders,
        simulate_open_position,
    )
except Exception as e:
    logger.warning("[registry] sim_account import failed: %s", e)
    simulate_open_position = get_journal_status = reconcile_paper_orders = prepare_simulated_order = None

try:
    from .market_data import fetch_market_data
except Exception as e:
    logger.warning("[registry] market_data import failed: %s", e)
    fetch_market_data = None

try:
    from domain.profile.user_profile import get_user_profile, update_user_profile
except Exception as e:
    logger.warning("[registry] user_profile import failed: %s", e)
    get_user_profile = update_user_profile = None

try:
    from .response_guidance import get_response_guidance
except Exception as e:
    logger.warning("[registry] response_guidance import failed: %s", e)
    get_response_guidance = None

try:
    from .context_memory import (
        get_last_snapshot,
        get_previous_analysis_snapshot,
        get_recent_tool_observations,
        search_conversation_summaries,
    )
except Exception as e:
    logger.warning("[registry] context_memory import failed: %s", e)
    get_last_snapshot = get_previous_analysis_snapshot = get_recent_tool_observations = search_conversation_summaries = None


def get_all_tools() -> List[BaseTool]:
    """统一注册所有可用工具（供 LangGraph 使用）"""
    tools = []
    for t in [
        analyze_market, get_key_levels, evaluate_structure, analyze_fibonacci,
        search_research_reports,
        prepare_simulated_order, simulate_open_position, reconcile_paper_orders, get_journal_status,
        fetch_market_data,
        get_user_profile, update_user_profile,
        get_response_guidance,
        get_last_snapshot, get_previous_analysis_snapshot, get_recent_tool_observations, search_conversation_summaries,
    ]:
        if t is not None:
            tools.append(t)
    return tools
