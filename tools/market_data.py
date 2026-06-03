"""MarketAssAgent — 行情数据工具。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from tools.legacy_bridge import (
    compare_assets,
    resolve_alias,
    resolve_provider,
    run_analysis,
    run_quote,
)


def make_market_data_tools(*, repo_root: Path) -> list:
    """创建行情数据相关工具，通过闭包注入 repo_root。"""

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
        """运行完整 K 线分析管线，返回结构化分析快照。

        当用户问到趋势、技术结构、Fib 位、Wyckoff 123 形态、入场/止损/止盈、
        或需要全面行情分析时使用此工具。

        Example:
            fetch_analysis_bundle(symbol="BTC_USDT", provider="gateio", interval="4h")
            fetch_analysis_bundle(symbol="002230.SZ", provider="tickflow", interval="1d")
        """
        resolved = resolve_provider(repo_root=repo_root, symbol=symbol, provider=provider)
        return run_analysis(
            repo_root=repo_root,
            symbol=symbol,
            provider=resolved,
            interval=interval,
            limit=limit,
            out_dir=out_dir,
            question=question,
            rag_top_k=rag_top_k,
            analysis_style=analysis_style,
        )

    @tool
    def resolve_asset_alias(text: str) -> list[str]:
        """将用户口语化的资产名称解析为标准 symbol 列表。

        当用户提到 "苹果"、"黄金"、"B站" 等非标准名称时，
        调用此工具获取标准 symbol（如 AAPL, AU9999, BILI）。

        Example:
            resolve_asset_alias("苹果") -> ["AAPL"]
            resolve_asset_alias("黄金") -> ["AU9999"]
            resolve_asset_alias("BTC") -> ["BTC_USDT"]
        """
        return resolve_alias(text)

    @tool
    def fetch_quote(
        symbol: str,
        provider: str = "gateio",
        interval: str = "4h",
    ) -> dict[str, Any]:
        """获取最新报价和简略趋势信息（不运行完整分析管线）。

        当用户只需要快速价格查看，不需要 Fib/123/完整结构时使用。

        Example:
            fetch_quote(symbol="BTC_USDT", provider="gateio")
        """
        resolved = resolve_provider(repo_root=repo_root, symbol=symbol, provider=provider)
        return run_quote(
            repo_root=repo_root,
            symbol=symbol,
            provider=resolved,
            interval=interval,
        )

    @tool
    def compare_assets_tool(
        symbols: list[str],
        interval: str = "1d",
        provider: str = "gateio",
    ) -> dict[str, Any]:
        """对比多个标的的价格和趋势信息。

        Example:
            compare_assets_tool(symbols=["BTC_USDT", "ETH_USDT"], interval="4h")
        """
        return compare_assets(
            repo_root=repo_root,
            symbols=symbols,
            interval=interval,
            provider=provider,
        )

    @tool
    def read_output_file(path: str) -> str:
        """读取本地产物文件内容。追问时如需读取完整报告或 overview JSON 可用此工具。

        Example:
            read_output_file(path="/path/to/ai_overview.json")
        """
        p = Path(path)
        if not p.exists() or not p.is_file():
            return f"Error: File {path} not found."
        try:
            return p.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file {path}: {e}"

    return [fetch_analysis_bundle, resolve_asset_alias, fetch_quote, compare_assets_tool, read_output_file]