"""Tests for Phase 5.5 D+ strategy branches: mutually_exclusive_category, inverse_temporal_order."""

from __future__ import annotations

from decimal import Decimal

from polymarket_arb.storage.base import RelationshipCandidateRow
from polymarket_arb.strategies.nesting_contradiction import (
    AlignedPricePoint,
    evaluate_relationship_at_tick,
)

_TS = 1_700_000_000_000


def _base_rel(**kwargs) -> RelationshipCandidateRow:
    defaults = dict(
        relationship_id="relid",
        market_id_a="m1",
        market_id_b="m2",
        condition_id_a="ca",
        condition_id_b="cb",
        token_id_a_yes="ya",
        token_id_a_no="na",
        token_id_b_yes="yb",
        token_id_b_no="nb",
        question_a="Q_A?",
        question_b="Q_B?",
        relationship_type="mutually_exclusive_category",
        entity_match_score=0.1,
        time_scope_match_score=0.9,
        resolution_criteria_match_score=0.8,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.8,
        model_confidence=1.0,
        final_confidence=0.8,
        validation_status="accepted",
        rejection_reasons_json="[]",
        rationale_summary="test",
        evidence_json="{}",
        rulebook_id="relationship_v2",
        rulebook_version=2,
        rulebook_content_hash="abc",
    )
    defaults.update(kwargs)
    return RelationshipCandidateRow(**defaults)


def _point(price_a: float, price_b: float) -> AlignedPricePoint:
    return AlignedPricePoint(
        ts_ms=_TS,
        price_a=Decimal(str(price_a)),
        price_b=Decimal(str(price_b)),
        price_a_ts_ms=_TS,
        price_b_ts_ms=_TS,
    )


class TestMutuallyExclusiveCategoryOverround:
    def test_overround_creates_no_a_no_b(self):
        """P(A) + P(B) > 1 should produce buy NO_A + NO_B."""
        rel = _base_rel(relationship_type="mutually_exclusive_category")
        point = _point(0.60, 0.55)  # sum = 1.15, edge = 0.15
        result = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="r1",
            min_gross_edge=0.02,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("50"),
            min_net_edge=0.01,
        )
        assert result is not None
        assert result.token_id_a == "na"   # NO_A
        assert result.token_id_b == "nb"   # NO_B
        assert result.accepted_for_simulation or result.rejection_reason

    def test_no_overround_returns_none(self):
        """P(A) + P(B) = 0.80, no overround → no signal."""
        rel = _base_rel(relationship_type="mutually_exclusive_category")
        point = _point(0.40, 0.40)  # sum = 0.80
        result = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="r1",
            min_gross_edge=0.02,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("50"),
            min_net_edge=0.01,
        )
        assert result is None

    def test_same_entity_exclusive_same_logic(self):
        """same_entity_exclusive should use the same overround logic."""
        rel = _base_rel(relationship_type="same_entity_exclusive")
        point = _point(0.60, 0.55)
        result = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="r1",
            min_gross_edge=0.02,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("50"),
            min_net_edge=0.01,
        )
        assert result is not None
        assert result.token_id_a == "na"
        assert result.token_id_b == "nb"


class TestInverseTemporalOrder:
    def test_overround_creates_no_a_no_b(self):
        """inverse_temporal_order with P(A) + P(B) > 1 → buy NO_A + NO_B."""
        rel = _base_rel(relationship_type="inverse_temporal_order")
        point = _point(0.65, 0.60)  # sum = 1.25
        result = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="r1",
            min_gross_edge=0.02,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("50"),
            min_net_edge=0.01,
        )
        assert result is not None
        assert result.token_id_a == "na"
        assert result.token_id_b == "nb"
        assert float(result.theoretical_edge) > 0.0

    def test_underround_creates_yes_a_yes_b(self):
        """inverse_temporal_order with P(A) + P(B) < 1 → buy YES_A + YES_B."""
        rel = _base_rel(relationship_type="inverse_temporal_order")
        point = _point(0.30, 0.35)  # sum = 0.65, underround
        result = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="r1",
            min_gross_edge=0.02,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("50"),
            min_net_edge=0.01,
        )
        assert result is not None
        assert result.token_id_a == "ya"
        assert result.token_id_b == "yb"

    def test_no_violation_returns_none(self):
        """inverse_temporal_order with P(A) + P(B) ≈ 1 → no signal."""
        rel = _base_rel(relationship_type="inverse_temporal_order")
        point = _point(0.50, 0.50)  # sum = 1.00, no edge
        result = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="r1",
            min_gross_edge=0.02,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("50"),
            min_net_edge=0.01,
        )
        assert result is None


class TestFeesReduceNetEdge:
    def test_fees_plus_slippage_reduce_net_edge(self):
        rel = _base_rel(relationship_type="mutually_exclusive_category")
        point = _point(0.55, 0.50)  # gross_edge = 0.05
        result_no_cost = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="r1",
            min_gross_edge=0.02,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            min_net_edge=0.0,
        )
        result_with_cost = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="r1",
            min_gross_edge=0.02,
            fee_bps=Decimal("25"),
            slippage_bps=Decimal("50"),
            min_net_edge=0.0,
        )
        assert result_no_cost is not None
        assert result_with_cost is not None
        # Net edge after costs should be lower
        assert result_with_cost.net_edge_after_costs < result_no_cost.net_edge_after_costs

    def test_high_costs_cause_rejection(self):
        rel = _base_rel(relationship_type="mutually_exclusive_category")
        point = _point(0.52, 0.50)  # small gross_edge = 0.02
        result = evaluate_relationship_at_tick(
            rel=rel, point=point, run_id="r1",
            min_gross_edge=0.01,
            fee_bps=Decimal("100"),
            slippage_bps=Decimal("200"),
            min_net_edge=0.02,
        )
        if result is not None:
            # Should be rejected due to costs
            assert not result.accepted_for_simulation
