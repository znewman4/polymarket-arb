"""Tests for the pair economic viability pre-filter."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from polymarket_arb.backtest.context_aware_replay import (
    _pair_is_economically_viable,
    run_context_aware_backtest,
)
from polymarket_arb.storage.base import (
    BackfillCoverageRow,
    ContextRelationshipDecisionRow,
    PriceHistoryRow,
    RelationshipCandidateRow,
)
from polymarket_arb.storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from polymarket_arb.storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from polymarket_arb.storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)
from polymarket_arb.strategies.models import ContextAwareBacktestConfig

TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _price_row(token_id: str, price: str, market_id: str = "m") -> PriceHistoryRow:
    return PriceHistoryRow(
        market_id=market_id, condition_id="c", token_id=token_id, outcome="Yes",
        ts_ms=TS, price=Decimal(price), source="test",
        fidelity="hourly", interval="1h", schema_version=1, ingested_ts_ms=TS,
    )


def _rel(rel_type: str = "mutually_exclusive_category") -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id="rel_viab", market_id_a="market_a", market_id_b="market_b",
        condition_id_a="cond_a", condition_id_b="cond_b",
        token_id_a_yes="a_yes", token_id_a_no="a_no",
        token_id_b_yes="b_yes", token_id_b_no="b_no",
        question_a="Will A win nomination?", question_b="Will B win nomination?",
        relationship_type=rel_type,
        entity_match_score=1.0, time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}", semantic_similarity_score=None,
        deterministic_confidence=0.8, model_confidence=0.8, final_confidence=0.8,
        validation_status="accepted", rejection_reasons_json="[]",
        rationale_summary="test", evidence_json="{}",
        rulebook_id="v2", rulebook_version=2, rulebook_content_hash="hash",
        strategy_eligibility_status="eligible",
        schema_version=1, ingested_ts_ms=TS,
    )


class TestPairViabilityHelper:
    def test_long_shot_pair_filtered(self) -> None:
        rows_a = [_price_row("a", "0.015")]
        rows_b = [_price_row("b", "0.010")]
        rel = _rel("mutually_exclusive_category")
        assert not _pair_is_economically_viable(
            rel, rows_a, rows_b, min_combined=0.40, min_single=0.0
        )

    def test_meaningful_pair_passes(self) -> None:
        rows_a = [_price_row("a", "0.35")]
        rows_b = [_price_row("b", "0.30")]
        rel = _rel("mutually_exclusive_category")
        assert _pair_is_economically_viable(
            rel, rows_a, rows_b, min_combined=0.40, min_single=0.0
        )

    def test_single_prob_threshold_rescues_pair(self) -> None:
        rows_a = [_price_row("a", "0.20")]
        rows_b = [_price_row("b", "0.01")]
        rel = _rel("mutually_exclusive_category")
        assert _pair_is_economically_viable(
            rel, rows_a, rows_b, min_combined=0.40, min_single=0.15
        )

    def test_nesting_type_always_passes(self) -> None:
        rows_a = [_price_row("a", "0.01")]
        rows_b = [_price_row("b", "0.01")]
        rel = _rel("nested_a_implies_b")
        assert _pair_is_economically_viable(
            rel, rows_a, rows_b, min_combined=0.99, min_single=0.99
        )

    def test_filter_disabled_at_zero(self) -> None:
        rows_a = [_price_row("a", "0.001")]
        rows_b = [_price_row("b", "0.001")]
        rel = _rel("mutually_exclusive_category")
        assert _pair_is_economically_viable(
            rel, rows_a, rows_b, min_combined=0.0, min_single=0.0
        )


class TestContextAwareBacktestPairViabilityIntegration:
    def test_funnel_records_pair_viability_reject(self, tmp_data_root: Path) -> None:
        rel = _rel("mutually_exclusive_category")
        ParquetRelationshipCandidatesRepository(tmp_data_root).append(rel)
        ParquetContextRelationshipDecisionsRepository(tmp_data_root).append(
            ContextRelationshipDecisionRow(
                decision_id="d1", relationship_id="rel_viab",
                context_space_id="space_01", context_rule_ids_json='["r1"]',
                previous_validation_status="accepted", new_validation_status="accepted",
                previous_strategy_eligibility="eligible", new_strategy_eligibility="eligible",
                strategy_lane="exploratory_context_auto_approved",
                decision_reason="auto", evidence_summary="",
                schema_version=1, ingested_ts_ms=TS,
            )
        )
        ParquetBackfillCoverageRepository(tmp_data_root).append_many([
            BackfillCoverageRow(
                market_id="market_a", condition_id="c", question="q",
                start_ts_ms=TS, end_ts_ms=TS + 3_600_000, requested_days=1,
                has_gamma=True, has_price_history=True, has_trade_history=False,
                has_semantics=True, has_rulebook_score=True, has_implications=True,
                has_embeddings=False, has_backfill_coverage=True,
                price_points_count=2, trade_points_count=0,
                first_price_ts_ms=TS, last_price_ts_ms=TS + 3_600_000,
                missing_price_gap_count=0, largest_price_gap_ms=0,
                price_min=Decimal("0.01"), price_max=Decimal("0.02"),
                price_out_of_bounds_count=0, duplicate_timestamp_count=0,
                coverage_score=1.0, recommended_for_backtest=True,
                exclusion_reasons_json="[]", schema_version=1, ingested_ts_ms=TS,
            ),
            BackfillCoverageRow(
                market_id="market_b", condition_id="c", question="q",
                start_ts_ms=TS, end_ts_ms=TS + 3_600_000, requested_days=1,
                has_gamma=True, has_price_history=True, has_trade_history=False,
                has_semantics=True, has_rulebook_score=True, has_implications=True,
                has_embeddings=False, has_backfill_coverage=True,
                price_points_count=2, trade_points_count=0,
                first_price_ts_ms=TS, last_price_ts_ms=TS + 3_600_000,
                missing_price_gap_count=0, largest_price_gap_ms=0,
                price_min=Decimal("0.01"), price_max=Decimal("0.02"),
                price_out_of_bounds_count=0, duplicate_timestamp_count=0,
                coverage_score=1.0, recommended_for_backtest=True,
                exclusion_reasons_json="[]", schema_version=1, ingested_ts_ms=TS,
            ),
        ])
        # Tiny prices: P(A)+P(B)=0.025, well below 0.40 threshold
        ParquetPriceHistoryRepository(tmp_data_root).append_many([
            _price_row("a_yes", "0.015", "market_a"),
            _price_row("b_yes", "0.010", "market_b"),
        ])

        result = run_context_aware_backtest(
            tmp_data_root,
            ContextAwareBacktestConfig(
                run_id="viab_test",
                lane="all_context_research",
                include_auto_approved=True,
                relationship_universe="all_with_context_decisions",
                min_combined_prob_for_pairwise=0.40,
                slippage_bps=Decimal("0"),
                fee_bps=Decimal("0"),
            ),
        )
        assert result["funnel"]["counts"]["rejected_by_pair_viability"] >= 1
        assert result["metrics"]["trades_executed"] == 0
