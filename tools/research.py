from langchain_core.tools import tool
from typing import Dict, Any
from .yanbaoke.yanbaoke_client import search_reports_json


@tool
def search_research_reports(keyword: str, top_k: int = 5) -> Dict[str, Any]:
    """搜索研报或概念板块信息（真实调用 yanbaoke）"""
    try:
        result = search_reports_json(keyword, n=top_k, search_type="title")
        items = result.get("items", [])[:top_k]
        return {
            "keyword": keyword,
            "total": result.get("total", 0),
            "results": items,
            "message": f"已检索到与 {keyword} 相关的 {len(items)} 条研报信息"
        }
    except Exception as e:
        return {
            "keyword": keyword,
            "error": str(e),
            "message": f"研报搜索失败: {e}"
        }
