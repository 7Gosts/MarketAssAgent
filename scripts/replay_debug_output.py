#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay/aggregate LLM token usage debug logs.")
    parser.add_argument(
        "--file",
        default="~/.marketassagent/debug/llm_token_usage.jsonl",
        help="Token usage jsonl path.",
    )
    parser.add_argument("--session-id", default="", help="Filter by one session_id.")
    parser.add_argument("--top", type=int, default=10, help="Top N expensive turns.")
    args = parser.parse_args()

    path = Path(args.file).expanduser().resolve()
    rows = _iter_jsonl(path)
    if not rows:
        print(f"[replay] no records found: {path}")
        return 0

    if args.session_id:
        rows = [r for r in rows if str(r.get("session_id") or "") == args.session_id]
        if not rows:
            print(f"[replay] no records for session_id={args.session_id}")
            return 0

    by_session: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        sid = str(row.get("session_id") or "unknown")
        by_session[sid]["calls"] += 1
        by_session[sid]["prompt_tokens"] += _to_int(row.get("prompt_tokens"))
        by_session[sid]["completion_tokens"] += _to_int(row.get("completion_tokens"))
        by_session[sid]["total_tokens"] += _to_int(row.get("total_tokens"))
        by_session[sid]["reasoning_tokens"] += _to_int(row.get("reasoning_tokens"))
        by_session[sid]["cached_prompt_tokens"] += _to_int(row.get("cached_prompt_tokens"))

    print(f"[replay] file={path}")
    print(f"[replay] records={len(rows)} sessions={len(by_session)}")
    print("")
    print("== Session Aggregates ==")
    ordered_sessions = sorted(
        by_session.items(),
        key=lambda kv: kv[1]["total_tokens"],
        reverse=True,
    )
    for sid, total in ordered_sessions:
        print(
            f"- {sid}: calls={total['calls']} prompt={total['prompt_tokens']} "
            f"completion={total['completion_tokens']} total={total['total_tokens']} "
            f"reasoning={total['reasoning_tokens']} cached_prompt={total['cached_prompt_tokens']}"
        )

    print("")
    print(f"== Top {max(args.top, 1)} Expensive Calls ==")
    ordered_calls = sorted(rows, key=lambda r: _to_int(r.get("total_tokens")), reverse=True)
    for row in ordered_calls[: max(args.top, 1)]:
        sid = str(row.get("session_id") or "unknown")
        ts = row.get("ts")
        print(
            f"- ts={ts} session_id={sid} prompt={_to_int(row.get('prompt_tokens'))} "
            f"completion={_to_int(row.get('completion_tokens'))} total={_to_int(row.get('total_tokens'))} "
            f"reasoning={_to_int(row.get('reasoning_tokens'))} cached_prompt={_to_int(row.get('cached_prompt_tokens'))}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
