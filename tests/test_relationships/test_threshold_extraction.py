"""Tests for threshold extraction and nesting detection."""

from __future__ import annotations

from decimal import Decimal

from polymarket_arb.relationships.threshold_extraction import (
    ThresholdClaim,
    detect_threshold_nesting,
    extract_threshold_claim,
)
from polymarket_arb.storage.base import MarketSemanticsRow


def _sem(question: str, positive_condition: str = "", deadline_ms: int | None = None) -> MarketSemanticsRow:
    from datetime import datetime, timezone
    ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    return MarketSemanticsRow(
        source_market_id="test_market",
        source_condition_id=None,
        question=question,
        canonical_question=question,
        market_type="binary",
        subject_entities=["Bitcoin"],
        event_entities=["BTC"],
        temporal_phrase=None,
        temporal_phrase_normalized=None,
        temporal_resolution="date",
        exact_deadline_ms=deadline_ms,
        date_constraints_json="{}",
        jurisdiction=None,
        positive_resolution_condition=positive_condition,
        negative_resolution_condition="",
        necessary_conditions_for_yes=[],
        sufficient_conditions_for_yes=[],
        necessary_conditions_for_no=[],
        sufficient_conditions_for_no=[],
        evidence_required=[],
        ambiguity_flags=[],
        ambiguity_score=0.1,
        semantic_confidence=0.9,
        needs_manual_review=False,
        explanation_summary=None,
        flag_rationales_json=None,
        uncertainty_notes_json=None,
        rule_curation_notes_json=None,
        raw_response_hash="abc",
        model_name="test",
        prompt_version="v1",
        rulebook_id=None,
        rulebook_version=None,
        extraction_id="test_extraction",
        schema_version=1,
        ingested_ts_ms=ts,
    )


class TestExtractThresholdClaim:
    def test_btc_above_100k(self):
        sem = _sem("Will BTC exceed $100,000 by end of year?")
        claim = extract_threshold_claim(sem)
        assert claim is not None
        assert claim.variable == "btc_price"
        assert claim.comparator == ">"
        assert claim.value == Decimal("100000")

    def test_btc_above_90k(self):
        sem = _sem("Will Bitcoin reach $90k by December?")
        claim = extract_threshold_claim(sem)
        assert claim is not None
        assert claim.variable == "btc_price"
        assert claim.value == Decimal("90000")

    def test_eth_above_5k(self):
        sem = _sem("Will ETH reach $5,000 before year end?")
        claim = extract_threshold_claim(sem)
        assert claim is not None
        assert claim.variable == "eth_price"

    def test_no_threshold_for_non_numeric(self):
        sem = _sem("Will Trump win the 2024 election?")
        claim = extract_threshold_claim(sem)
        assert claim is None

    def test_below_comparator(self):
        sem = _sem("Will BTC fall below $50,000?")
        claim = extract_threshold_claim(sem)
        assert claim is not None
        assert claim.comparator == "<"
        assert claim.value == Decimal("50000")


class TestDetectThresholdNesting:
    def test_higher_implies_lower_for_gt(self):
        a = ThresholdClaim(variable="btc_price", comparator=">", value=Decimal("100000"),
                          unit="usd", deadline_ms=1700000000000, raw_phrase="exceeds $100,000")
        b = ThresholdClaim(variable="btc_price", comparator=">", value=Decimal("90000"),
                          unit="usd", deadline_ms=1700000000000, raw_phrase="exceeds $90,000")
        direction = detect_threshold_nesting(a, b)
        assert direction == "a_implies_b"

    def test_lower_implies_higher_for_gt(self):
        a = ThresholdClaim(variable="btc_price", comparator=">", value=Decimal("90000"),
                          unit="usd", deadline_ms=1700000000000, raw_phrase="exceeds $90,000")
        b = ThresholdClaim(variable="btc_price", comparator=">", value=Decimal("100000"),
                          unit="usd", deadline_ms=1700000000000, raw_phrase="exceeds $100,000")
        direction = detect_threshold_nesting(a, b)
        assert direction == "b_implies_a"

    def test_different_variable_no_nesting(self):
        a = ThresholdClaim(variable="btc_price", comparator=">", value=Decimal("100000"),
                          unit="usd", deadline_ms=None, raw_phrase="BTC > 100k")
        b = ThresholdClaim(variable="eth_price", comparator=">", value=Decimal("5000"),
                          unit="usd", deadline_ms=None, raw_phrase="ETH > 5k")
        direction = detect_threshold_nesting(a, b)
        assert direction == "none"

    def test_mismatched_comparators_no_nesting(self):
        a = ThresholdClaim(variable="btc_price", comparator=">", value=Decimal("100000"),
                          unit="usd", deadline_ms=None, raw_phrase=">100k")
        b = ThresholdClaim(variable="btc_price", comparator="<", value=Decimal("90000"),
                          unit="usd", deadline_ms=None, raw_phrase="<90k")
        direction = detect_threshold_nesting(a, b)
        assert direction == "none"

    def test_too_far_apart_deadlines(self):
        a = ThresholdClaim(variable="btc_price", comparator=">", value=Decimal("100000"),
                          unit="usd", deadline_ms=1700000000000, raw_phrase=">100k")
        b = ThresholdClaim(variable="btc_price", comparator=">", value=Decimal("90000"),
                          unit="usd", deadline_ms=1700000000000 + 30 * 24 * 3600 * 1000,
                          raw_phrase=">90k")
        direction = detect_threshold_nesting(a, b)
        assert direction == "none"
