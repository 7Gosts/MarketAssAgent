#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "runtime", ROOT / "src", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from infrastructure.adapters.feishu_adapter import (
    get_tenant_access_token,
    send_interactive_message,
    send_post_message,
)
from config.runtime_config import get_analysis_config
from infrastructure.adapters.renderers.feishu_renderer import FeishuRenderer


DEFAULT_TABLE_MARKDOWN = """## 渲染冒烟测试

### 关键位

| 方向 | 价格 | 性质 |
|------|------|------|
| 阻力 | $72.82 | 当前高点 |
| 阻力 | $73.00 | 心理整数关口 |
| 支撑 | $71.72 | 回踩关键位 |
"""


def _build_interactive_payload_from_lark_md(text: str) -> dict[str, Any]:
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


def _resolve_app_credentials() -> tuple[str, str]:
    cfg = get_analysis_config()
    feishu = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    app_id = str(feishu.get("app_id", ""))
    app_secret = str(feishu.get("app_secret", ""))
    if not app_id or not app_secret:
        raise RuntimeError("feishu.app_id / feishu.app_secret 未配置")
    return app_id, app_secret


async def _send_interactive(
    receive_id: str,
    receive_id_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    app_id, app_secret = _resolve_app_credentials()
    token = await get_tenant_access_token(app_id=app_id, app_secret=app_secret)
    return await send_interactive_message(
        tenant_access_token=token,
        receive_id=receive_id,
        card=payload,
        receive_id_type=receive_id_type,
    )


async def _send_post(
    receive_id: str,
    receive_id_type: str,
    text: str,
) -> dict[str, Any]:
    app_id, app_secret = _resolve_app_credentials()
    token = await get_tenant_access_token(app_id=app_id, app_secret=app_secret)
    return await send_post_message(
        tenant_access_token=token,
        receive_id=receive_id,
        text=text,
        receive_id_type=receive_id_type,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test Feishu renderer without booting app.")
    parser.add_argument("--text", default="", help="Inline markdown content.")
    parser.add_argument("--input-file", default="", help="Markdown file path to load.")
    parser.add_argument("--send", action="store_true", help="Send rendered result to Feishu.")
    parser.add_argument("--receive-id", default="", help="Feishu receive_id (open_id/chat_id).")
    parser.add_argument("--receive-id-type", default="open_id", choices=["open_id", "chat_id"])
    parser.add_argument(
        "--send-mode",
        default="auto",
        choices=["auto", "interactive", "post"],
        help="Feishu send mode when --send is enabled.",
    )
    args = parser.parse_args()

    content = _read_content(args)
    renderer = FeishuRenderer()
    rendered = renderer.render(content)

    if isinstance(rendered, dict):
        render_type = "interactive_payload"
        interactive_payload = rendered
    else:
        render_type = "text"
        interactive_payload = _build_interactive_payload_from_lark_md(rendered)

    print(json.dumps({"render_type": render_type, "content_len": len(content)}, ensure_ascii=False))
    print(json.dumps({"interactive_preview": interactive_payload}, ensure_ascii=False)[:1200])

    if args.send:
        if not args.receive_id:
            raise SystemExit("--send requires --receive-id")
        if args.send_mode == "interactive":
            resp = asyncio.run(
                _send_interactive(args.receive_id, args.receive_id_type, interactive_payload)
            )
        elif args.send_mode == "post":
            text_payload = rendered if isinstance(rendered, str) else content
            resp = asyncio.run(
                _send_post(args.receive_id, args.receive_id_type, text_payload)
            )
        else:
            if isinstance(rendered, dict):
                resp = asyncio.run(
                    _send_interactive(args.receive_id, args.receive_id_type, interactive_payload)
                )
            else:
                resp = asyncio.run(
                    _send_post(args.receive_id, args.receive_id_type, rendered)
                )
        print(json.dumps({"send_resp": resp}, ensure_ascii=False))


if __name__ == "__main__":
    main()
