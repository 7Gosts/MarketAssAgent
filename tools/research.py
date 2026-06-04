from langchain_core.tools import tool
from typing import Dict, Any


@tool
def search_research_reports(keyword: str, top_k: int = 5) -> Dict[str, Any]:
    """搜索研报或概念板块信息"""
    return {
        "keyword": keyword,
        "results": [
            {"title": f"{keyword} 行业研报", "source": "东方财富", "summary": "行业景气度回升"},
            {"title": f"{keyword} 概念解析", "source": "雪球", "summary": "资金持续流入"}
        ][:top_k],
        "message": f"已检索到与 {keyword} 相关的 {top_k} 条研报信息"
    }
