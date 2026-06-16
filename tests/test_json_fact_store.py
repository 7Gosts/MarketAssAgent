"""JsonFactStore 单元测试。"""

from __future__ import annotations

from core.fact_store import Fact
from core.json_fact_store import JsonFactStore


def _make_store(tmp_path):
    return JsonFactStore(
        facts_path=tmp_path / "memory_facts.jsonl",
        checkpoints_path=tmp_path / "memory_checkpoints.json",
    )


def test_json_fact_store_roundtrip(tmp_path):
    store = _make_store(tmp_path)
    fact = Fact(
        thread_id="t1",
        source="test",
        type="open_position",
        payload={"price": 100},
        tags=["open_position"],
    )
    fact_id = store.write_fact(fact)

    results = store.recall("t1", {"type": "open_position"})
    assert len(results) == 1
    assert results[0].id == fact_id
    assert results[0].payload["price"] == 100


def test_json_fact_store_get_latest_fact(tmp_path):
    store = _make_store(tmp_path)
    store.write_fact(Fact(thread_id="t2", type="user_profile", payload={"v": 1}, timestamp="2026-01-01T00:00:00Z"))
    store.write_fact(Fact(thread_id="t2", type="user_profile", payload={"v": 2}, timestamp="2026-01-02T00:00:00Z"))

    latest = store.get_latest_fact("t2", "user_profile")
    assert latest is not None
    assert latest.payload["v"] == 2


def test_json_fact_store_checkpoint_roundtrip(tmp_path):
    store = _make_store(tmp_path)
    store.set_checkpoint("t3", "last_snapshot", {"symbol": "BTCUSDT"})
    assert store.get_checkpoint("t3", "last_snapshot") == {"symbol": "BTCUSDT"}
