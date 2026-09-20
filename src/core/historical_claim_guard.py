from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal


EvidenceTier = Literal["verbatim", "retrieved", "checkpoint_cited"]
_REF_RE = re.compile(r"\[mem:(ev_[0-9a-f]{40})\]")
_TEMPORAL_RE = re.compile(
    r"最近|过去|此前|之前|上次|本周|这周|一周|全程|一直|始终|永远|多次|从未|曾经|当时|以来|回顾|历史|"
    r"previously|last\s+time|recent|always|never",
    re.IGNORECASE,
)
_DIALOGUE_CLAIM_RE = re.compile(
    r"(?:你|我|我们|助手|用户).{0,24}(?:说|提|问|要求|让我|关注|看多|看空|观望|判断|建议|回复|回答|认为)|"
    r"(?:说|提|问|要求|关注|看多|看空|观望|判断|建议|回复|回答|认为).{0,24}(?:你|我|我们|助手|用户)|"
    r"(?:每次|多次|从未|一直|始终|没有一次|共\s*\d+\s*次|总共\s*\d+\s*次).{0,16}"
    r"(?:建议|回答|回复|提问|要求|判断|说)",
    re.IGNORECASE,
)
_EXHAUSTIVE_RE = re.compile(
    r"全程|一直|始终|永远|从未|每次|全部|总共|共\s*\d+\s*次|\d+\s*次|always|never",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(
    r"[^。！？!?\n]+(?:[。！？!?](?:\s*\[mem:ev_[0-9a-f]{40}\])*)?|\n"
)


@dataclass(frozen=True)
class ClaimValidation:
    text: str
    valid: bool
    needs_refetch: bool
    invalid_spans: tuple[tuple[int, int], ...]
    reasons: tuple[str, ...]


class HistoricalClaimGuard:
    """Validate model-provided memory citations without making semantic judgments."""

    def __init__(
        self,
        *,
        evidence_index: dict[str, EvidenceTier] | dict[str, str],
        retrieval_receipts: list[dict[str, Any]],
    ) -> None:
        self.evidence_index = dict(evidence_index)
        self.retrieval_receipts = [dict(item) for item in retrieval_receipts]

    def validate(self, text: str) -> ClaimValidation:
        raw = str(text or "")
        invalid_spans: list[tuple[int, int]] = []
        reasons: list[str] = []
        for match in _SENTENCE_RE.finditer(raw):
            sentence = match.group(0)
            if not _is_conversation_history_claim(sentence):
                continue
            refs = _REF_RE.findall(sentence)
            if not refs:
                invalid_spans.append(match.span())
                reasons.append("historical_claim_missing_reference")
                continue
            invalid_ref = next(
                (
                    event_id
                    for event_id in refs
                    if self.evidence_index.get(event_id) not in {"verbatim", "retrieved"}
                ),
                "",
            )
            if invalid_ref:
                invalid_spans.append(match.span())
                reasons.append("reference_not_loaded_or_not_verbatim")
                continue
            if _EXHAUSTIVE_RE.search(sentence) and not self._has_complete_receipt(sentence, refs):
                invalid_spans.append(match.span())
                reasons.append("exhaustive_claim_requires_complete_retrieval")

        return ClaimValidation(
            text=raw,
            valid=not invalid_spans,
            needs_refetch=bool(invalid_spans),
            invalid_spans=tuple(invalid_spans),
            reasons=tuple(reasons),
        )

    def _has_complete_receipt(self, sentence: str, refs: list[str]) -> bool:
        ref_set = set(refs)
        for receipt in self.retrieval_receipts:
            truncated = bool(receipt.get("truncated", not bool(receipt.get("complete", False))))
            hit_ids = set(receipt.get("hit_event_ids") or receipt.get("event_ids") or [])
            if truncated or not ref_set <= hit_ids:
                continue
            count_match = re.search(r"(?:共|总共)?\s*(\d+)\s*次", sentence)
            if count_match and int(count_match.group(1)) != int(receipt.get("total_hits") or 0):
                continue
            receipt_days = int((receipt.get("time_range") or {}).get("days") or 0)
            claimed_days = _claimed_window_days(sentence)
            if claimed_days and receipt_days < claimed_days:
                continue
            return True
        return False

    @staticmethod
    def render(text: str) -> str:
        return re.sub(r"\s*\[mem:ev_[0-9a-f]{40}\]", "", str(text or "")).strip()

    @staticmethod
    def degrade(text: str, invalid_spans: tuple[tuple[int, int], ...]) -> str:
        if not invalid_spans:
            return HistoricalClaimGuard.render(text)
        out: list[str] = []
        cursor = 0
        for start, end in sorted(invalid_spans):
            if start < cursor:
                continue
            out.append(text[cursor:start])
            out.append("当前可见记录不足以确认。")
            cursor = end
        out.append(text[cursor:])
        return HistoricalClaimGuard.render("".join(out))


def _is_conversation_history_claim(sentence: str) -> bool:
    """Do not mistake historical market facts for claims about prior dialogue."""
    return bool(_TEMPORAL_RE.search(sentence) and _DIALOGUE_CLAIM_RE.search(sentence))


def _claimed_window_days(sentence: str) -> int:
    day_match = re.search(r"(?:最近|过去)\s*(\d+)\s*天", sentence)
    if day_match:
        return int(day_match.group(1))
    week_match = re.search(r"(?:最近|过去)\s*(\d+)\s*(?:周|星期)", sentence)
    if week_match:
        return int(week_match.group(1)) * 7
    if re.search(r"最近一周|过去一周|本周|这周", sentence):
        return 7
    if re.search(r"最近一个月|过去一个月|本月", sentence):
        return 30
    return 0
