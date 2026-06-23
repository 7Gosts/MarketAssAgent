from __future__ import annotations

from infrastructure.adapters.renderers.feishu_renderer import FeishuRenderer


def test_simple_markdown_renders_as_text():
    renderer = FeishuRenderer()
    content = "## 标题\n\n- a\n- b"
    rendered = renderer.render(content)
    assert isinstance(rendered, str)
    assert "**标题**" in rendered


def test_table_markdown_renders_as_schema2_payload():
    renderer = FeishuRenderer()
    content = (
        "## 行情\n\n"
        "前文说明。\n\n"
        "| 方向 | 价格 |\n"
        "|---|---|\n"
        "| 支撑 | 1732 |\n"
        "| 阻力 | 1769 |\n"
        "\n后文结论。"
    )
    rendered = renderer.render(content)
    assert isinstance(rendered, dict)
    assert rendered.get("schema") == "2.0"
    assert rendered.get("header", {}).get("title", {}).get("content") == "市场助手回复"
    elements = rendered["body"]["elements"]
    assert any(e.get("tag") == "markdown" and "前文说明。" in e.get("content", "") for e in elements)
    assert any(e.get("tag") == "markdown" and "后文结论。" in e.get("content", "") for e in elements)
    table = next(e for e in elements if e.get("tag") == "table")
    assert table["rows"][0]["col_1"] == "支撑"


def test_table_uses_schema2_by_default():
    renderer = FeishuRenderer()
    content = (
        "前文\n\n"
        "| 方向 | 价格 |\n"
        "|---|---|\n"
        "| 支撑 | 1732 |\n"
        "| 阻力 | 1769 |\n"
        "\n后文"
    )
    rendered = renderer.render(content)
    assert isinstance(rendered, dict)
    assert rendered.get("schema") == "2.0"
    elements = rendered.get("body", {}).get("elements", [])
    assert any(e.get("tag") == "table" for e in elements)
    assert any(e.get("tag") == "markdown" and "前文" in e.get("content", "") for e in elements)
    table = next(e for e in elements if e.get("tag") == "table")
    assert table["columns"][0]["display_name"] == "方向"
    assert table["rows"][0]["col_1"] == "支撑"
    # 列宽现为动态计算（基于最长单元格字符数），不再依赖静态关键字规则
    assert table["columns"][1]["width"] in ("80px", "120px", "140px", "180px", "260px", "320px")


def test_schema2_table_strips_inline_markdown():
    renderer = FeishuRenderer()
    content = (
        "| 位置 | 价格 | 说明 |\n"
        "|---|---|---|\n"
        "| 上方阻力 | **$1,769** | 当前 15m 高点 |\n"
    )
    rendered = renderer.render(content)
    assert isinstance(rendered, dict)
    table = next(e for e in rendered["body"]["elements"] if e.get("tag") == "table")
    assert table["rows"][0]["col_2"] == "$1,769"
