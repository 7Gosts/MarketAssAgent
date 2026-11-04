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


def test_longconn_runs_shared_plan_tracker_thread_and_notifies_through_adapter(monkeypatch):
    from tools import plan_tracking

    events = []
    monitor = {
        "destination": {"receive_id": "ou_alice", "receive_id_type": "open_id"},
        "conversation_scope": {
            "transport": "feishu",
            "tenant_id": "tenant-a",
            "visibility_scope": "private",
            "visibility_scope_id": "ou_alice",
            "actor_id": "ou_alice",
        },
        "notification_attempt": 1,
    }
    plan = {
        "plan_id": "plan_1",
        "symbol": "ETH_USDT",
        "direction": "short",
        "interval": "4h",
        "zone_low": 2274,
        "zone_high": 2283,
        "monitor": monitor,
    }
    candle = {"time": "2026-11-04T00:00:00+00:00", "high": 2278, "low": 2260, "close": 2270}

    async def run_tracker(*, notifier):
        await notifier(plan, candle)

    monkeypatch.setattr(plan_tracking, "run_plan_tracker", run_tracker)
    monkeypatch.setattr(plan_tracking, "build_plan_monitor_event", lambda _plan, _candle: "monitor event")

    class Thread:
        def __init__(self, *, target, args, name, daemon):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon

        def start(self):
            events.append(("thread", self.name, self.daemon))
            self.target(*self.args)

    monkeypatch.setattr(feishu_longconn.threading, "Thread", Thread)

    class Handler:
        def register_p2_im_message_receive_v1(self, _callback):
            return self

        def build(self):
            return self

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            events.append(("bot", "started"))

    monkeypatch.setattr(feishu_longconn, "_import_lark", lambda: SimpleNamespace(
        EventDispatcherHandler=SimpleNamespace(builder=lambda *_args: Handler()),
        LogLevel=SimpleNamespace(INFO=20),
        ws=SimpleNamespace(Client=Client),
    ))

    class Adapter:
        def get_app_credentials(self):
            return "app_id", "app_secret"

        async def handle_plan_monitor_event(self, **kwargs):
            events.append(("notice", kwargs))
            return {"code": 0}

    feishu_longconn.run_feishu_longconn(Adapter())

    assert events[0] == ("thread", "plan-tracker", True)
    assert events[1][0] == "notice"
    assert events[1][1]["text"] == "monitor event"
    assert events[1][1]["receive_id"] == "ou_alice"
    assert events[1][1]["receive_id_type"] == "open_id"
    assert events[1][1]["external_message_id"] == "plan_1_trigger_1"
    assert events[2] == ("bot", "started")
