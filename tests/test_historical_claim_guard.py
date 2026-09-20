from __future__ import annotations

from core.historical_claim_guard import HistoricalClaimGuard


def test_guard_accepts_cited_historical_claim_from_complete_retrieval():
    event_id = "ev_" + "a" * 40
    guard = HistoricalClaimGuard(
        evidence_index={event_id: "retrieved"},
        retrieval_receipts=[{
            "hit_event_ids": [event_id],
            "total_hits": 3,
            "truncated": False,
            "time_range": {"days": 7},
        }],
    )

    result = guard.validate(f"最近一周你多次要求寻找 ETH 多头机会。[mem:{event_id}]")

    assert result.valid is True
    assert guard.render(result.text) == "最近一周你多次要求寻找 ETH 多头机会。"


def test_guard_rejects_forged_and_checkpoint_only_references():
    checkpoint_source = "ev_" + "b" * 40
    forged = "ev_" + "c" * 40
    guard = HistoricalClaimGuard(
        evidence_index={checkpoint_source: "checkpoint_cited"},
        retrieval_receipts=[],
    )

    result = guard.validate(
        f"你之前一直看多 ETH。[mem:{checkpoint_source}] [mem:{forged}]"
    )

    assert result.valid is False
    assert result.needs_refetch is True


def test_guard_rejects_count_claim_when_retrieval_was_truncated():
    event_id = "ev_" + "d" * 40
    guard = HistoricalClaimGuard(
        evidence_index={event_id: "retrieved"},
        retrieval_receipts=[{
            "hit_event_ids": [event_id],
            "total_hits": 20,
            "truncated": True,
            "time_range": {"days": 7},
        }],
    )

    result = guard.validate(f"最近一周你共提了 3 次。[mem:{event_id}]")

    assert result.valid is False
    assert result.needs_refetch is True
    assert "当前可见记录不足以确认" in guard.degrade(result.text, result.invalid_spans)


def test_guard_does_not_treat_market_history_as_conversation_memory():
    guard = HistoricalClaimGuard(evidence_index={}, retrieval_receipts=[])

    result = guard.validate("最近一周 ETH 上涨了 8.3%，当前仍在压力位附近。")

    assert result.valid is True


def test_guard_still_requires_evidence_for_prior_advice():
    guard = HistoricalClaimGuard(evidence_index={}, retrieval_receipts=[])

    result = guard.validate("最近一周我给 ETH 的判断一直偏多。")

    assert result.valid is False
    assert result.needs_refetch is True


def test_guard_rejects_exact_count_that_disagrees_with_receipt():
    event_id = "ev_" + "e" * 40
    guard = HistoricalClaimGuard(
        evidence_index={event_id: "retrieved"},
        retrieval_receipts=[{
            "hit_event_ids": [event_id],
            "total_hits": 4,
            "truncated": False,
            "time_range": {"days": 7},
        }],
    )

    result = guard.validate(f"最近一周你共提了 3 次。[mem:{event_id}]")

    assert result.valid is False


def test_guard_requires_receipt_to_cover_claimed_window():
    event_id = "ev_" + "f" * 40
    guard = HistoricalClaimGuard(
        evidence_index={event_id: "retrieved"},
        retrieval_receipts=[{
            "hit_event_ids": [event_id],
            "total_hits": 1,
            "truncated": False,
            "time_range": {"days": 7},
        }],
    )

    result = guard.validate(f"过去 30 天你一直让我找多头机会。[mem:{event_id}]")

    assert result.valid is False


def test_guard_catches_all_the_time_and_always_wording_from_regression_case():
    guard = HistoricalClaimGuard(evidence_index={}, retrieval_receipts=[])

    result = guard.validate("全程我判断多是对的，你永远都是观望。")

    assert result.valid is False
