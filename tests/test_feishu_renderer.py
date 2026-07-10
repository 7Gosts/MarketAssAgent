from __future__ import annotations

from infrastructure.adapters.renderers.feishu_renderer import FeishuRenderer


def test_simple_markdown_renders_as_text():
    renderer = FeishuRenderer()
    content = "## 标题\n\n- a\n- b"
    rendered = renderer.render(content)
    assert isinstance(rendered, str)
    assert "**标题**" in rendered


def test_table_markdown_renders_as_text_payload():
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
    assert isinstance(rendered, str)
    assert "前文说明。" in rendered
    assert "后文结论。" in rendered
    assert "- 方向: 支撑 | 价格: 1732" in rendered
    assert "- 方向: 阻力 | 价格: 1769" in rendered


def test_table_renders_as_text_by_default():
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
    assert isinstance(rendered, str)
    assert "前文" in rendered
    assert "后文" in rendered
    assert "- 方向: 支撑 | 价格: 1732" in rendered


def test_text_table_strips_inline_markdown():
    renderer = FeishuRenderer()
    content = (
        "| 位置 | 价格 | 说明 |\n"
        "|---|---|---|\n"
        "| 上方阻力 | **$1,769** | 当前 15m 高点 |\n"
    )
    rendered = renderer.render(content)
    assert isinstance(rendered, str)
    assert "- 位置: 上方阻力 | 价格: $1,769 | 说明: 当前 15m 高点" in rendered
