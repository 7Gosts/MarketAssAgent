from __future__ import annotations

import json

from ops.migrations.migrate_jsonl_event_sequences import (
    _normalize_history_result,
    _normalize_request,
    _remap_payload_references,
)


def test_history_result_migration_keeps_messages_and_drops_nested_trace():
    old = {
        "status": "success",
        "keyword": "ETH",
        "days": 7,
        "total": 1,
        "truncated": False,
        "items": [{
            "session_id": "sess_old",
            "occurred_at": "2026-11-03T17:12:32Z",
            "user_event_id": "ev_" + "a" * 40,
            "event_ids": ["ev_" + "a" * 40],
            "user": "ETH 当时是什么情况？",
            "assistant": ["过去的回复〔mem:ev_" + "a" * 40 + "〕"],
            "tools": [{"event_id": "ev_" + "b" * 40, "tool_name": "get_journal_status"}],
        }],
        "retrieval_receipt": {"hit_event_ids": ["ev_" + "a" * 40]},
    }

    migrated = _normalize_history_result(old)

    assert migrated == {
        "status": "success",
        "keyword": "ETH",
        "truncated": False,
        "time_range": {"days": 7},
        "items": [
            {
                "occurred_at": "2026-11-03T17:12:32Z",
                "role": "user",
                "content": "ETH 当时是什么情况？",
            },
            {"role": "assistant", "content": "过去的回复"},
        ],
    }


def test_model_request_migration_preserves_user_text_but_cleans_legacy_outputs():
    event_id = "ev_" + "a" * 40
    old_history = {
        "status": "success",
        "keyword": "ETH",
        "days": 7,
        "items": [{
            "occurred_at": "2026-11-03T17:12:32Z",
            "user_event_id": event_id,
            "user": "ETH 复盘",
            "assistant": [f"回复〔mem:{event_id}〕"],
            "tools": [{"event_id": event_id}],
        }],
        "retrieval_receipt": {"hit_event_ids": [event_id]},
    }
    journal_status = {
        "status": "success",
        "session_id": "sess_old",
        "recent_events": [{"event_id": "evt_" + "b" * 24, "idea_id": "idea_old", "order_id": "ord_keep"}],
    }
    request = {
        "messages": [
            {"role": "system", "content": "keep this\n- 所有关于过去对话的陈述，必须在对应句末附工具返回的内部引用 [mem:event_id]。次数 retrieval_receipt.truncated=false\nkeep that"},
            {"role": "user", "content": f"我发来的原文 {event_id}"},
            {"role": "assistant", "content": f"答复〔mem:{event_id}〕"},
            {"role": "tool", "name": "search_session_history", "content": json.dumps(old_history)},
            {"role": "tool", "name": "get_journal_status", "content": json.dumps(journal_status)},
        ],
    }

    migrated = _normalize_request(request)

    assert migrated["messages"][0]["content"] == "keep this\nkeep that"
    assert migrated["messages"][1]["content"] == f"我发来的原文 {event_id}"
    assert migrated["messages"][2]["content"] == "答复"
    history = json.loads(migrated["messages"][3]["content"])
    ledger = json.loads(migrated["messages"][4]["content"])
    assert "retrieval_receipt" not in history
    assert "event_id" not in ledger["recent_events"][0]
    assert "idea_id" not in ledger["recent_events"][0]
    assert ledger["recent_events"][0]["order_id"] == "ord_keep"


def test_payload_references_become_integer_sequences_and_evidence_ids_are_dropped():
    payload = {
        "model_request_event_id": "ev_model",
        "turn_user_event_id": "ev_user",
        "lower_checkpoint_event_ids": ["ev_old_checkpoint"],
        "source_event_ids": ["ev_evidence"],
    }

    _remap_payload_references(payload, {
        "ev_model": 12,
        "ev_user": 4,
        "ev_old_checkpoint": 9,
        "ev_evidence": 2,
    })

    assert payload == {
        "model_request_seq": 12,
        "turn_user_seq": 4,
        "lower_checkpoint_seqs": [9],
    }
