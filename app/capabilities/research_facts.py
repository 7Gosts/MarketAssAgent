"""研报 / research 路径：RAG 命中或 ``run_research_summary`` + merge_facts_bundle。"""
from __future__ import annotations

from typing import Any

from app.executors.facts_bundle import merge_facts_bundle
from app.executors.research_summary import run_research_summary


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        key = str(it.get("title") or it.get("source_path") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _research_keywords_from_payload(payload: dict[str, Any], user_question: str) -> list[str]:
    raw_list = payload.get("research_keywords")
    keywords: list[str] = []
    if isinstance(raw_list, list):
        keywords = [str(k).strip() for k in raw_list if str(k).strip()]
    primary = str(payload.get("research_keyword") or payload.get("symbol") or "").strip()
    if primary and primary not in keywords:
        keywords = [primary, *keywords]
    elif not keywords and primary:
        keywords = [primary]
    if not keywords:
        fallback = str(user_question or "").strip()
        if fallback:
            keywords = [fallback]
    # 最多 3 个，保持顺序
    deduped: list[str] = []
    for kw in keywords:
        if kw not in deduped:
            deduped.append(kw)
        if len(deduped) >= 3:
            break
    return deduped


def _fetch_keyword_facts(rag_index: Any, keyword: str, *, n: int = 5) -> dict[str, Any]:
    kw = str(keyword or "").strip()
    if not kw:
        return {"ok": False, "keyword": "", "items": [], "error": "empty_keyword"}
    hits = rag_index.query(kw, top_k=n, source_type_filter="research")
    if hits:
        items: list[dict[str, Any]] = []
        for hit in hits:
            snippet = str(hit.get("snippet") or "")
            title = snippet.split("title=")[-1].split(" org=")[0] if "title=" in snippet else snippet[:50]
            items.append({
                "title": title,
                "source_path": hit.get("source_path"),
                "score": hit.get("score"),
                "keyword": kw,
            })
        return {"ok": True, "keyword": kw, "items": items, "source": "rag"}
    rs = run_research_summary(keyword=kw, n=n)
    if isinstance(rs, dict):
        rs = dict(rs)
        rs["keyword"] = kw
    return rs


def build_research_facts_bundle(
    *,
    rag_index: Any,
    user_question: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """构建 research 用 facts_bundle；支持多关键词分别检索后合并。"""
    sym = str(payload.get("symbol") or "").strip()
    keywords = _research_keywords_from_payload(payload, user_question)
    primary_kw = keywords[0] if keywords else str(user_question or "").strip()

    merged_items: list[dict[str, Any]] = []
    total_sum = 0
    per_keyword: list[dict[str, Any]] = []
    any_ok = False
    degraded = False

    for kw in keywords:
        rs = _fetch_keyword_facts(rag_index, kw, n=5)
        per_keyword.append({"keyword": kw, "ok": bool(rs.get("ok")), "total": rs.get("total"), "count": len(rs.get("items") or [])})
        if rs.get("ok"):
            any_ok = True
        else:
            degraded = True
        try:
            total_sum += int(rs.get("total") or 0)
        except (TypeError, ValueError):
            pass
        for it in rs.get("items") or []:
            if isinstance(it, dict):
                row = dict(it)
                row.setdefault("keyword", kw)
                merged_items.append(row)

    merged_items = _dedupe_items(merged_items)[:15]
    rs_facts: dict[str, Any] = {
        "ok": any_ok,
        "keyword": primary_kw,
        "keywords": keywords,
        "items": merged_items,
        "total": total_sum if total_sum else len(merged_items),
        "source": "yanbaoke_search",
        "per_keyword": per_keyword,
    }

    fb = merge_facts_bundle(
        task_type="research",
        response_mode="narrative",
        user_question=user_question,
        symbols=[sym] if sym else [],
        research_facts=rs_facts,
        evidence_sources=[{"source_path": "yanbaoke:search", "source_type": "research"}],
        risk_flags=["normal"] if any_ok and not degraded else ["research:degraded"],
        trace={"executors": ["research_summary"], "keyword": primary_kw, "keywords": keywords},
    )
    return fb, primary_kw
