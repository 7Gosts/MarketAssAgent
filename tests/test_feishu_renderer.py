from __future__ import annotations

from infrastructure.adapters.renderers.feishu_renderer import FeishuRenderer


def test_simple_markdown_renders_as_text():
    renderer = FeishuRenderer()
    content = "## 标题\n\n- a\n- b"
    rendered = renderer.render(content)
    assert isinstance(rendered, str)
    assert "**标题**" in rendered
