from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

_MAX_FEISHU_MESSAGE_CHARS = 4000
_MAX_CARD_SECTIONS = 8

_LIST_LINE = re.compile(r"^([-*•]|\d+\.)\s+")
_SECTION_HEADER = re.compile(r"^\*\*[^*\n]+\*\*\s*$")
_HASH_HEADER = re.compile(r"^(#{1,6})\s+(.+)$")
_CODE_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)


def normalize_lark_md(text: str) -> str:
    """将 Writer 输出的 Markdown 规范为飞书 lark_md 更易渲染的形态。

    网页侧 markdown-it 对列表/标题较宽容；飞书 lark_md 要求列表前有空行、
    且不支持 # 标题语法。本函数仅做展示层转换，不改语义。
    """
    raw = (text or "").replace("\r\n", "\n").strip()
    if not raw:
        return ""
    raw = _CODE_FENCE.sub(r"\1", raw).strip()
    lines = raw.split("\n")
    out: list[str] = []
    prev_line = ""
    for line in lines:
        stripped = line.strip()
        if stripped in {"---", "***", "___"}:
            if out and out[-1].strip():
                out.append("")
            prev_line = ""
            continue
        hash_m = _HASH_HEADER.match(stripped)
        if hash_m:
            stripped = f"**{hash_m.group(2).strip()}**"
            line = stripped
        if stripped.startswith("• "):
            stripped = f"- {stripped[2:].strip()}"
            line = stripped
        is_list = bool(_LIST_LINE.match(stripped))
        prev_stripped = prev_line.strip()
        prev_is_list = bool(_LIST_LINE.match(prev_stripped))
        prev_is_section = bool(_SECTION_HEADER.match(prev_stripped))
        if is_list and prev_stripped and not prev_is_list:
            if out and out[-1].strip():
                out.append("")
        if is_list and prev_is_section and out and out[-1].strip():
            out.append("")
        out.append(line.rstrip())
        prev_line = line
    result = "\n".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def split_card_sections(text: str, *, max_sections: int = _MAX_CARD_SECTIONS) -> list[str]:
    """按 **小标题** 段首拆分为多个卡片区块（便于 hr 分隔）。"""
    normalized = normalize_lark_md(text)
    if not normalized:
        return []
    parts = re.split(r"\n\n(?=\*\*[^*\n]+\*\*)", normalized)
    sections = [p.strip() for p in parts if p.strip()]
    if len(sections) <= max_sections:
        return sections
    head = sections[: max_sections - 1]
    tail = "\n\n".join(sections[max_sections - 1 :])
    return head + [tail]


def build_card_elements(reply_text: str) -> list[dict[str, Any]]:
    """从 reply_text 构建飞书卡片 elements（多段 lark_md + 免责 note）。"""
    sections = split_card_sections(reply_text)
    if not sections:
        return []
    elements: list[dict[str, Any]] = []
    for i, section in enumerate(sections):
        if i > 0:
            elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": section}})
    elements.append({"tag": "note", "elements": [{"tag": "plain_text", "content": _DISCLAIMER}]})
    return elements


def split_feishu_text(text: str, max_len: int = _MAX_FEISHU_MESSAGE_CHARS) -> list[str]:
    """飞书单条消息长度限制下的分段（仅格式层，不决定内容重点）。"""
    t = normalize_lark_md(text)
    if not t:
        return []
    if len(t) <= max_len:
        return [t]
    parts: list[str] = []
    buf: list[str] = []
    acc = 0
    for block in t.split("\n\n"):
        extra = len(block) + (2 if buf else 0)
        if acc + extra <= max_len:
            buf.append(block)
            acc += extra
            continue
        if buf:
            parts.append("\n\n".join(buf))
        buf = []
        acc = 0
        if len(block) <= max_len:
            buf.append(block)
            acc = len(block)
        else:
            for i in range(0, len(block), max_len):
                parts.append(block[i : i + max_len])
    if buf:
        parts.append("\n\n".join(buf))
    return parts if parts else [t[:max_len]]


# ── 飞书交互式卡片薄包装 ──

_DISCLAIMER = "仅供技术分析与程序化演示，不构成投资建议。"
FEISHU_CARD_MODE_DISABLED_MSG = "当前未启用飞书卡片模式，请开启 FEISHU_CARD_MODE。"
FEISHU_CARD_BUILD_FAILED_MSG = "卡片构建失败，请稍后重试。"
FEISHU_CARD_SEND_FAILED_MSG = "消息发送失败，请稍后重试。"


@dataclass(frozen=True)
class FeishuDelivery:
    """飞书渠道投递形态（由 adapter 在发送前构建，不进 core state）。"""

    kind: Literal["card", "text"]
    card: dict[str, Any] | None = None
    text: str | None = None


def build_feishu_delivery(
    *,
    reply_text: str,
    task_type: str,
    facts_bundle: dict[str, Any] | None,
    card_mode: bool,
) -> FeishuDelivery:
    """根据 AgentResponse 内容构建飞书投递形态。"""
    tt = str(task_type or "analysis").strip().lower()
    text = (reply_text or "").strip()

    if tt == "chat":
        return FeishuDelivery(kind="text", text=text or "我这次没有稳定生成回复。")

    if not card_mode:
        return FeishuDelivery(kind="text", text=FEISHU_CARD_MODE_DISABLED_MSG)

    fb = facts_bundle if isinstance(facts_bundle, dict) else {}
    card = wrap_reply_as_card(tt, fb, text)
    if card:
        return FeishuDelivery(kind="card", card=card)
    return FeishuDelivery(kind="text", text=FEISHU_CARD_BUILD_FAILED_MSG)


def wrap_reply_as_card(
    task_type: str,
    facts_bundle: dict[str, Any],
    reply_text: str,
) -> dict[str, Any] | None:
    """给 reply_text 包一层飞书卡片 header，正文按段落入多个 lark_md div。"""
    if task_type == "chat":
        return None
    title = _card_title(task_type, facts_bundle)
    if not title:
        return None
    elements = build_card_elements(reply_text)
    if not elements:
        return None
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "turquoise",
        },
        "elements": elements,
    }


_MAX_CARD_TITLE_CHARS = 100


def _short_symbol_label(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    if not s:
        return ""
    if s.endswith("_USDT"):
        return s[:-5].lower()
    if s in {"AU9999", "AU9995"}:
        return "黄金"
    if "." in s:
        return s.split(".", 1)[0].lower()
    return s.lower()


def _format_card_price(price: Any) -> str:
    try:
        val = float(price)
    except (TypeError, ValueError):
        return "—"
    if val >= 1000:
        text = f"{val:.1f}"
    elif val >= 1:
        text = f"{val:.2f}"
    else:
        text = f"{val:.4f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _normalize_trend_label(trend: Any) -> str:
    t = str(trend or "").strip()
    if not t:
        return "—"
    if "偏多" in t:
        return "偏多"
    if "偏空" in t:
        return "偏空"
    if "震荡" in t or "观察" in t or "中性" in t:
        return "震荡"
    return t[:6]


def _symbol_snapshot_title_parts(rows: list[dict[str, Any]]) -> list[str]:
    parts: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = _short_symbol_label(str(row.get("symbol") or ""))
        if not label:
            continue
        price = _format_card_price(row.get("last_price"))
        trend = _normalize_trend_label(row.get("trend") or row.get("tendency"))
        parts.append(f"{label} 现价{price} {trend}")
    return parts


def _join_card_title_parts(parts: list[str], *, max_len: int = _MAX_CARD_TITLE_CHARS) -> str:
    if not parts:
        return ""
    head = parts[:4]
    title = " | ".join(head)
    if len(parts) > 4:
        title = f"{title} 等{len(parts)}项"
    if len(title) > max_len:
        return title[: max_len - 1].rstrip() + "…"
    return title


def _collect_compare_rows(facts_bundle: dict[str, Any]) -> list[dict[str, Any]]:
    mf = facts_bundle.get("market_facts") if isinstance(facts_bundle.get("market_facts"), dict) else {}
    for key in ("multi_compare", "compare_summary"):
        node = mf.get(key) if isinstance(mf.get(key), dict) else {}
        rows = node.get("rows")
        if isinstance(rows, list) and rows:
            return [r for r in rows if isinstance(r, dict)]
    cf = facts_bundle.get("compare_facts") if isinstance(facts_bundle.get("compare_facts"), dict) else {}
    rows = cf.get("rows")
    if isinstance(rows, list) and rows:
        return [r for r in rows if isinstance(r, dict)]
    return []


def _card_title(task_type: str, facts_bundle: dict[str, Any]) -> str | None:
    """从 facts_bundle 提取卡片标题（现价 + 倾向；多标的用 | 连接）。"""
    if task_type == "sim_account":
        return "模拟账户概览"

    rows = _collect_compare_rows(facts_bundle)
    if len(rows) >= 2:
        title = _join_card_title_parts(_symbol_snapshot_title_parts(rows))
        if title:
            return title

    mf = facts_bundle.get("market_facts") or {}
    af = mf.get("analysis_facts") or {}
    if isinstance(af, dict) and af.get("symbol"):
        single = _symbol_snapshot_title_parts(
            [
                {
                    "symbol": af.get("symbol"),
                    "last_price": af.get("last_price"),
                    "trend": af.get("tendency") or af.get("trend"),
                }
            ]
        )
        if single:
            return single[0]

    if len(rows) == 1:
        single = _symbol_snapshot_title_parts(rows)
        if single:
            return single[0]

    # compare 仅 symbols、无 rows 时的兜底
    cf = facts_bundle.get("compare_facts") or {}
    symbols = cf.get("symbols") if isinstance(cf.get("symbols"), list) else []
    symbols = [str(s).strip() for s in symbols if str(s).strip()]
    bundle_symbols = facts_bundle.get("symbols") if isinstance(facts_bundle.get("symbols"), list) else []
    bundle_symbols = [str(s).strip() for s in bundle_symbols if str(s).strip()]
    sym_list = symbols or bundle_symbols
    if len(sym_list) >= 2:
        labels = [_short_symbol_label(s) for s in sym_list[:4]]
        labels = [x for x in labels if x]
        if labels:
            return _join_card_title_parts(labels)
    if sym_list:
        return _short_symbol_label(sym_list[0]) or str(sym_list[0])

    # narrative / research
    rf = facts_bundle.get("research_facts") or {}
    kws = rf.get("keywords") if isinstance(rf.get("keywords"), list) else []
    kws = [str(k).strip() for k in kws if str(k).strip()]
    kw = rf.get("keyword") or rf.get("query")
    if kws:
        if len(kws) == 1:
            return f"研报线索 · {kws[0]}"
        if len(kws) == 2:
            return f"研报线索 · {kws[0]}、{kws[1]}"
        return f"研报线索 · {kws[0]} 等{len(kws)}项"
    if kw:
        return f"研报线索 · {kw}"
    return None
