from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from infrastructure.adapters import feishu_longconn


def test_messages_run_fifo_per_session_and_failure_does_not_block_followups(monkeypatch):
    class Handler:
        def register_p2_im_message_receive_v1(self, callback):
            self.on_message = callback
            return self

        def build(self):
            return self

    monkeypatch.setattr(feishu_longconn, "_import_lark", lambda: SimpleNamespace(
        EventDispatcherHandler=SimpleNamespace(builder=lambda *args: Handler()),
    ))
    first_started = threading.Event()
    release_first = threading.Event()
    other_finished = threading.Event()
    followups_finished = threading.Event()
    started = []
    completed = []

    async def handle_message(**message):
        message_id = message["message_id"]
        started.append(message_id)
        if message_id == "first":
            first_started.set()
            assert release_first.wait(5)
            completed.append(message_id)
            raise RuntimeError("first request failed")
        completed.append(message_id)
        if message_id == "other":
            other_finished.set()
        elif message_id == "third":
            followups_finished.set()
        return {"code": 0}

    handler = feishu_longconn.build_event_handler(SimpleNamespace(handle_longconn_message=handle_message))

    def receive(message_id, chat_id="oc_team", open_id="ou_alice"):
        handler.on_message(SimpleNamespace(event=SimpleNamespace(
            sender=SimpleNamespace(sender_type="user", tenant_key="tenant-a",
                                   sender_id=SimpleNamespace(open_id=open_id, user_id="")),
            message=SimpleNamespace(message_type="text", message_id=message_id, chat_id=chat_id,
                                    chat_type="group", content=json.dumps({"text": message_id})),
        )))

    try:
        receive("first")
        assert first_started.wait(3)
        receive("second", open_id="ou_bob")
        receive("third")
        receive("other", chat_id="oc_other")
        assert other_finished.wait(3)
        assert started == ["first", "other"]
        assert completed == ["other"]
    finally:
        release_first.set()
        followups_finished.wait(3)

    assert followups_finished.is_set()
    assert started == ["first", "other", "second", "third"]
    assert completed == ["other", "first", "second", "third"]
