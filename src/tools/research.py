from typing import Dict, Any
from .yanbaoke.yanbaoke_client import search_reports_json


def search_research_reports(keyword: str, top_k: int = 5) -> Dict[str, Any]:
    """搜索研报或概念板块信息（真实调用 yanbaoke）"""
    try:
        limit = max(1, min(int(top_k), 10))
        result = search_reports_json(keyword, n=limit, search_type="title")
        total = result.get("total", 0)
        if not isinstance(total, int) or isinstance(total, bool):
            total = 0
        items = result.get("items", [])[:limit] if total > 0 else []
        message = (
            f"已检索到与 {keyword} 相关的 {len(items)} 条研报信息"
            if items
            else f"未检索到与 {keyword} 相关的研报信息"
        )
        return {
            "keyword": keyword,
            "total": max(total, 0),
            "results": items,
            "message": message,
        }
    except Exception as e:
        return {
            "keyword": keyword,
            "error": str(e),
            "message": f"研报搜索失败: {e}"
        }
