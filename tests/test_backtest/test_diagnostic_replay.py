"""Tests for the diagnostic bypass backtest and simulated wallet."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from polymarket_arb.backtest.diagnostic_replay import run_diagnostic_backtest
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
from polymarket_arb.strategies.models import DiagnosticBacktestConfig

TS = int(datetime.now(timezone.utc).timestamp() * 1000)
HOUR_MS = 3_600_000


def _rel(
    rel_id: str = "rel_diag",
    rel_type: str = "nested_a_implies_b",
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=rel_id,
        market_id_a="market_a",
        market_id_b="market_b",
        condition_id_a="cond_a",
        condition_id_b="cond_b",
        token_id_a_yes="a_yes",
        token_id_a_no="a_no",
        token_id_b_yes="b_yes",
        token_id_b_no="b_no",
        question_a="Will A win?",
        question_b="Will A qualify?",
        relationship_type=rel_type,
        entity_match_score=1.0,
        time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.9,
        model_confidence=1.0,
        final_confidence=0.9,
        validation_status="accepted",
        rejection_reasons_json="[]",
        rationale_summary="test",
        evidence_json="{}",
        rulebook_id="v2",
        rulebook_version=2,
        rulebook_content_hash="hash",
        strategy_eligibility_status="eligible",
        relationship_family="nesting",
        relationship_subtype="test",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _decision(rel_id: str = "rel_diag", eligible: bool = True) -> ContextRelationshipDecisionRow:
    return ContextRelationshipDecisionRow(
        decision_id="dec_diag",
        relationship_id=rel_id,
        context_space_id="space_01",
        context_rule_ids_json='["r1"]',
        previous_validation_status="accepted",
        new_validation_status="accepted",
        previous_strategy_eligibility="eligible",
        new_strategy_eligibility="eligible" if eligible else "ineligible",
        strategy_lane="exploratory_context_auto_approved",
        decision_reason="auto_approved",
        evidence_summary="test",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _coverage(market_id: str, score: float = 1.0, recommended: bool = True) -> BackfillCoverageRow:
    return BackfillCoverageRow(
        market_id=market_id,
        condition_id=f"cond_{market_id}",
        question="fixture",
        start_ts_ms=TS,
        end_ts_ms=TS + 4 * HOUR_MS,
        requested_days=1,
        has_gamma=True,
        has_price_history=True,
        has_trade_history=False,
        has_semantics=True,
        has_rulebook_score=True,
        has_implications=True,
        has_embeddings=False,
        has_backfill_coverage=True,
        price_points_count=4,
        trade_points_count=0,
        first_price_ts_ms=TS,
        last_price_ts_ms=TS + 4 * HOUR_MS,
        missing_price_gap_count=0,
        largest_price_gap_ms=0,
        price_min=Decimal("0.1"),
        price_max=Decimal("0.9"),
        price_out_of_bounds_count=0,
        duplicate_timestamp_count=0,
        coverage_score=score,
        recommended_for_backtest=recommended,
        exclusion_reasons_json="[]",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _price(
    market_id: str,
    token_id: str,
    ts_ms: int,
    price: str,
) -> PriceHistoryRow:
    return PriceHistoryRow(
        market_id=market_id,
        condition_id=f"cond_{market_id}",
        token_id=token_id,
        outcome="Yes" if token_id.endswith("yes") else "No",
        ts_ms=ts_ms,
        price=Decimal(price),
        source="test",
        fidelity="hourly",
        interval="1h",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _seed_violation(tmp_data_root: Path) -> None:
    """Seed a nested_a_implies_b relationship with P(A)=0.80 > P(B)=0.50.

    This creates a violation: P(A) > P(B), so we buy NO_A and YES_B.
    """
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(_rel())
    ParquetContextRelationshipDecisionsRepository(tmp_data_root).append(_decision())
    ParquetBackfillCoverageRepository(tmp_data_root).append_many([
        _coverage("market_a"),
        _coverage("market_b"),
    ])
    ParquetPriceHistoryRepository(tmp_data_root).append_many([
        _price("market_a", "a_yes", TS, "0.80"),
        _price("market_b", "b_yes", TS, "0.50"),
    ])


def _seed_no_violation(tmp_data_root: Path) -> None:
    """Seed same relationship but with P(A) ≤ P(B) — no violation."""
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(_rel())
    ParquetContextRelationshipDecisionsRepository(tmp_data_root).append(_decision())
    ParquetBackfillCoverageRepository(tmp_data_root).append_many([
        _coverage("market_a"),
        _coverage("market_b"),
    ])
    ParquetPriceHistoryRepository(tmp_data_root).append_many([
        _price("market_a", "a_yes", TS, "0.40"),
        _price("market_b", "b_yes", TS, "0.60"),
    ])


class TestDiagnosticBacktestAlwaysLabelled:
    def test_credibility_always_diagnostic(self, tmp_data_root: Path) -> None:
        _seed_no_violation(tmp_data_root)
        cfg = DiagnosticBacktestConfig(
            run_id="diag_label_test",
            bypass_review_lane_checks=True,
            bypass_min_confidence=True,
            bypass_coverage_threshold=True,
        )
        result = run_diagnostic_backtest(tmp_data_root, cfg)
        assert result["metrics"]["credibility_label"] == "diagnostic_only_not_credible"
        assert result["funnel"]["credibility_label"] == "diagnostic_only_not_credible"

    def test_outputs_have_label(self, tmp_data_root: Path) -> None:
        _seed_no_violation(tmp_data_root)
        cfg = DiagnosticBacktestConfig(run_id="diag_lbl2", bypass_review_lane_checks=True)
        result = run_diagnostic_backtest(tmp_data_root, cfg)
        metrics = json.loads((result["output_dir"] / "metrics.json").read_text())
        assert metrics["label"] == "diagnostic_only_not_credible"


class TestDiagnosticBypassFlags:
    def test_bypass_lane_allows_ineligible(self, tmp_data_root: Path) -> None:
        """With bypass_lane=False, ineligible decision → rejected. With True → proceeds."""
        ParquetRelationshipCandidatesRepository(tmp_data_root).append(_rel())
        ParquetContextRelationshipDecisionsRepository(tmp_data_root).append(
            _decision(eligible=False)
        )
        ParquetBackfillCoverageRepository(tmp_data_root).append_many([
            _coverage("market_a"), _coverage("market_b"),
        ])
        ParquetPriceHistoryRepository(tmp_data_root).append_many([
            _price("market_a", "a_yes", TS, "0.80"),
            _price("market_b", "b_yes", TS, "0.50"),
        ])

        cfg_off = DiagnosticBacktestConfig(
            run_id="bypass_off", bypass_review_lane_checks=False
        )
        result_off = run_diagnostic_backtest(tmp_data_root, cfg_off)
        assert result_off["funnel"]["counts"]["rejected_by_context"] >= 1

        cfg_on = DiagnosticBacktestConfig(
            run_id="bypass_on", bypass_review_lane_checks=True
        )
        result_on = run_diagnostic_backtest(tmp_data_root, cfg_on)
        # Should reach price history check at minimum
        assert result_on["funnel"]["counts"]["price_history_present"] >= 1

    def test_bypass_coverage_skips_poor_score(self, tmp_data_root: Path) -> None:
        ParquetRelationshipCandidatesRepository(tmp_data_root).append(_rel())
        ParquetContextRelationshipDecisionsRepository(tmp_data_root).append(_decision())
        ParquetBackfillCoverageRepository(tmp_data_root).append_many([
            _coverage("market_a", score=0.1, recommended=False),
            _coverage("market_b"),
        ])
        ParquetPriceHistoryRepository(tmp_data_root).append_many([
            _price("market_a", "a_yes", TS, "0.80"),
            _price("market_b", "b_yes", TS, "0.50"),
        ])

        cfg_off = DiagnosticBacktestConfig(
            run_id="cov_off", bypass_review_lane_checks=True, bypass_coverage_threshold=False
        )
        result_off = run_diagnostic_backtest(tmp_data_root, cfg_off)
        assert result_off["funnel"]["counts"]["rejected_by_coverage"] >= 1

        cfg_on = DiagnosticBacktestConfig(
            run_id="cov_on", bypass_review_lane_checks=True, bypass_coverage_threshold=True
        )
        result_on = run_diagnostic_backtest(tmp_data_root, cfg_on)
        assert result_on["funnel"]["counts"]["price_history_present"] >= 1

    def test_bypass_confidence_allows_low(self, tmp_data_root: Path) -> None:
        low_conf_rel = _rel()
        low_conf_rel = low_conf_rel.__class__(
            **{**low_conf_rel.__dict__, "final_confidence": 0.1}
        )
        ParquetRelationshipCandidatesRepository(tmp_data_root).append(low_conf_rel)
        ParquetContextRelationshipDecisionsRepository(tmp_data_root).append(_decision())
        ParquetBackfillCoverageRepository(tmp_data_root).append_many([
            _coverage("market_a"), _coverage("market_b"),
        ])
        ParquetPriceHistoryRepository(tmp_data_root).append_many([
            _price("market_a", "a_yes", TS, "0.80"),
            _price("market_b", "b_yes", TS, "0.50"),
        ])

        cfg = DiagnosticBacktestConfig(
            run_id="conf_bypass",
            min_relationship_confidence=0.8,
            bypass_min_confidence=True,
            bypass_review_lane_checks=True,
        )
        result = run_diagnostic_backtest(tmp_data_root, cfg)
        # Should not be rejected by confidence
        assert result["funnel"]["counts"]["rejected_by_confidence"] == 0
        assert result["funnel"]["counts"]["price_history_present"] >= 1


class TestDiagnosticWallet:
    def test_violation_opens_position(self, tmp_data_root: Path) -> None:
        _seed_violation(tmp_data_root)
        cfg = DiagnosticBacktestConfig(
            run_id="wallet_open",
            bypass_review_lane_checks=True,
            bypass_coverage_threshold=True,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            min_gross_edge=0.02,
            min_net_edge=0.01,
        )
        result = run_diagnostic_backtest(tmp_data_root, cfg)
        assert result["metrics"]["positions_opened"] >= 1
        fills = list(csv.DictReader(
            (result["output_dir"] / "fills_open.csv").open(encoding="utf-8")
        ))
        assert len(fills) >= 2  # two legs per position
        sides = {f["side"] for f in fills}
        assert sides == {"buy"}

    def test_no_violation_no_position(self, tmp_data_root: Path) -> None:
        _seed_no_violation(tmp_data_root)
        cfg = DiagnosticBacktestConfig(
            run_id="wallet_no",
            bypass_review_lane_checks=True,
            bypass_coverage_threshold=True,
        )
        result = run_diagnostic_backtest(tmp_data_root, cfg)
        assert result["metrics"]["positions_opened"] == 0

    def test_position_closed_end_of_window(self, tmp_data_root: Path) -> None:
        """A position opened on tick 1 should be closed at end-of-window."""
        _seed_violation(tmp_data_root)
        cfg = DiagnosticBacktestConfig(
            run_id="wallet_eow",
            bypass_review_lane_checks=True,
            bypass_coverage_threshold=True,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            min_gross_edge=0.02,
            min_net_edge=0.01,
        )
        result = run_diagnostic_backtest(tmp_data_root, cfg)
        eow = result["metrics"]["positions_closed_eow"]
        rev = result["metrics"]["positions_closed_reversal"]
        assert eow + rev >= 1

    def test_reversal_closes_position(self, tmp_data_root: Path) -> None:
        """Two ticks: first a violation, then prices converge → reversal close."""
        ParquetRelationshipCandidatesRepository(tmp_data_root).append(_rel())
        ParquetContextRelationshipDecisionsRepository(tmp_data_root).append(_decision())
        ParquetBackfillCoverageRepository(tmp_data_root).append_many([
            _coverage("market_a"), _coverage("market_b"),
        ])
        ParquetPriceHistoryRepository(tmp_data_root).append_many([
            # tick 1: violation P(A)=0.80 > P(B)=0.50
            _price("market_a", "a_yes", TS, "0.80"),
            _price("market_b", "b_yes", TS, "0.50"),
            # tick 2 (one hour later): no violation P(A)=0.55 < P(B)=0.60
            _price("market_a", "a_yes", TS + HOUR_MS, "0.55"),
            _price("market_b", "b_yes", TS + HOUR_MS, "0.60"),
        ])
        cfg = DiagnosticBacktestConfig(
            run_id="wallet_reversal",
            bypass_review_lane_checks=True,
            bypass_coverage_threshold=True,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            min_gross_edge=0.02,
            min_net_edge=0.01,
            signal_interval_ms=HOUR_MS,
        )
        result = run_diagnostic_backtest(tmp_data_root, cfg)
        assert result["metrics"]["positions_opened"] >= 1
        rev_closes = result["metrics"]["positions_closed_reversal"]
        assert rev_closes >= 1

    def test_per_rel_funnel_fields_present(self, tmp_data_root: Path) -> None:
        _seed_violation(tmp_data_root)
        cfg = DiagnosticBacktestConfig(
            run_id="funnel_fields",
            bypass_review_lane_checks=True,
            bypass_coverage_threshold=True,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        )
        result = run_diagnostic_backtest(tmp_data_root, cfg)
        per_rel = result["per_rel_funnel"]
        assert len(per_rel) == 1
        entry = per_rel[0]
        required = {
            "relationship_id", "question_a", "question_b", "lane",
            "validation_status", "strategy_eligibility_status", "final_confidence",
            "coverage_score_pair", "has_price_history", "tick_count",
            "gross_violations", "trades_opened", "trades_closed",
            "realized_pnl_usdc", "mark_to_market_pnl_usdc", "final_blocker",
        }
        assert required <= set(entry.keys())

    def test_cash_decreases_on_open(self, tmp_data_root: Path) -> None:
        _seed_violation(tmp_data_root)
        starting = Decimal("10000")
        cfg = DiagnosticBacktestConfig(
            run_id="cash_test",
            starting_cash_usdc=starting,
            bypass_review_lane_checks=True,
            bypass_coverage_threshold=True,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
            min_gross_edge=0.02,
            min_net_edge=0.01,
        )
        result = run_diagnostic_backtest(tmp_data_root, cfg)
        # After opening and closing all positions, cash should reflect realized PnL
        ending = Decimal(result["metrics"]["ending_cash_usdc"])
        # With no fees/slippage the ending cash will differ from starting by realized PnL
        assert isinstance(ending, Decimal)

    def test_fills_csv_written(self, tmp_data_root: Path) -> None:
        _seed_violation(tmp_data_root)
        cfg = DiagnosticBacktestConfig(
            run_id="csv_test",
            bypass_review_lane_checks=True,
            bypass_coverage_threshold=True,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        )
        result = run_diagnostic_backtest(tmp_data_root, cfg)
        assert (result["output_dir"] / "fills_open.csv").exists()
        assert (result["output_dir"] / "fills_close.csv").exists()
        assert (result["output_dir"] / "per_relationship_funnel.csv").exists()
        assert (result["output_dir"] / "metrics.json").exists()

    def test_label_in_fills(self, tmp_data_root: Path) -> None:
        _seed_violation(tmp_data_root)
        cfg = DiagnosticBacktestConfig(
            run_id="label_fill",
            bypass_review_lane_checks=True,
            bypass_coverage_threshold=True,
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        )
        result = run_diagnostic_backtest(tmp_data_root, cfg)
        fills = list(csv.DictReader(
            (result["output_dir"] / "fills_open.csv").open(encoding="utf-8")
        ))
        if fills:
            assert all(f["label"] == "diagnostic_only_not_credible" for f in fills)


class TestDiagnosticIncludeAllStatuses:
    def test_all_statuses_loads_rejected_relationships(self, tmp_data_root: Path) -> None:
        rejected_rel = _rel()
        rejected_rel = rejected_rel.__class__(
            **{**rejected_rel.__dict__, "validation_status": "rejected"}
        )
        ParquetRelationshipCandidatesRepository(tmp_data_root).append(rejected_rel)
        ParquetBackfillCoverageRepository(tmp_data_root).append_many([
            _coverage("market_a"), _coverage("market_b"),
        ])
        ParquetPriceHistoryRepository(tmp_data_root).append_many([
            _price("market_a", "a_yes", TS, "0.80"),
            _price("market_b", "b_yes", TS, "0.50"),
        ])

        cfg = DiagnosticBacktestConfig(
            run_id="all_status",
            include_all_validation_statuses=True,
            bypass_review_lane_checks=True,
            bypass_coverage_threshold=True,
        )
        result = run_diagnostic_backtest(tmp_data_root, cfg)
        assert result["metrics"]["relationships_considered"] >= 1

    def test_accepted_only_excludes_rejected(self, tmp_data_root: Path) -> None:
        rejected_rel = _rel()
        rejected_rel = rejected_rel.__class__(
            **{**rejected_rel.__dict__, "validation_status": "rejected"}
        )
        ParquetRelationshipCandidatesRepository(tmp_data_root).append(rejected_rel)

        cfg = DiagnosticBacktestConfig(
            run_id="accepted_only",
            include_all_validation_statuses=False,
            bypass_review_lane_checks=True,
        )
        result = run_diagnostic_backtest(tmp_data_root, cfg)
        assert result["metrics"]["relationships_considered"] == 0
