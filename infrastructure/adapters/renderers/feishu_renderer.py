from __future__ import annotations

import re
from typing import Any

from .base import BaseRenderer

TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
SEPARATOR_RE = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")


class FeishuRenderer(BaseRenderer[str | dict[str, Any]]):
    """Render markdown for Feishu transport.

    - simple content -> lark_md text (str)
    - complex content -> interactive card payload (dict)
    """

    def render(self, content: str) -> str | dict[str, Any]:
        # 正向修复：统一走 lark_md 文本渲染，避免 schema2 复杂卡片在首轮被拒后降级为原始 Markdown 展示。
        return self._render_as_markdown_text(content)

    def _is_complex(self, content: str) -> bool:
        has_table = re.search(r"^\s*\|.*\|\s*$", content, re.MULTILINE) is not None
        has_code_block = "```" in content
        headings = re.findall(r"^#{1,6}\s+", content, re.MULTILINE)
        many_headings = len(headings) >= 3
        return has_table or has_code_block or many_headings

    def _contains_table(self, content: str) -> bool:
        lines = content.splitlines()
        for idx in range(len(lines) - 1):
            if TABLE_ROW_RE.match(lines[idx]) and self._is_separator_row(lines[idx + 1]):
                return True
        return False

    def _is_separator_row(self, line: str) -> bool:
        raw = line.strip()
        if not SEPARATOR_RE.match(raw):
            return False
        payload = raw.strip("|").replace(" ", "")
        return bool(payload) and all(ch in "-:|" for ch in payload) and "-" in payload

    def _parse_markdown_table(self, content: str) -> list[list[str]]:
        rows: list[list[str]] = []
        for line in content.splitlines():
            if "|" not in line:
                continue
            if self._is_separator_row(line):
                continue
            stripped = line.strip()
            if not (stripped.startswith("|") and stripped.endswith("|")):
                continue
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if cols:
                rows.append(cols)
        return rows

    def _render_table_as_schema2_card(self, content: str) -> dict[str, Any]:
        elements: list[dict[str, Any]] = []
        for block_type, payload in self._split_blocks(content):
            if block_type == "table":
                rows = self._parse_markdown_table(payload)
                table = self._build_schema2_table(rows)
                if table:
                    elements.append(table)
                continue
            text = payload.strip()
            if text:
                elements.append(
                    {
                        "tag": "markdown",
                        "content": text,
                        "text_align": "left",
                        "text_size": "normal",
                        "margin": "0px 0px 0px 0px",
                    }
                )

        if not elements:
            elements = [{"tag": "markdown", "content": "（空响应）"}]

        return {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {
                "title": {"tag": "plain_text", "content": "市场助手回复"},
                "template": "blue",
                "padding": "12px 12px 12px 12px",
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "elements": elements,
            },
        }

    def _split_blocks(self, content: str) -> list[tuple[str, str]]:
        lines = content.splitlines()
        blocks: list[tuple[str, str]] = []
        text_buf: list[str] = []
        i = 0
        n = len(lines)
        while i < n:
            if i + 1 < n and TABLE_ROW_RE.match(lines[i] or "") and self._is_separator_row(lines[i + 1] or ""):
                if text_buf:
                    blocks.append(("text", "\n".join(text_buf).strip()))
                    text_buf = []
                tlines = [lines[i], lines[i + 1]]
                j = i + 2
                while j < n and TABLE_ROW_RE.match(lines[j] or ""):
                    tlines.append(lines[j])
                    j += 1
                blocks.append(("table", "\n".join(tlines)))
                i = j
                continue
            text_buf.append(lines[i])
            i += 1
        if text_buf:
            blocks.append(("text", "\n".join(text_buf).strip()))
        return [(t, p) for t, p in blocks if p]

    def _build_schema2_table(self, rows: list[list[str]]) -> dict[str, Any] | None:
        if len(rows) < 2:
            return None
        header = rows[0]
        body = rows[1:]
        col_count = len(header)
        widths = self._infer_table_widths(rows)
        columns = [
            {
                "data_type": "text",
                "name": f"col_{idx+1}",
                "display_name": str(name or f"列{idx+1}"),
                "horizontal_align": "left",
                "width": widths[idx],
            }
            for idx, name in enumerate(header)
        ]
        mapped_rows: list[dict[str, str]] = []
        for row in body:
            item: dict[str, str] = {}
            for idx in range(col_count):
                key = f"col_{idx+1}"
                raw = str(row[idx] if idx < len(row) else "")
                item[key] = self._strip_inline_markdown(raw)
            mapped_rows.append(item)
        return {
            "tag": "table",
            "columns": columns,
            "rows": mapped_rows,
            "row_height": "low",
            "header_style": {"background_style": "grey", "bold": True, "lines": 1},
            "page_size": 8,
            "margin": "0px 0px 0px 0px",
        }

    def _infer_table_widths(self, rows: list[list[str]]) -> list[str]:
        """根据每列最长单元格的字符数动态计算列宽。"""
        if not rows or len(rows) < 1:
            return []
        header = rows[0]
        col_count = len(header)
        max_lengths = [len(str(h or "")) for h in header]
        for row in rows[1:]:
            for idx, cell in enumerate(row):
                if idx < col_count:
                    max_lengths[idx] = max(max_lengths[idx], len(str(cell or "")))

        widths: list[str] = []
        for max_len in max_lengths:
            if max_len <= 8:
                widths.append("80px")
            elif max_len <= 15:
                widths.append("120px")
            elif max_len <= 25:
                widths.append("180px")
            elif max_len <= 40:
                widths.append("260px")
            else:
                widths.append("320px")
        return widths

    def _strip_inline_markdown(self, text: str) -> str:
        cleaned = text
        # bold / italic / inline code
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"\*(.*?)\*", r"\1", cleaned)
        cleaned = re.sub(r"`(.*?)`", r"\1", cleaned)
        # links [x](y) -> x
        cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", cleaned)
        return cleaned.strip()

    def _render_as_markdown_text(self, content: str) -> str:
        lines = content.splitlines()
        out: list[str] = []
        table_buffer: list[str] = []

        def flush_table() -> None:
            nonlocal table_buffer
            if not table_buffer:
                return
            rows: list[list[str]] = []
            for raw in table_buffer:
                cols = [c.strip() for c in raw.strip().strip("|").split("|")]
                if cols:
                    rows.append(cols)
            if len(rows) >= 3:
                headers = rows[0]
                for row in rows[2:]:
                    pairs: list[str] = []
                    for idx, cell in enumerate(row):
                        if not cell:
                            continue
                        key = headers[idx] if idx < len(headers) else f"列{idx+1}"
                        pairs.append(f"{key}: {cell}")
                    if pairs:
                        out.append(f"- {' | '.join(pairs)}")
            table_buffer = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("|") and stripped.endswith("|"):
                table_buffer.append(line)
                continue
            if table_buffer:
                flush_table()

            if re.fullmatch(r"\s*[-*_]{3,}\s*", stripped):
                continue

            heading = re.match(r"^\s*#{1,6}\s*(.+?)\s*$", line)
            if heading:
                title = heading.group(1).strip()
                if title:
                    out.append(f"**{title}**")
                continue

            out.append(line)

        if table_buffer:
            flush_table()

        normalized = "\n".join(out)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
        return normalized or "（空响应）"

    def _render_as_card(self, content: str) -> dict[str, Any]:
        lines = content.splitlines()
        elements: list[dict[str, Any]] = []
        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            heading = re.match(r"^\s*#{1,6}\s*(.+?)\s*$", line)
            if heading:
                text = heading.group(1).strip()
                if text:
                    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**{text}**"}})
                continue
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": line}})

        if not elements:
            elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "（空响应）"}}]

        return {
            "config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text", "content": "市场助手回复"}},
            "elements": elements,
        }
