"""Context-aware strategy replay tests."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from decimal import Decimal

from polymarket_arb.backtest.context_aware_replay import run_context_aware_backtest
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


def _relationship(
    *,
    validation_status: str = "accepted",
    relationship_type: str = "nested_a_implies_b",
    relationship_subtype: str = "championship_implies_conference",
    relationship_family: str = "nesting",
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id="rel_bt",
        market_id_a="market_a",
        market_id_b="market_b",
        condition_id_a="cond_a",
        condition_id_b="cond_b",
        token_id_a_yes="a_yes",
        token_id_a_no="a_no",
        token_id_b_yes="b_yes",
        token_id_b_no="b_no",
        question_a="Will the Oklahoma City Thunder win the 2026 NBA Finals?",
        question_b="Will the Oklahoma City Thunder win the NBA Western Conference Finals?",
        relationship_type=relationship_type,
        entity_match_score=1.0,
        time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.9,
        model_confidence=1.0,
        final_confidence=0.9,
        validation_status=validation_status,
        rejection_reasons_json="[]",
        rationale_summary="fixture",
        evidence_json="{}",
        rulebook_id="relationship_v2",
        rulebook_version=2,
        rulebook_content_hash="hash",
        relationship_validity_status=validation_status,
        strategy_eligibility_status="eligible" if validation_status == "accepted" else "ineligible",
        relationship_family=relationship_family,
        relationship_subtype=relationship_subtype,
        outcome_space_id="nba_fixture",
        outcome_subtype_a="team_wins_championship",
        outcome_subtype_b="team_wins_conference",
        team_a="Oklahoma City Thunder",
        team_b="Oklahoma City Thunder",
        strategy_family="nesting",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _coverage(market_id: str) -> BackfillCoverageRow:
    return BackfillCoverageRow(
        market_id=market_id,
        condition_id=f"cond_{market_id}",
        question="fixture",
        start_ts_ms=TS,
        end_ts_ms=TS + 3_600_000,
        requested_days=1,
        has_gamma=True,
        has_price_history=True,
        has_trade_history=False,
        has_semantics=True,
        has_rulebook_score=True,
        has_implications=True,
        has_embeddings=False,
        has_backfill_coverage=True,
        price_points_count=2,
        trade_points_count=0,
        first_price_ts_ms=TS,
        last_price_ts_ms=TS + 3_600_000,
        missing_price_gap_count=0,
        largest_price_gap_ms=0,
        price_min=Decimal("0.1"),
        price_max=Decimal("0.9"),
        price_out_of_bounds_count=0,
        duplicate_timestamp_count=0,
        coverage_score=1.0,
        recommended_for_backtest=True,
        exclusion_reasons_json="[]",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _price(market_id: str, token_id: str, ts_ms: int, price: str) -> PriceHistoryRow:
    return PriceHistoryRow(
        market_id=market_id,
        condition_id=f"cond_{market_id}",
        token_id=token_id,
        outcome="Yes" if token_id.endswith("yes") else "No",
        ts_ms=ts_ms,
        price=Decimal(price),
        source="fixture",
        fidelity="hourly",
        interval="1h",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def test_context_aware_backtest_lane_funnel_buy_only_and_no_lookahead(tmp_data_root):
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(_relationship())
    ParquetContextRelationshipDecisionsRepository(tmp_data_root).append(
        ContextRelationshipDecisionRow(
            decision_id="dec_bt",
            relationship_id="rel_bt",
            context_space_id="nba_championship_conference_progression",
            context_rule_ids_json=json.dumps(["world", "terms"]),
            previous_validation_status="needs_manual_review",
            new_validation_status="accepted",
            previous_strategy_eligibility="manual_review",
            new_strategy_eligibility="eligible",
            strategy_lane="strict_context_valid",
            decision_reason="valid world context and market terms",
            evidence_summary="fixture",
            schema_version=1,
            ingested_ts_ms=TS,
        )
    )
    ParquetBackfillCoverageRepository(tmp_data_root).append_many([_coverage("market_a"), _coverage("market_b")])
    ParquetPriceHistoryRepository(tmp_data_root).append_many(
        [
            _price("market_a", "a_yes", TS, "0.80"),
            _price("market_b", "b_yes", TS, "0.60"),
            _price("market_a", "a_no", TS, "0.20"),
            _price("market_b", "b_no", TS, "0.40"),
        ]
    )

    result = run_context_aware_backtest(
        tmp_data_root,
        ContextAwareBacktestConfig(
            run_id="ctx_test",
            lane="strict_context_valid",
            min_net_edge=0.01,
            slippage_bps=Decimal("0"),
            fee_bps=Decimal("0"),
        ),
    )

    assert result["metrics"]["trades_executed"] == 1
    assert result["funnel"]["counts"]["ticks_evaluated"] >= 1
    assert result["metrics"]["credibility_label"] == "data_insufficient"

    trade_rows = list(csv.DictReader((result["output_dir"] / "trades.csv").open(encoding="utf-8")))
    assert {row["side"] for row in trade_rows} == {"buy"}
    assert {row["token_id"] for row in trade_rows} == {"a_no", "b_yes"}

    no_lookahead = json.loads((result["output_dir"] / "no_lookahead_audit.json").read_text(encoding="utf-8"))
    assert no_lookahead["violations"] == 0


def test_context_aware_all_decisions_universe_uses_reviewed_decision_not_old_status(tmp_data_root):
    rel = _relationship(validation_status="rejected")
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(rel)
    ParquetContextRelationshipDecisionsRepository(tmp_data_root).append(
        ContextRelationshipDecisionRow(
            decision_id="dec_reviewed",
            relationship_id="rel_bt",
            context_space_id="nba_championship_conference_progression",
            context_rule_ids_json=json.dumps(["world"]),
            previous_validation_status="rejected",
            new_validation_status="needs_manual_review",
            previous_strategy_eligibility="ineligible",
            new_strategy_eligibility="eligible",
            strategy_lane="reviewed_context_valid",
            decision_reason="world context reviewed; market terms incomplete or unreviewed",
            evidence_summary="fixture",
            schema_version=1,
            ingested_ts_ms=TS,
        )
    )
    ParquetBackfillCoverageRepository(tmp_data_root).append_many([_coverage("market_a"), _coverage("market_b")])
    ParquetPriceHistoryRepository(tmp_data_root).append_many(
        [
            _price("market_a", "a_yes", TS, "0.80"),
            _price("market_b", "b_yes", TS, "0.60"),
            _price("market_a", "a_no", TS, "0.20"),
            _price("market_b", "b_no", TS, "0.40"),
        ]
    )

    result = run_context_aware_backtest(
        tmp_data_root,
        ContextAwareBacktestConfig(
            run_id="ctx_reviewed_all",
            lane="reviewed_context_valid",
            relationship_universe="all_with_context_decisions",
            min_relationship_confidence=0.0,
            min_net_edge=0.01,
            slippage_bps=Decimal("0"),
            fee_bps=Decimal("0"),
        ),
    )

    assert result["funnel"]["counts"]["relationships_loaded"] == 1
    assert result["metrics"]["trades_executed"] == 1


def test_context_aware_nba_context_uses_nesting_logic_not_inverse(tmp_data_root):
    rel = _relationship(
        relationship_type="inverse",
        relationship_subtype="same_topic_no_trade",
        relationship_family="contradiction",
    )
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(rel)
    ParquetContextRelationshipDecisionsRepository(tmp_data_root).append(
        ContextRelationshipDecisionRow(
            decision_id="dec_reviewed_nba",
            relationship_id="rel_bt",
            context_space_id="nba_championship_conference_progression",
            context_rule_ids_json=json.dumps(["world"]),
            previous_validation_status="rejected",
            new_validation_status="accepted",
            previous_strategy_eligibility="ineligible",
            new_strategy_eligibility="eligible",
            strategy_lane="reviewed_context_valid",
            decision_reason="world context reviewed; market terms incomplete or unreviewed",
            evidence_summary="fixture",
            schema_version=1,
            ingested_ts_ms=TS,
        )
    )
    ParquetBackfillCoverageRepository(tmp_data_root).append_many([_coverage("market_a"), _coverage("market_b")])
    ParquetPriceHistoryRepository(tmp_data_root).append_many(
        [
            _price("market_a", "a_yes", TS, "0.80"),
            _price("market_b", "b_yes", TS, "0.60"),
            _price("market_a", "a_no", TS, "0.20"),
            _price("market_b", "b_no", TS, "0.40"),
        ]
    )

    result = run_context_aware_backtest(
        tmp_data_root,
        ContextAwareBacktestConfig(
            run_id="ctx_nba_nesting",
            lane="reviewed_context_valid",
            relationship_universe="all_with_context_decisions",
            min_relationship_confidence=0.0,
            min_net_edge=0.01,
            slippage_bps=Decimal("0"),
            fee_bps=Decimal("0"),
        ),
    )

    assert result["metrics"]["trades_executed"] == 1
    signal_rows = list(csv.DictReader((result["output_dir"] / "signals.csv").open(encoding="utf-8")))
    assert signal_rows[0]["relationship_type"] == "nested_a_implies_b"
    assert signal_rows[0]["inequality_violated"].startswith("P(A)=")
    trade_rows = list(csv.DictReader((result["output_dir"] / "trades.csv").open(encoding="utf-8")))
    assert {row["token_id"] for row in trade_rows} == {"a_no", "b_yes"}


def test_context_aware_accepted_only_universe_keeps_old_status_filter(tmp_data_root):
    rel = _relationship(validation_status="rejected")
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(rel)
    ParquetContextRelationshipDecisionsRepository(tmp_data_root).append(
        ContextRelationshipDecisionRow(
            decision_id="dec_reviewed_rejected",
            relationship_id="rel_bt",
            context_space_id="nba_championship_conference_progression",
            context_rule_ids_json=json.dumps(["world"]),
            previous_validation_status="rejected",
            new_validation_status="needs_manual_review",
            previous_strategy_eligibility="ineligible",
            new_strategy_eligibility="eligible",
            strategy_lane="reviewed_context_valid",
            decision_reason="world context reviewed; market terms incomplete or unreviewed",
            evidence_summary="fixture",
            schema_version=1,
            ingested_ts_ms=TS,
        )
    )

    result = run_context_aware_backtest(
        tmp_data_root,
        ContextAwareBacktestConfig(
            run_id="ctx_accepted_only",
            lane="reviewed_context_valid",
            relationship_universe="accepted_only",
        ),
    )

    assert result["funnel"]["counts"]["relationships_loaded"] == 0
