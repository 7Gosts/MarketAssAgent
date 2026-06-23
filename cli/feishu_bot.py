#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from infrastructure.adapters.feishu_longconn import run_feishu_longconn
from app.factory import create_runtime_services


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="飞书机器人长连接入口。")


def main() -> int:
    _args = build_parser().parse_args()
    services = create_runtime_services()
    run_feishu_longconn(services.feishu_adapter)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
