"""Feishu 长连接适配层。"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

from adapters.feishu_adapter import FeishuAdapter
from utils.logging_utils import get_logger


_SEEN_MESSAGE_IDS: dict[str, float] = {}
_SEEN_LOCK = threading.Lock()
_MESSAGE_DEDUP_TTL_SEC = 10 * 60

_BOT_START_TS_MS = int(time.time() * 1000)
_STARTUP_GRACE_MS = 5000
logger = get_logger(__name__)


def _import_lark() -> Any:
    try:
        import lark_oapi as lark  # type: ignore
    except Exception as exc:
        raise RuntimeError("未安装 lark-oapi，请先执行 `pip install -r requirements.txt`。") from exc
    return lark


def extract_event_text(data: Any) -> str:
    content = getattr(getattr(getattr(data, "event", None), "message", None), "content", "") or ""
    if not isinstance(content, str):
        return ""
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        return content
    if isinstance(obj, dict) and isinstance(obj.get("text"), str):
        return obj["text"]
    deep = _extract_text_from_obj(obj)
    if deep:
        return deep
    return content


def extract_sender_open_id(data: Any) -> str:
    sender_id = getattr(getattr(getattr(data, "event", None), "sender", None), "sender_id", None)
    return str(getattr(sender_id, "open_id", "") or "").strip()


def extract_sender_user_id(data: Any) -> str:
    sender_id = getattr(getattr(getattr(data, "event", None), "sender", None), "sender_id", None)
    return str(getattr(sender_id, "user_id", "") or "").strip()


def extract_chat_id(data: Any) -> str:
    message = getattr(getattr(data, "event", None), "message", None)
    return str(getattr(message, "chat_id", "") or "").strip()


def extract_message_id(data: Any) -> str:
    message = getattr(getattr(data, "event", None), "message", None)
    return str(getattr(message, "message_id", "") or "").strip()


def extract_sender_type(data: Any) -> str:
    sender = getattr(getattr(data, "event", None), "sender", None)
    return str(getattr(sender, "sender_type", "") or "").strip().lower()


def extract_message_type(data: Any) -> str:
    message = getattr(getattr(data, "event", None), "message", None)
    return str(getattr(message, "message_type", "") or "").strip().lower()


def extract_message_create_time_ms(data: Any) -> int | None:
    message = getattr(getattr(data, "event", None), "message", None)
    raw = str(getattr(message, "create_time", "") or "").strip()
    if not raw:
        return None
    try:
        ts = int(raw)
    except ValueError:
        return None
    if ts < 10_000_000_000:
        ts *= 1000
    return ts


def is_stale_message(data: Any) -> bool:
    cts = extract_message_create_time_ms(data)
    if cts is None:
        return False
    return cts < (_BOT_START_TS_MS - _STARTUP_GRACE_MS)


def should_process_message(message_id: str, *, now_ts: float | None = None) -> bool:
    if not message_id:
        return True
    now = time.time() if now_ts is None else float(now_ts)
    with _SEEN_LOCK:
        expired = [mid for mid, ts in _SEEN_MESSAGE_IDS.items() if (now - ts) > _MESSAGE_DEDUP_TTL_SEC]
        for mid in expired:
            _SEEN_MESSAGE_IDS.pop(mid, None)
        if message_id in _SEEN_MESSAGE_IDS:
            return False
        _SEEN_MESSAGE_IDS[message_id] = now
        return True


def build_event_handler(adapter: FeishuAdapter) -> Any:
    lark = _import_lark()

    def _process_message(*, open_id: str, user_id: str, chat_id: str, text: str) -> None:
        try:
            asyncio.run(
                adapter.handle_longconn_message(
                    text=text,
                    open_id=open_id,
                    user_id=user_id,
                    chat_id=chat_id,
                )
            )
        except Exception as e:
            logger.exception("[FeishuLongConn] 处理消息失败: %s", e)

    def _on_message(data: Any) -> None:
        if extract_sender_type(data) != "user":
            return
        if extract_message_type(data) != "text":
            return
        if is_stale_message(data):
            return

        message_id = extract_message_id(data)
        if not should_process_message(message_id):
            return

        text = extract_event_text(data)
        if not text:
            return

        open_id = extract_sender_open_id(data)
        user_id = extract_sender_user_id(data)
        chat_id = extract_chat_id(data)

        th = threading.Thread(
            target=_process_message,
            kwargs={
                "open_id": open_id,
                "user_id": user_id,
                "chat_id": chat_id,
                "text": text,
            },
            daemon=True,
        )
        th.start()

    return (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(_on_message)
        .build()
    )


def run_feishu_longconn(adapter: FeishuAdapter, *, log_level: Any = None) -> None:
    lark = _import_lark()
    if log_level is None:
        log_level = lark.LogLevel.INFO

    app_id, app_secret = adapter.get_app_credentials()
    event_handler = build_event_handler(adapter)
    client = lark.ws.Client(app_id, app_secret, event_handler=event_handler, log_level=log_level)
    client.start()


def _extract_text_from_obj(obj: Any) -> str:
    texts: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            tag = str(node.get("tag") or "").lower()
            if tag == "at":
                return
            txt = node.get("text")
            if isinstance(txt, str) and txt.strip():
                texts.append(txt.strip())
            for value in node.values():
                _walk(value)
            return
        if isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(obj)
    return " ".join(texts).strip()
