"""FeishuCardBuilder — 飞书交互式卡片 JSON 构建器

原项目使用 app/formatters/feishu.py 构建卡片，此处为等价新实现。
飞书卡片文档参考: https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-json-structure
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CardSection:
    """卡片分区"""
    title: str | None = None
    content_md: str = ""
    elements: list[dict[str, Any]] = field(default_factory=list)


class FeishuCardBuilder:
    """Builder 模式构建飞书交互式卡片 JSON

    使用方式:
        card = FeishuCardBuilder("BTCUSDT 4h 分析")
            .add_trend("偏多", 78)
            .add_key_levels([62000, 60500], [65000, 66500])
            .add_structure("均线多头排列，量价配合良好")
            .add_suggestion("若站稳62000可考虑试多，目标65000")
            .add_disclaimer()
            .build()
    """

    # 趋势→颜色映射
    _TREND_TEMPLATE = {
        "偏多": "blue",
        "多头": "blue",
        "看涨": "blue",
        "偏空": "red",
        "空头": "red",
        "看跌": "red",
        "震荡": "turquoise",
        "中性": "turquoise",
    }

    def __init__(
        self, header_title: str = "", header_template: str = "blue"
    ) -> None:
        self._header_title = header_title
        self._header_template = header_template
        self._sections: list[CardSection] = []
        self._actions: list[dict[str, Any]] = []
        self._disclaimer: str = "仅供技术分析与程序化演示，不构成投资建议。投资有风险，入市需谨慎。"

    # ── Builder 方法 ──

    def add_header(self, title: str, template: str = "blue") -> FeishuCardBuilder:
        """设置卡片标题"""
        self._header_title = title
        self._header_template = template
        return self

    def add_trend(self, trend: str, confidence: int) -> FeishuCardBuilder:
        """添加趋势行"""
        # 根据趋势自动选择 header 颜色
        self._header_template = self._TREND_TEMPLATE.get(trend, "turquoise")

        confidence_bar = self._build_confidence_bar(confidence)
        md = f"**趋势:** {trend} | 置信度 {confidence}%\n{confidence_bar}"
        self._sections.append(CardSection(content_md=md))
        return self

    def add_key_levels(
        self,
        support: list[float] | list[int],
        resistance: list[float] | list[int],
    ) -> FeishuCardBuilder:
        """添加关键位分区"""
        support_str = " / ".join(str(s) for s in support)
        resistance_str = " / ".join(str(r) for r in resistance)
        md = f"**关键位**\n支撑: {support_str}\n阻力: {resistance_str}"
        self._sections.append(CardSection(content_md=md))
        return self

    def add_structure(self, structure: str) -> FeishuCardBuilder:
        """添加结构分析"""
        md = f"**结构分析**\n{structure}"
        self._sections.append(CardSection(content_md=md))
        return self

    def add_suggestion(self, suggestion: str) -> FeishuCardBuilder:
        """添加条件化建议"""
        md = f"**操作建议**\n{suggestion}"
        self._sections.append(CardSection(content_md=md))
        return self

    def add_disclaimer(self, text: str | None = None) -> FeishuCardBuilder:
        """添加免责声明"""
        if text:
            self._disclaimer = text
        return self

    def add_action_button(
        self, tag: str, text: str, value: dict[str, Any]
    ) -> FeishuCardBuilder:
        """添加交互按钮"""
        self._actions.append(
            {"tag": "button", "text": {"tag": "plain_text", "content": text},
             "type": tag, "value": value}
        )
        return self

    def add_custom_section(self, content_md: str) -> FeishuCardBuilder:
        """添加自定义 Markdown 分区"""
        self._sections.append(CardSection(content_md=content_md))
        return self

    # ── 构建方法 ──

    def build(self) -> dict[str, Any]:
        """构建完整卡片 JSON"""
        elements: list[dict[str, Any]] = []

        # 逐个添加分区
        for i, section in enumerate(self._sections):
            # 分区内容
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": section.content_md},
            })

            # 分区之间加分割线（最后一个分区后不加）
            if i < len(self._sections) - 1:
                elements.append({"tag": "hr"})

        # 交互按钮组
        if self._actions:
            elements.append({"tag": "hr"})
            elements.append({"tag": "action", "actions": self._actions})

        # 免责声明（note 区域）
        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": self._disclaimer}],
        })

        card: dict[str, Any] = {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": self._header_template,
                "title": {
                    "tag": "plain_text",
                    "content": self._header_title or "市场分析",
                },
            },
            "elements": elements,
        }

        return card

    def build_safe(self) -> tuple[dict[str, Any], str | None]:
        """安全构建：返回 (card_json, error)。失败时 error 非空，可用于 fallback"""
        try:
            card = self.build()
            # 基本结构校验
            if not card.get("elements"):
                return {}, "卡片 elements 为空"
            return card, None
        except Exception as e:
            return {}, str(e)

    # ── 内部方法 ──

    def _build_confidence_bar(self, confidence: int) -> str:
        """构建置信度进度条（用 emoji 模拟）"""
        filled = min(max(confidence // 10, 0), 10)
        empty = 10 - filled
        return "▓" * filled + "░" * empty


def format_analysis_as_card(result: dict[str, Any]) -> FeishuCardBuilder:
    """从 Agent 输出结果自动构建飞书卡片

    从 result["recommendation"] / result["last_snapshot"] / result["polished_text"] 提取数据。
    """
    builder = FeishuCardBuilder()

    # 提取 recommendation
    rec = result.get("recommendation") or {}
    text = rec.get("text", "")
    disclaimer = rec.get("disclaimer", "")

    # 提取 snapshot / analysis_result
    snapshot = result.get("last_snapshot") or {}
    analysis = result.get("analysis_result") or {}

    # 从 snapshot 中提取结构化数据
    symbol = snapshot.get("symbol") or analysis.get("symbol") or "标的"
    interval = snapshot.get("interval") or analysis.get("interval") or ""
    trend = snapshot.get("trend") or analysis.get("trend") or "震荡"
    confidence = snapshot.get("confidence") or analysis.get("confidence") or 60
    key_levels = snapshot.get("key_levels") or analysis.get("key_levels") or {}
    structure = snapshot.get("structure") or analysis.get("structure") or ""

    # 设置标题
    title = f"{symbol} {interval} 技术分析" if interval else f"{symbol} 技术分析"
    builder.add_header(title)

    # 趋势
    builder.add_trend(trend, confidence)

    # 关键位
    support = key_levels.get("support", [])
    resistance = key_levels.get("resistance", [])
    if support or resistance:
        builder.add_key_levels(support, resistance)

    # 结构分析
    if structure:
        builder.add_structure(structure)

    # 建议：从 recommendation text 中提取（含条件化建议段落）
    if text:
        # 尝试提取"建议"部分
        suggestion_match = re.search(
            r"(?:建议|操作建议|我的建议|可考虑)[:：]\s*(.+?)(?:\n|$)",
            text,
            re.IGNORECASE,
        )
        if suggestion_match:
            builder.add_suggestion(suggestion_match.group(1).strip())
        else:
            # 整条作为建议
            builder.add_suggestion(text[:200])

    # 免责声明
    if disclaimer:
        builder.add_disclaimer(disclaimer)

    # 如果没有任何分区数据，使用 polished_text / raw text 作为整体内容
    if not builder._sections:
        polished = result.get("polished_text") or text or "分析完成"
        builder.add_custom_section(polished[:500])

    return builder