"""MarketAssAgent — Legacy 过渡层（tools/ → app/ 的桥接）。

本模块是明确的**过渡层**，最终目标是将 app/ 中的功能迁移到 tools/ 原生实现后，
删除此文件中的对应调用。每个函数标注 TODO(legacy) 说明迁移路径。

依赖规则：tools/ 内的模块只能通过 legacy_bridge 间接调用 app/，不得直接 import app/。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_provider(*, repo_root: Path, symbol: str, provider: str) -> str:
    """按 market_config 将 symbol 映射到正确 provider。

    # TODO(legacy): 迁移 provider 解析逻辑到 tools/market_data 后删除此调用
    """
    from app.market_data.resolver import resolve_provider_for_symbol
    return resolve_provider_for_symbol(
        repo_root=repo_root, symbol=symbol, provider_hint=provider,
    )


def run_analysis(
    *,
    repo_root: Path,
    symbol: str,
    provider: str,
    interval: str,
    limit: int = 180,
    out_dir: str | None = None,
    question: str | None = None,
    rag_top_k: int = 5,
    analysis_style: str = "auto",
) -> dict[str, Any]:
    """运行完整 K 线分析管线，返回结构化分析快照。

    # TODO(legacy): 迁移分析执行逻辑到 tools/market_data 后删除此调用
    """
    from app.executors.market_snapshot import run_market_snapshot
    return run_market_snapshot(
        repo_root=repo_root,
        symbol=symbol,
        provider=provider,
        interval=interval,
        limit=limit,
        out_dir=out_dir,
        question=question,
        rag_top_k=rag_top_k,
        analysis_style=analysis_style,
        with_research=False,
        research_keyword=None,
    )


def run_quote(
    *,
    repo_root: Path,
    symbol: str,
    provider: str,
    interval: str,
) -> dict[str, Any]:
    """获取简略报价信息（不运行完整分析管线）。

    # TODO(legacy): 迁移报价逻辑到 tools/market_data 后删除此调用
    # 注：此处修复了原 tools/market_data.py 中错误的 import 路径
    """
    from app.capabilities.quote_facts import run_quote_facts_bundle
    return run_quote_facts_bundle(
        repo_root=repo_root,
        symbol=symbol,
        provider=provider,
        interval=interval,
    )


def resolve_alias(text: str) -> list[str]:
    """将用户口语化的资产名称解析为标准 symbol 列表。

    # TODO(legacy): 迁移资产解析逻辑到 tools/market_data 后删除此调用
    """
    from app.feishu_asset_catalog import AssetCatalog
    catalog = AssetCatalog()
    return catalog.resolve_symbols_from_text(text)


def compare_assets(
    *,
    repo_root: Path,
    symbols: list[str],
    interval: str,
    provider: str,
) -> dict[str, Any]:
    """对比多个标的的价格和趋势信息。

    # TODO(legacy): 迁移对比逻辑到 tools/market_data 后删除此调用
    """
    from app.market_data.snapshots import fetch_market_snapshots
    payloads = [{"symbol": s, "interval": interval, "provider": provider} for s in symbols]
    return fetch_market_snapshots(repo_root=repo_root, payloads=payloads)


def search_research(
    *,
    keyword: str,
    symbol: str | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """搜索机构研报、板块叙事、催化/风险等信息。

    # TODO(legacy): 迁移研报检索逻辑到 tools/research 后删除此调用
    """
    from app.capabilities.research_facts import build_research_facts_bundle
    return build_research_facts_bundle(
        symbol=symbol or keyword,
        research_keyword=keyword,
        limit=limit,
        use_rag=True,
    )


def view_sim_account(
    *,
    scope: str = "overview",
    account_id: str | None = None,
    symbol: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """查看纸交易模拟账户状态。

    # TODO(legacy): 迁移模拟账户逻辑到 tools/sim_account 后删除此调用
    """
    from app.capabilities.sim_account_capability import view_sim_account_state
    result = view_sim_account_state(
        scope=scope,
        account_id=account_id,
        symbol=symbol,
        limit=limit,
    )
    return result.to_dict()


# ── LLM 客户端配置桥接 ──────────────────────────────────────────


def load_agent_runtime_config():
    """加载 Agent 运行时配置。

    # TODO(legacy): 迁移运行时配置到 config/ 或 core/ 后删除此调用
    """
    from app.agent_runtime_config import load_agent_runtime_config
    return load_agent_runtime_config()


def normalize_research_keywords(keywords: list[str]) -> list[str]:
    """规范化研报搜索关键词。

    # TODO(legacy): 迁移关键词规范化到 tools/research 后删除此调用
    """
    from app.research_keyword import normalize_research_keywords
    return normalize_research_keywords(keywords)