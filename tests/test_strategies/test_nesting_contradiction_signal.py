"""Tests for nesting/contradiction/inverse signal evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from polymarket_arb.storage.base import RelationshipCandidateRow
from polymarket_arb.strategies.nesting_contradiction import (
    AlignedPricePoint,
    evaluate_relationship_at_tick,
)

_TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _relationship(
    rel_type: str = "nested_a_implies_b",
    token_a_yes: str = "tok_a_yes",
    token_a_no: str = "tok_a_no",
    token_b_yes: str = "tok_b_yes",
    token_b_no: str = "tok_b_no",
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id="rel_001",
        market_id_a="market_a",
        market_id_b="market_b",
        condition_id_a="cond_a",
        condition_id_b="cond_b",
        token_id_a_yes=token_a_yes,
        token_id_a_no=token_a_no,
        token_id_b_yes=token_b_yes,
        token_id_b_no=token_b_no,
        question_a="Will BTC exceed $100k?",
        question_b="Will BTC exceed $90k?",
        relationship_type=rel_type,
        entity_match_score=0.9,
        time_scope_match_score=0.9,
        resolution_criteria_match_score=0.7,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.75,
        model_confidence=1.0,
        final_confidence=0.75,
        validation_status="accepted",
        rejection_reasons_json="[]",
        rationale_summary="Test relationship",
        evidence_json="{}",
        rulebook_id="relationship_v1",
        rulebook_version=1,
        rulebook_content_hash="abc",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _point(price_a: float, price_b: float) -> AlignedPricePoint:
    return AlignedPricePoint(
        ts_ms=_TS,
        price_a=Decimal(str(price_a)),
        price_b=Decimal(str(price_b)),
        price_a_ts_ms=_TS - 100,
        price_b_ts_ms=_TS - 100,
    )


class TestNestingSignal:
    def test_violation_generates_candidate(self):
        """P(A) > P(B) + min_edge → accepted candidate."""
        rel = _relationship("nested_a_implies_b")
        point = _point(price_a=0.7, price_b=0.4)  # P(A) - P(B) = 0.3

        candidate = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="test_run",
            min_gross_edge=0.10, fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
            min_net_edge=0.05,
        )
        assert candidate is not None
        assert candidate.accepted_for_simulation is True
        # Buy NO_A and YES_B
        assert candidate.token_id_a == "tok_a_no"
        assert candidate.token_id_b == "tok_b_yes"

    def test_no_violation_returns_none(self):
        """P(A) < P(B) → no violation → returns None."""
        rel = _relationship("nested_a_implies_b")
        point = _point(price_a=0.4, price_b=0.7)  # no violation

        candidate = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="test_run",
            min_gross_edge=0.10, fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
            min_net_edge=0.05,
        )
        assert candidate is None

    def test_fees_reduce_net_edge(self):
        """Higher fee_bps → lower net_edge_after_costs."""
        rel = _relationship("nested_a_implies_b")
        point = _point(price_a=0.7, price_b=0.4)

        c0 = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="run",
            min_gross_edge=0.01, fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
            min_net_edge=0.0,
        )
        c1 = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="run",
            min_gross_edge=0.01, fee_bps=Decimal("200"), slippage_bps=Decimal("0"),
            min_net_edge=0.0,
        )
        assert c0 is not None and c1 is not None
        assert c0.net_edge_after_costs > c1.net_edge_after_costs

    def test_slippage_reduces_net_edge(self):
        """Higher slippage_bps → lower net_edge."""
        rel = _relationship("nested_a_implies_b")
        point = _point(price_a=0.7, price_b=0.4)

        c0 = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="run",
            min_gross_edge=0.01, fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
            min_net_edge=0.0,
        )
        c1 = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="run",
            min_gross_edge=0.01, fee_bps=Decimal("0"), slippage_bps=Decimal("200"),
            min_net_edge=0.0,
        )
        assert c0 is not None and c1 is not None
        assert c0.net_edge_after_costs > c1.net_edge_after_costs

    def test_fees_applied_before_acceptance(self):
        """If net_edge after costs < min_net_edge → not accepted."""
        rel = _relationship("nested_a_implies_b")
        point = _point(price_a=0.55, price_b=0.5)  # tiny gross edge of 0.05

        c = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="run",
            min_gross_edge=0.02,
            fee_bps=Decimal("200"),
            slippage_bps=Decimal("200"),
            min_net_edge=0.08,  # higher than what remains after costs
        )
        assert c is not None
        assert c.accepted_for_simulation is False


class TestContradictionSignal:
    def test_contradiction_violation(self):
        """P(A) + P(B) > 1 + edge → contradiction accepted."""
        rel = _relationship("contradiction")
        point = _point(price_a=0.65, price_b=0.65)  # sum = 1.3

        c = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="run",
            min_gross_edge=0.10, fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
            min_net_edge=0.05,
        )
        assert c is not None
        assert c.accepted_for_simulation is True
        # Buy NO_A and NO_B
        assert c.token_id_a == "tok_a_no"
        assert c.token_id_b == "tok_b_no"

    def test_no_contradiction_when_sum_under_one(self):
        """P(A) + P(B) = 0.9 → no contradiction."""
        rel = _relationship("contradiction")
        point = _point(price_a=0.45, price_b=0.45)

        c = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="run",
            min_gross_edge=0.10, fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
            min_net_edge=0.05,
        )
        assert c is None


class TestInverseSignal:
    def test_inverse_overround(self):
        """P(A) + P(B) > 1 + edge → buy NO_A + NO_B."""
        rel = _relationship("inverse")
        point = _point(price_a=0.60, price_b=0.60)  # sum = 1.2

        c = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="run",
            min_gross_edge=0.05, fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
            min_net_edge=0.02,
        )
        assert c is not None
        assert c.accepted_for_simulation is True
        assert c.token_id_a == "tok_a_no"
        assert c.token_id_b == "tok_b_no"

    def test_inverse_underround(self):
        """P(A) + P(B) < 1 - edge → buy YES_A + YES_B."""
        rel = _relationship("inverse")
        point = _point(price_a=0.35, price_b=0.35)  # sum = 0.7

        c = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="run",
            min_gross_edge=0.05, fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
            min_net_edge=0.02,
        )
        assert c is not None
        assert c.accepted_for_simulation is True
        assert c.token_id_a == "tok_a_yes"
        assert c.token_id_b == "tok_b_yes"


class TestUnsupportedStructure:
    def test_missing_token_ids_rejected(self):
        """If token IDs are None → unsupported_trade_structure."""
        rel = _relationship("nested_a_implies_b", token_a_no=None)
        point = _point(price_a=0.7, price_b=0.4)

        c = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="run",
            min_gross_edge=0.01, fee_bps=Decimal("0"), slippage_bps=Decimal("0"),
            min_net_edge=0.0,
        )
        assert c is not None
        assert c.accepted_for_simulation is False
        assert c.rejection_reason == "unsupported_trade_structure"
