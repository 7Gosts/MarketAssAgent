#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.feishu_adapter import (
    get_tenant_access_token,
    send_interactive_message,
)
from config.runtime_config import get_analysis_config
from interfaces.renderers.feishu_renderer import FeishuRenderer


DEFAULT_TABLE_MARKDOWN = """## 渲染冒烟测试

### 关键位

| 方向 | 价格 | 性质 |
|------|------|------|
| 阻力 | $72.82 | 当前高点 |
| 阻力 | $73.00 | 心理整数关口 |
| 支撑 | $71.72 | 回踩关键位 |
"""


def _build_card_from_lark_md(text: str) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "FeishuRenderer Smoke Test"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": text or "（空响应）"}},
        ],
    }


def _read_content(args: argparse.Namespace) -> str:
    if args.input_file:
        return Path(args.input_file).read_text(encoding="utf-8")
    if args.text:
        return args.text
    return DEFAULT_TABLE_MARKDOWN


async def _send_card(receive_id: str, receive_id_type: str, card: dict[str, Any]) -> dict[str, Any]:
    cfg = get_analysis_config()
    feishu = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    app_id = str(feishu.get("app_id", ""))
    app_secret = str(feishu.get("app_secret", ""))
    token = await get_tenant_access_token(app_id=app_id, app_secret=app_secret)
    return await send_interactive_message(
        tenant_access_token=token,
        receive_id=receive_id,
        card=card,
        receive_id_type=receive_id_type,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test Feishu markdown renderer without booting app.")
    parser.add_argument("--text", default="", help="Inline markdown content.")
    parser.add_argument("--input-file", default="", help="Markdown file path to load.")
    parser.add_argument("--send", action="store_true", help="Send rendered result to Feishu.")
    parser.add_argument("--receive-id", default="", help="Feishu receive_id (open_id/chat_id).")
    parser.add_argument("--receive-id-type", default="open_id", choices=["open_id", "chat_id"])
    args = parser.parse_args()

    content = _read_content(args)
    renderer = FeishuRenderer()
    rendered = renderer.render(content)

    if isinstance(rendered, dict):
        render_type = "card"
        card = rendered
    else:
        render_type = "text"
        card = _build_card_from_lark_md(rendered)

    print(json.dumps({"render_type": render_type, "content_len": len(content)}, ensure_ascii=False))
    print(json.dumps({"card_preview": card}, ensure_ascii=False)[:1200])

    if args.send:
        if not args.receive_id:
            raise SystemExit("--send requires --receive-id")
        resp = asyncio.run(_send_card(args.receive_id, args.receive_id_type, card))
        print(json.dumps({"send_resp": resp}, ensure_ascii=False))


if __name__ == "__main__":
    main()
