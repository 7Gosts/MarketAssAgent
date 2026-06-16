"""JSON session 持久化回归测试。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from memory.json_persistence import JsonSessionPersistence


def test_json_session_append_and_read_recent_messages():
    with tempfile.TemporaryDirectory() as tmp:
        persistence = JsonSessionPersistence(Path(tmp))
        session_id = "web_session_001"

        persistence.append_message(session_id, "user", "hello")
        persistence.append_message(session_id, "assistant", "hi there")

        recent = persistence.get_recent_messages(session_id, limit=8)
        assert len(recent) == 2
        assert recent[0]["role"] == "user"
        assert recent[0]["text"] == "hello"
        assert recent[1]["role"] == "assistant"
        assert recent[1]["text"] == "hi there"


def test_json_session_history_written_to_jsonl_file():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Path(tmp)
        persistence = JsonSessionPersistence(storage)
        session_id = "feishu_u123"

        persistence.append_message(session_id, "user", "test msg")

        history_path = storage / session_id / "_history.jsonl"
        assert history_path.is_file()
        lines = history_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["role"] == "user"
        assert record["text"] == "test msg"
