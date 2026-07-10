from __future__ import annotations

import json
from typing import Any

from langchain_openai import ChatOpenAI

from config.runtime_config import get_llm_runtime_settings, require_llm_model


def _create_llm() -> ChatOpenAI:
    settings = get_llm_runtime_settings()
    model = require_llm_model(settings, context="AssetDiscovery")
    kwargs: dict[str, Any] = {
        "model": model,
        "temperature": 0.0,
    }
    if settings.get("base_url"):
        kwargs["base_url"] = settings["base_url"]
    if settings.get("api_key"):
        kwargs["api_key"] = settings["api_key"]
    return ChatOpenAI(**kwargs)


def _extract_json_array(raw: str) -> list[dict[str, Any]]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            return [item for item in obj if isinstance(item, dict)]
    except Exception:
        pass

    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        obj = json.loads(text[start:end + 1])
    except Exception:
        return []
    return [item for item in obj if isinstance(item, dict)] if isinstance(obj, list) else []


def discover_asset_candidates(
    *,
    query: str,
    tradable_assets: list[dict[str, Any]] | None = None,
    max_candidates: int = 3,
) -> list[dict[str, Any]]:
    user_query = str(query or "").strip()
    if not user_query:
        return []

    try:
        llm = _create_llm()
    except Exception:
        return []

    known_assets = json.dumps(list(tradable_assets or [])[:30], ensure_ascii=False)
    system_prompt = (
        "你是金融标的解析器。"
        "任务：根据用户的中文或英文股票名称，输出 0 到 3 个最可能的可交易标的候选。"
        "优先股票，其次港股/美股；不要输出黄金或加密货币，除非用户明确提到。"
        "symbol 必须使用规范交易代码：A股用 600600.SH / 000625.SZ，港股用 00168.HK，美股用 NVDA。"
        "若存在明显歧义，应返回多个候选。"
        "只返回 JSON 数组，不要解释。"
        "每个元素结构："
        "{\"symbol\":\"...\",\"name\":\"...\",\"market\":\"CN|US|HK\",\"research_keyword\":\"...\","
        "\"aliases\":[\"...\"],\"confidence\":0.0}"
    )
    human_prompt = (
        f"用户请求：{user_query}\n"
        f"当前已知资产（避免重复发明格式，仅供参考）：{known_assets}\n"
        f"最多返回 {max_candidates} 个候选。"
    )

    try:
        response = llm.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": human_prompt},
            ]
        )
    except Exception:
        return []

    rows = _extract_json_array(getattr(response, "content", ""))
    out: list[dict[str, Any]] = []
    for row in rows[:max_candidates]:
        symbol = str(row.get("symbol") or "").strip().upper()
        market = str(row.get("market") or "").strip().upper()
        if not symbol or market not in {"CN", "US", "HK"}:
            continue
        confidence = row.get("confidence")
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.0
        out.append(
            {
                "symbol": symbol,
                "name": str(row.get("name") or symbol).strip() or symbol,
                "market": market,
                "data_symbol": symbol,
                "research_keyword": str(row.get("research_keyword") or row.get("name") or symbol).strip() or symbol,
                "aliases": [str(alias).strip() for alias in (row.get("aliases") or []) if str(alias).strip()],
                "tags": [],
                "confidence": max(0.0, min(confidence_value, 1.0)),
            }
        )
    return out
