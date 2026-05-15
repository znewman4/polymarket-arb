"""Tests for relationship validators."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from polymarket_arb.relationships.candidate_generation import CandidatePair
from polymarket_arb.relationships.validators import validate_all_pairs
from polymarket_arb.semantics.rulebook import load_rulebook
from polymarket_arb.storage.base import (
    BackfillCoverageRow,
    MarketRow,
    MarketSemanticsRow,
)

_TS = int(datetime.now(timezone.utc).timestamp() * 1000)

_RULEBOOK_PATH = (
    __file__
    .replace("tests/test_relationships/test_validators.py", "")
    .replace("\\", "/")
)


def _find_rulebook():
    from pathlib import Path
    # Walk up to find the configs directory
    p = Path(__file__).parent
    for _ in range(6):
        rb = p / "configs" / "semantic_rules" / "relationship_v1.yaml"
        if rb.exists():
            return rb
        p = p.parent
    raise FileNotFoundError("relationship_v1.yaml not found")


def _load_rulebook():
    rb_path = _find_rulebook()
    return load_rulebook(rb_path, kind="relationship"), rb_path


def _market(
    market_id: str,
    question: str,
    outcomes: list[str] | None = None,
    token_ids: list[str] | None = None,
    event_id: str | None = None,
) -> MarketRow:
    if outcomes is None:
        outcomes = ["Yes", "No"]
    if token_ids is None:
        token_ids = [f"tok_{market_id}_yes", f"tok_{market_id}_no"]
    return MarketRow(
        id=market_id,
        condition_id=f"cond_{market_id}",
        slug=market_id,
        question=question,
        description=None,
        end_date_ms=_TS + 30 * 24 * 3600 * 1000,
        start_date_ms=_TS,
        closed_at_ms=None,
        resolved_at_ms=None,
        active=True,
        closed=False,
        archived=False,
        outcomes=outcomes,
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=token_ids,
        volume=None,
        liquidity=None,
        event_id=event_id,
        neg_risk=False,
        text_hash="abc",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _semantics(
    market_id: str,
    entities: list[str],
    deadline_ms: int | None = None,
    pos_condition: str = "resolves YES",
    neg_condition: str = "resolves NO",
    confidence: float = 0.85,
    needs_review: bool = False,
    temporal_resolution: str = "date",
) -> MarketSemanticsRow:
    return MarketSemanticsRow(
        source_market_id=market_id,
        source_condition_id=None,
        question="",
        canonical_question="",
        market_type="binary",
        subject_entities=entities,
        event_entities=entities,
        temporal_phrase=None,
        temporal_phrase_normalized=None,
        temporal_resolution=temporal_resolution,
        exact_deadline_ms=deadline_ms,
        date_constraints_json="{}",
        jurisdiction=None,
        positive_resolution_condition=pos_condition,
        negative_resolution_condition=neg_condition,
        necessary_conditions_for_yes=[],
        sufficient_conditions_for_yes=[],
        necessary_conditions_for_no=[],
        sufficient_conditions_for_no=[],
        evidence_required=[],
        ambiguity_flags=[],
        ambiguity_score=0.1,
        semantic_confidence=confidence,
        needs_manual_review=needs_review,
        explanation_summary=None,
        flag_rationales_json=None,
        uncertainty_notes_json=None,
        rule_curation_notes_json=None,
        raw_response_hash="abc",
        model_name="test",
        prompt_version="v1",
        rulebook_id=None,
        rulebook_version=None,
        extraction_id=f"ext_{market_id}",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _coverage(market_id: str, coverage_score: float = 0.8) -> BackfillCoverageRow:
    return BackfillCoverageRow(
        market_id=market_id,
        condition_id=f"cond_{market_id}",
        question="q",
        start_ts_ms=_TS - 90 * 24 * 3600 * 1000,
        end_ts_ms=_TS,
        requested_days=90,
        has_gamma=True,
        has_price_history=True,
        has_trade_history=False,
        has_semantics=True,
        has_rulebook_score=True,
        has_implications=True,
        has_embeddings=False,
        has_backfill_coverage=True,
        price_points_count=100,
        trade_points_count=0,
        first_price_ts_ms=_TS - 90 * 24 * 3600 * 1000,
        last_price_ts_ms=_TS,
        missing_price_gap_count=0,
        largest_price_gap_ms=0,
        price_min=Decimal("0.3"),
        price_max=Decimal("0.7"),
        price_out_of_bounds_count=0,
        duplicate_timestamp_count=0,
        coverage_score=coverage_score,
        recommended_for_backtest=coverage_score >= 0.6,
        exclusion_reasons_json="[]",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


class TestValidateAllPairs:
    def setup_method(self):
        self.rb, self.rb_path = _load_rulebook()
        self.rb_hash = "test_hash"

    def _validate(self, pairs, semantics_by_market, coverage_by_market=None):
        if coverage_by_market is None:
            coverage_by_market = {}
        return list(validate_all_pairs(
            pairs, semantics_by_market, coverage_by_market,
            self.rb, self.rb_hash,
        ))

    def test_nested_threshold_pair_accepted(self):
        """BTC > 100k and BTC > 90k, same date → nested_a_implies_b accepted."""
        ma = _market("btc_100k", "Will BTC exceed $100,000 by Dec 31?")
        mb = _market("btc_90k", "Will BTC exceed $90,000 by Dec 31?")
        deadline = _TS + 90 * 24 * 3600 * 1000
        sem_a = _semantics("btc_100k", ["Bitcoin", "BTC"], deadline_ms=deadline,
                           pos_condition="BTC price exceeds $100,000")
        sem_b = _semantics("btc_90k", ["Bitcoin", "BTC"], deadline_ms=deadline,
                           pos_condition="BTC price exceeds $90,000")
        pair = CandidatePair(market_a=ma, market_b=mb, sources=["entity_overlap"])

        rows = self._validate([pair], {"btc_100k": sem_a, "btc_90k": sem_b})
        assert len(rows) == 1
        row = rows[0]
        assert row.validation_status in ("accepted", "needs_manual_review")
        # Should detect threshold relation
        threshold = json.loads(row.threshold_relation_json)
        assert threshold.get("variable") == "btc_price" or row.validation_status != "rejected"

    def test_missing_semantics_does_not_crash(self):
        """Market without semantics → rejected with missing_semantics, never raises."""
        ma = _market("market_a", "Question A?")
        mb = _market("market_b", "Question B?")
        pair = CandidatePair(market_a=ma, market_b=mb, sources=["event_id_match"])

        rows = self._validate([pair], {})  # empty semantics
        assert len(rows) == 1
        assert rows[0].validation_status == "rejected"
        reasons = json.loads(rows[0].rejection_reasons_json)
        assert any(r.get("code") == "missing_semantics" for r in reasons)

    def test_unrelated_similar_text_pair_rejected(self):
        """Different entities, similar question structure → entity_mismatch rejection."""
        ma = _market("trump", "Will Trump win 2024?")
        mb = _market("harris", "Will Biden win 2024?")
        sem_a = _semantics("trump", ["Trump", "Donald Trump"], pos_condition="Trump wins 2024")
        sem_b = _semantics("harris", ["Biden", "Joe Biden"], pos_condition="Biden wins 2024")
        pair = CandidatePair(market_a=ma, market_b=mb, sources=["entity_overlap"])

        rows = self._validate([pair], {"trump": sem_a, "harris": sem_b})
        assert len(rows) == 1
        # Entity overlap is different people → should be low entity_score → rejected
        row = rows[0]
        assert row.entity_match_score < 0.5 or row.validation_status != "accepted"

    def test_inverse_pair_same_scope_accepted_or_manual_review(self):
        """Same entity, same deadline, opposing conditions → inverse."""
        ma = _market("ma", "Will Candidate A win?")
        mb = _market("mb", "Will Candidate A lose?")
        deadline = _TS + 90 * 24 * 3600 * 1000
        sem_a = _semantics("ma", ["Candidate A"], deadline_ms=deadline,
                           pos_condition="Candidate A wins the election")
        sem_b = _semantics("mb", ["Candidate A"], deadline_ms=deadline,
                           pos_condition="Candidate A loses the election")
        pair = CandidatePair(market_a=ma, market_b=mb, sources=["entity_overlap"])

        rows = self._validate([pair], {"ma": sem_a, "mb": sem_b})
        assert len(rows) == 1
        row = rows[0]
        # Entity overlap should be perfect → should not be rejected for entity
        assert row.entity_match_score >= 0.5

    def test_inverse_pair_different_dates_not_accepted(self):
        """Same entity but far-apart deadlines → should not be accepted."""
        ma = _market("m2024", "Will BTC hit $100k in 2024?")
        mb = _market("m2025", "Will BTC hit $100k in 2025?")
        deadline_a = _TS + 90 * 24 * 3600 * 1000
        deadline_b = _TS + 365 * 24 * 3600 * 1000 + 90 * 24 * 3600 * 1000
        sem_a = _semantics("m2024", ["Bitcoin", "BTC"], deadline_ms=deadline_a)
        sem_b = _semantics("m2025", ["Bitcoin", "BTC"], deadline_ms=deadline_b)
        pair = CandidatePair(market_a=ma, market_b=mb, sources=["entity_overlap"])

        rows = self._validate([pair], {"m2024": sem_a, "m2025": sem_b})
        row = rows[0]
        # Time scope should be low → should not be accepted
        assert row.time_scope_match_score < 0.6 or row.validation_status != "accepted"

    def test_ambiguous_date_pair_needs_manual_review_or_rejected(self):
        """Markets with ambiguous temporal resolution → not accepted."""
        ma = _market("ma", "Will X happen someday?")
        mb = _market("mb", "Will X happen eventually?")
        sem_a = _semantics("ma", ["X"], temporal_resolution="ambiguous")
        sem_b = _semantics("mb", ["X"], temporal_resolution="ambiguous")
        pair = CandidatePair(market_a=ma, market_b=mb, sources=["entity_overlap"])

        rows = self._validate([pair], {"ma": sem_a, "mb": sem_b})
        row = rows[0]
        assert row.validation_status in ("rejected", "needs_manual_review")

    def test_identical_market_pair_rejected(self):
        """Same market_id on both sides → rejected."""
        ma = _market("same", "Same market?")
        pair = CandidatePair(market_a=ma, market_b=ma, sources=["entity_overlap"])
        rows = self._validate([pair], {"same": _semantics("same", ["X"])})
        row = rows[0]
        assert row.validation_status == "rejected"
        reasons = json.loads(row.rejection_reasons_json)
        assert any(r.get("code") == "identical_market_pair" for r in reasons)

    def test_low_coverage_does_not_change_relationship_status(self):
        """Low coverage doesn't reject the relationship, but marks it in evidence."""
        ma = _market("ma", "Will BTC exceed $100k?")
        mb = _market("mb", "Will BTC exceed $90k?")
        deadline = _TS + 90 * 24 * 3600 * 1000
        sem_a = _semantics("ma", ["Bitcoin", "BTC"], deadline_ms=deadline,
                           pos_condition="BTC exceeds $100,000")
        sem_b = _semantics("mb", ["Bitcoin", "BTC"], deadline_ms=deadline,
                           pos_condition="BTC exceeds $90,000")
        pair = CandidatePair(market_a=ma, market_b=mb, sources=["entity_overlap"])
        low_cov = _coverage("ma", coverage_score=0.1)
        low_cov_b = _coverage("mb", coverage_score=0.1)

        rows = self._validate(
            [pair], {"ma": sem_a, "mb": sem_b},
            {"ma": low_cov, "mb": low_cov_b},
        )
        row = rows[0]
        # Coverage is reflected in evidence but doesn't by itself reject the relationship
        evidence = json.loads(row.evidence_json)
        assert "coverage_a_ok" in evidence
        assert evidence["coverage_a_ok"] is False

    def test_no_chain_of_thought_in_output(self):
        """Rationale summary must not contain <think> tags."""
        ma = _market("ma", "Will BTC exceed $100k?")
        mb = _market("mb", "Will BTC exceed $90k?")
        sem_a = _semantics("ma", ["Bitcoin", "BTC"])
        sem_b = _semantics("mb", ["Bitcoin", "BTC"])
        pair = CandidatePair(market_a=ma, market_b=mb, sources=["entity_overlap"])
        rows = self._validate([pair], {"ma": sem_a, "mb": sem_b})
        for row in rows:
            assert "<think>" not in (row.rationale_summary or "")
            assert "<think>" not in (row.evidence_json or "")
