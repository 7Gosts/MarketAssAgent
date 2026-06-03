#!/usr/bin/env python3
"""MarketAssAgent — Core LangGraph 入口（并行测试用）。

使用 core/graph.py 的完整 LangGraph 流程：
START → restore_session → init_context → reason → [tools → observe → reason]* → supervisor → persist_snapshot → END

与 cli/agent_run.py 对齐参数，方便对比测试。
当 core/ 链路完全稳定后，将切换 agent_run.py 到此路径。

用法：
    python cli/core_run.py "BTC_USDT 行情分析"     # 单轮
    python cli/core_run.py --interactive             # 交互模式
    python cli/core_run.py --session-id abc123       # 指定 session
    python cli/core_run.py --json                    # JSON 输出
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from langchain_core.messages import AIMessage, HumanMessage

from core.graph import get_or_create_graph
from memory.session_manager import MarketSessionManager


def run_core(text: str, *, session_id: str, channel: str = "cli", json_output: bool = False) -> str:
    """运行 core LangGraph Agent，返回回复文本或 JSON。"""
    session_mgr = MarketSessionManager(repo_root=_REPO_ROOT)
    session_mgr.save_user_message(session_id, text)

    # 构建初始 state
    initial_state = {
        "messages": [HumanMessage(content=text)],
        "session_id": session_id,
        "channel": channel,
        "iteration_count": 0,
        "current_symbol": "",
        "current_interval": "",
        "current_provider": "",
    }

    # 运行图
    graph = get_or_create_graph(repo_root=_REPO_ROOT, session_mgr=session_mgr, force_refresh=True)
    result = graph.invoke(initial_state)

    # 提取最终回复
    reply = result.get("final_reply", "")
    if not reply:
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage):
                content = msg.content
                if isinstance(content, str):
                    reply = content
                elif isinstance(content, list):
                    parts = []
                    for part in content:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            parts.append(part["text"])
                        elif isinstance(part, str):
                            parts.append(part)
                    reply = "\n".join(parts)
                break

    if json_output:
        output = {
            "reply": reply,
            "snapshot": result.get("last_snapshot"),
            "output_refs": result.get("output_refs"),
            "session_id": session_id,
            "iteration_count": result.get("iteration_count"),
            "has_disclaimer": result.get("has_disclaimer"),
        }
        return json.dumps(output, ensure_ascii=False, indent=2)

    return reply


def run_interactive(*, session_id: str) -> None:
    """交互式 REPL。"""
    print("=" * 60)
    print("MarketAssAgent Core — 交互模式（输入 q 退出）")
    print(f"Session: {session_id}")
    print("=" * 60)

    while True:
        try:
            text = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not text:
            continue
        if text.lower() in ("q", "quit", "exit"):
            print("再见！")
            break

        try:
            reply = run_core(text, session_id=session_id)
            print(f"\n助手: {reply}")
        except Exception as e:
            print(f"\n[错误] {e}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MarketAssAgent Core LangGraph 入口（并行测试用）"
    )
    parser.add_argument("text", nargs="?", default=None, help="用户输入文本")
    parser.add_argument("--session-id", default=None, help="会话 ID")
    parser.add_argument("--interactive", action="store_true", help="交互模式")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--channel", default="cli", help="渠道（cli/feishu/http）")
    args = parser.parse_args()

    import uuid
    session_id = args.session_id or uuid.uuid4().hex[:8]

    if args.interactive:
        run_interactive(session_id=session_id)
        return 0

    if args.text is None:
        print("错误：请提供输入文本，或使用 --interactive 进入交互模式")
        return 1

    output = run_core(args.text, session_id=session_id, channel=args.channel, json_output=args.json)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())