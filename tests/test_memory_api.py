from __future__ import annotations

from core.fact_store import Fact
from core.json_fact_store import JsonFactStore
from core.memory_api import DefaultMemoryAPI


def _make_store(tmp_path):
    return JsonFactStore(
        facts_path=tmp_path / "memory_facts.jsonl",
        checkpoints_path=tmp_path / "memory_checkpoints.json",
    )


def test_fact_written_and_recalled(tmp_path):
    store = _make_store(tmp_path)
    memory_api = DefaultMemoryAPI(store=store)
    thread_id = "thread_test_1"

    fact = Fact(
        thread_id=thread_id,
        source="tool_x",
        type="open_position",
        payload={"price": 888},
        tags=["open_position", "price"],
        provenance={"tool_call_id": "tc_1", "request_id": "req_1"},
    )
    fact_id = memory_api.write_fact(thread_id, fact)

    results = memory_api.recall(thread_id, {"type": "open_position"})
    assert any(item.id == fact_id for item in results)
    assert results[0].payload.get("price") == 888


def test_checkpoint_roundtrip_and_snapshot(tmp_path):
    store = _make_store(tmp_path)
    memory_api = DefaultMemoryAPI(store=store)
    thread_id = "thread_test_2"
    snapshot = {"symbol": "AU0", "entry": 888}

    memory_api.checkpoint(thread_id, "last_snapshot", snapshot)

    read = memory_api.get_checkpoint(thread_id, "last_snapshot")
    assert read == snapshot
    assert memory_api.snapshot(thread_id) == snapshot
