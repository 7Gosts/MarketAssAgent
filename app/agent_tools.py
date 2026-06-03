from __future__ import annotations

from pathlib import Path
from typing import Any

from app.executors.market_snapshot import run_market_snapshot
from langchain_core.tools import tool


from app.market_data.resolver import resolve_provider_for_symbol as _resolve_provider


def resolve_provider_for_symbol(*, repo_root: Path, symbol: str, provider: str) -> str:
    """按 market_config 将 symbol 映射到正确 provider，避免 LLM 工具默认 gateio 误拉贵金属/股票。"""
    return _resolve_provider(
        repo_root=repo_root,
        symbol=symbol,
        provider_hint=provider,
    )


def _build_analysis_bundle(
    *,
    repo_root: Path,
    symbol: str,
    provider: str,
    interval: str,
    limit: int,
    out_dir: str | None,
    question: str | None,
    rag_top_k: int,
    analysis_style: str,
) -> dict[str, Any]:
    resolved_provider = resolve_provider_for_symbol(
        repo_root=repo_root,
        symbol=symbol,
        provider=provider,
    )
    return run_market_snapshot(
        repo_root=repo_root,
        symbol=symbol,
        provider=resolved_provider,
        interval=interval,
        limit=limit,
        out_dir=out_dir,
        question=question,
        rag_top_k=rag_top_k,
        analysis_style=analysis_style,
        with_research=False,
        research_keyword=None,
    )


def make_tools(*, repo_root: Path) -> list[Any]:
    @tool
    def fetch_analysis_bundle(
        symbol: str,
        provider: str = "gateio",
        interval: str = "1d",
        limit: int = 180,
        out_dir: str | None = None,
        question: str | None = None,
        rag_top_k: int = 5,
        analysis_style: str = "auto",
    ) -> dict[str, Any]:
        """拉取行情并生成结构化分析快照（含固定模板、风险标记与证据源）。"""
        return _build_analysis_bundle(
            repo_root=repo_root,
            symbol=symbol,
            provider=provider,
            interval=interval,
            limit=limit,
            out_dir=out_dir,
            question=question,
            rag_top_k=rag_top_k,
            analysis_style=analysis_style,
        )

    @tool
    def view_sim_account_state(
        scope: str = "overview",
        account_id: str | None = None,
        symbol: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """查看模拟账户状态。

        scope 可选值：
        - overview: 余额 + 持仓 + 活动想法 + 对账统计
        - positions: 当前未平仓持仓
        - active_ideas: watch/pending/filled 的活动交易想法
        - orders: 最近委托
        - fills: 最近成交
        - health: order/fill 对账统计
        """
        from app.capabilities.sim_account_capability import view_sim_account_state as _view

        result = _view(
            scope=scope,
            account_id=account_id,
            symbol=symbol,
            limit=limit,
        )
        return result.to_dict()

    @tool
    def resolve_asset_alias(text: str) -> list[str]:
        """将用户口语化的资产名称（如“黄金”、“小鹏汽车”、“B站”）解析为标准 symbol 列表。
        如果返回空列表，说明系统当前未配置该资产。
        """
        from app.feishu_asset_catalog import AssetCatalog
        catalog = AssetCatalog()
        return catalog.resolve_symbols_from_text(text)

    @tool
    def view_research_digest(symbol: str, limit: int = 5) -> dict[str, Any]:
        """检索指定 symbol 的机构研报、板块叙事、催化/风险等信息。"""
        from app.capabilities.research_facts import build_research_facts_bundle
        from config.market_config import get_market_config
        # Extract research keywords
        config = get_market_config()
        keyword = symbol # fallback
        for asset in config.get("assets", []):
            if asset.get("symbol") == symbol:
                keyword = asset.get("research_keyword") or asset.get("name") or symbol
                break

        return build_research_facts_bundle(
            symbol=symbol,
            research_keyword=keyword,
            limit=limit,
            use_rag=True
        )

    @tool
    def load_local_output_refs(path: str) -> str:
        """读取指定的本地产物文件内容。如果多轮对话中存在 output_refs（如 ai_overview_path, full_report_path 的具体路径），可使用本工具读取其内容。"""
        p = Path(path)
        if not p.exists() or not p.is_file():
            return f"Error: File {path} not found."
        try:
            return p.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file {path}: {e}"

    return [
        fetch_analysis_bundle,
        view_sim_account_state,
        resolve_asset_alias,
        view_research_digest,
        load_local_output_refs
    ]
