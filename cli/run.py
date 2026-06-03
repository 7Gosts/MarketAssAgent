"""MarketAssAgent — 命令行 REPL 入口。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中（MarketAssAgent 自身即为项目根）
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from langchain_core.messages import HumanMessage

from core.graph import get_or_create_graph
from memory.session_manager import MarketSessionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_single(text: str, *, session_id: str, channel: str = "cli") -> str:
    """单轮运行 Agent，返回最终回复。"""
    session_mgr = MarketSessionManager(repo_root=_REPO_ROOT)

    # 保存用户消息
    session_mgr.save_user_message(session_id, text)

    # 恢复 session state
    session_state = session_mgr.load_session(session_id)

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

    # 从 session state 恢复上下文
    if session_state.last_symbols:
        initial_state["current_symbol"] = session_state.last_symbols[0] if session_state.last_symbols else ""
    if session_state.last_interval:
        initial_state["current_interval"] = session_state.last_interval
    if session_state.last_provider:
        initial_state["current_provider"] = session_state.last_provider
    if session_state.last_facts_bundle:
        initial_state["last_snapshot"] = session_state.last_facts_bundle

    # 运行图
    graph = get_or_create_graph(repo_root=_REPO_ROOT)
    result = graph.invoke(initial_state)

    # 提取最终回复
    reply = result.get("final_reply", "")
    if not reply:
        # fallback: 取最后一条 AIMessage
        from langchain_core.messages import AIMessage
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage):
                content = msg.content
                if isinstance(content, str):
                    reply = content
                    break

    # 持久化 snapshot
    snapshot = result.get("last_snapshot")
    output_refs = result.get("output_refs")
    if snapshot:
        session_mgr.save_snapshot(session_id, snapshot, output_refs)

    # 保存回复
    if reply:
        session_mgr.save_reply(session_id, reply)

    return reply


def run_repl(*, session_id: str) -> None:
    """交互式 REPL。"""
    print("=" * 60)
    print("MarketAssAgent — 交互模式（输入 q 退出）")
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
            reply = run_single(text, session_id=session_id)
            print(f"\n助手: {reply}")
        except Exception as e:
            logger.exception("Agent 执行出错")
            print(f"\n[错误] {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MarketAssAgent CLI")
    parser.add_argument("text", nargs="?", help="单轮输入文本（省略则进入交互模式）")
    parser.add_argument("--session-id", default=None, help="Session ID（默认自动生成）")
    args = parser.parse_args()

    session_id = args.session_id or f"cli_{_REPO_ROOT.name}_{id(_REPO_ROOT)}"

    if args.text:
        reply = run_single(args.text, session_id=session_id)
        print(reply)
    else:
        run_repl(session_id=session_id)


if __name__ == "__main__":
    main()