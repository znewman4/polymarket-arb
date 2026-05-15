"""Tests for the relationship-market coverage audit."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from polymarket_arb.backtest.coverage_audit import run_coverage_audit
from polymarket_arb.storage.base import (
    BackfillCoverageRow,
    ContextRelationshipDecisionRow,
    MarketRow,
    PriceHistoryRow,
    RelationshipCandidateRow,
)
from polymarket_arb.storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from polymarket_arb.storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository
from polymarket_arb.storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _rel(
    rel_id: str = "rel_01",
    market_a: str = "market_a",
    market_b: str = "market_b",
    tok_a_yes: str | None = "tok_a_yes",
    tok_b_yes: str | None = "tok_b_yes",
    tok_a_no: str = "tok_a_no",
    tok_b_no: str = "tok_b_no",
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=rel_id,
        market_id_a=market_a,
        market_id_b=market_b,
        condition_id_a="cond_a",
        condition_id_b="cond_b",
        token_id_a_yes=tok_a_yes,
        token_id_a_no=tok_a_no,
        token_id_b_yes=tok_b_yes,
        token_id_b_no=tok_b_no,
        question_a="Will A happen?",
        question_b="Will B happen?",
        relationship_type="nested_a_implies_b",
        entity_match_score=1.0,
        time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.8,
        model_confidence=0.9,
        final_confidence=0.8,
        validation_status="accepted",
        rejection_reasons_json="[]",
        rationale_summary="test",
        evidence_json="{}",
        rulebook_id="v2",
        rulebook_version=2,
        rulebook_content_hash="hash",
        strategy_eligibility_status="eligible",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _market(market_id: str, clob_tokens: list[str]) -> MarketRow:
    return MarketRow(
        id=market_id,
        condition_id=f"cond_{market_id}",
        slug=market_id,
        question=f"Question {market_id}?",
        description=None,
        end_date_ms=None,
        start_date_ms=None,
        closed_at_ms=None,
        resolved_at_ms=None,
        active=True,
        closed=False,
        archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=clob_tokens,
        volume=None,
        liquidity=None,
        event_id=None,
        neg_risk=False,
        text_hash="hash",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _price(market_id: str, token_id: str, price: str = "0.6") -> PriceHistoryRow:
    return PriceHistoryRow(
        market_id=market_id,
        condition_id=f"cond_{market_id}",
        token_id=token_id,
        outcome="Yes",
        ts_ms=TS,
        price=Decimal(price),
        source="test",
        fidelity="hourly",
        interval="1h",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _coverage(market_id: str, score: float = 1.0, recommended: bool = True) -> BackfillCoverageRow:
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
        price_points_count=5,
        trade_points_count=0,
        first_price_ts_ms=TS,
        last_price_ts_ms=TS + 3_600_000,
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


def _decision(rel_id: str = "rel_01") -> ContextRelationshipDecisionRow:
    return ContextRelationshipDecisionRow(
        decision_id="dec_01",
        relationship_id=rel_id,
        context_space_id="space_01",
        context_rule_ids_json='["r1"]',
        previous_validation_status="accepted",
        new_validation_status="accepted",
        previous_strategy_eligibility="eligible",
        new_strategy_eligibility="eligible",
        strategy_lane="exploratory_context_auto_approved",
        decision_reason="auto_approved",
        evidence_summary="test",
        schema_version=1,
        ingested_ts_ms=TS,
    )


class TestCoverageAuditEmptyStore:
    def test_empty_returns_empty(self, tmp_data_root: Path) -> None:
        rows = run_coverage_audit(tmp_data_root)
        assert rows == []


class TestCoverageAuditFullCoverage:
    def test_fully_covered_relationship_has_no_blocker(self, tmp_data_root: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        market_repo = ParquetMarketsRepository(tmp_data_root)
        price_repo = ParquetPriceHistoryRepository(tmp_data_root)
        cov_repo = ParquetBackfillCoverageRepository(tmp_data_root)
        dec_repo = ParquetContextRelationshipDecisionsRepository(tmp_data_root)

        rel_repo.append(_rel())
        market_repo.upsert_markets([
            _market("market_a", ["tok_a_yes", "tok_a_no"]),
            _market("market_b", ["tok_b_yes", "tok_b_no"]),
        ])
        price_repo.append_many([
            _price("market_a", "tok_a_yes"),
            _price("market_b", "tok_b_yes"),
        ])
        cov_repo.append_many([_coverage("market_a"), _coverage("market_b")])
        dec_repo.append(_decision())

        rows = run_coverage_audit(tmp_data_root)
        assert len(rows) == 1
        r = rows[0]
        assert r.gamma_exists_a and r.gamma_exists_b
        assert r.price_history_exists_a and r.price_history_exists_b
        assert r.both_have_price_history
        assert r.coverage_recommended_a and r.coverage_recommended_b
        assert r.has_context_decision
        assert r.final_blocker == "none"

    def test_tick_counts_match_price_rows(self, tmp_data_root: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        market_repo = ParquetMarketsRepository(tmp_data_root)
        price_repo = ParquetPriceHistoryRepository(tmp_data_root)
        cov_repo = ParquetBackfillCoverageRepository(tmp_data_root)
        dec_repo = ParquetContextRelationshipDecisionsRepository(tmp_data_root)

        rel_repo.append(_rel())
        market_repo.upsert_markets([
            _market("market_a", ["tok_a_yes"]),
            _market("market_b", ["tok_b_yes"]),
        ])
        price_repo.append_many([
            _price("market_a", "tok_a_yes", "0.6"),
            _price("market_a", "tok_a_yes", "0.65"),
            _price("market_b", "tok_b_yes", "0.4"),
        ])
        cov_repo.append_many([_coverage("market_a"), _coverage("market_b")])
        dec_repo.append(_decision())

        rows = run_coverage_audit(tmp_data_root)
        assert rows[0].tick_count_a == 2
        assert rows[0].tick_count_b == 1


class TestCoverageAuditBlockers:
    def test_missing_gamma_detected(self, tmp_data_root: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        rel_repo.append(_rel())
        rows = run_coverage_audit(tmp_data_root)
        assert rows[0].final_blocker == "no_gamma_metadata_a"
        assert not rows[0].gamma_exists_a

    def test_missing_price_history_a_detected(self, tmp_data_root: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        market_repo = ParquetMarketsRepository(tmp_data_root)
        price_repo = ParquetPriceHistoryRepository(tmp_data_root)

        rel_repo.append(_rel())
        market_repo.upsert_markets([
            _market("market_a", ["tok_a_yes"]),
            _market("market_b", ["tok_b_yes"]),
        ])
        # Only market B has price history
        price_repo.append(_price("market_b", "tok_b_yes"))

        rows = run_coverage_audit(tmp_data_root)
        assert "no_price_history_a" in rows[0].final_blocker
        assert not rows[0].price_history_exists_a
        assert rows[0].price_history_exists_b

    def test_missing_price_history_b_detected(self, tmp_data_root: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        market_repo = ParquetMarketsRepository(tmp_data_root)
        price_repo = ParquetPriceHistoryRepository(tmp_data_root)

        rel_repo.append(_rel())
        market_repo.upsert_markets([
            _market("market_a", ["tok_a_yes"]),
            _market("market_b", ["tok_b_yes"]),
        ])
        price_repo.append(_price("market_a", "tok_a_yes"))

        rows = run_coverage_audit(tmp_data_root)
        assert "no_price_history_b" in rows[0].final_blocker

    def test_no_context_decision_is_blocker(self, tmp_data_root: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        market_repo = ParquetMarketsRepository(tmp_data_root)
        price_repo = ParquetPriceHistoryRepository(tmp_data_root)
        cov_repo = ParquetBackfillCoverageRepository(tmp_data_root)

        rel_repo.append(_rel())
        market_repo.upsert_markets([
            _market("market_a", ["tok_a_yes"]),
            _market("market_b", ["tok_b_yes"]),
        ])
        price_repo.append_many([
            _price("market_a", "tok_a_yes"),
            _price("market_b", "tok_b_yes"),
        ])
        cov_repo.append_many([_coverage("market_a"), _coverage("market_b")])
        # no decision added

        rows = run_coverage_audit(tmp_data_root)
        assert rows[0].final_blocker == "no_context_decision"
        assert not rows[0].has_context_decision

    def test_coverage_not_recommended_is_blocker(self, tmp_data_root: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        market_repo = ParquetMarketsRepository(tmp_data_root)
        price_repo = ParquetPriceHistoryRepository(tmp_data_root)
        cov_repo = ParquetBackfillCoverageRepository(tmp_data_root)

        rel_repo.append(_rel())
        market_repo.upsert_markets([
            _market("market_a", ["tok_a_yes"]),
            _market("market_b", ["tok_b_yes"]),
        ])
        price_repo.append_many([
            _price("market_a", "tok_a_yes"),
            _price("market_b", "tok_b_yes"),
        ])
        cov_repo.append_many([
            _coverage("market_a", score=0.3, recommended=False),
            _coverage("market_b"),
        ])

        rows = run_coverage_audit(tmp_data_root)
        assert "coverage_not_recommended_a" in rows[0].final_blocker

    def test_multiple_relationships_audited_independently(self, tmp_data_root: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        market_repo = ParquetMarketsRepository(tmp_data_root)
        price_repo = ParquetPriceHistoryRepository(tmp_data_root)
        cov_repo = ParquetBackfillCoverageRepository(tmp_data_root)
        dec_repo = ParquetContextRelationshipDecisionsRepository(tmp_data_root)

        # rel_01: fully covered (uses tok_a_yes / tok_b_yes)
        rel_repo.append(_rel("rel_01", "market_a", "market_b",
                             tok_a_yes="tok_a_yes", tok_b_yes="tok_b_yes"))
        # rel_02: different tokens that have no price history
        rel_repo.append(_rel("rel_02", "market_c", "market_d",
                             tok_a_yes="tok_c_yes", tok_b_yes="tok_d_yes",
                             tok_a_no="tok_c_no", tok_b_no="tok_d_no"))

        market_repo.upsert_markets([
            _market("market_a", ["tok_a_yes"]),
            _market("market_b", ["tok_b_yes"]),
            _market("market_c", ["tok_c_yes"]),
            _market("market_d", ["tok_d_yes"]),
        ])
        price_repo.append_many([
            _price("market_a", "tok_a_yes"),
            _price("market_b", "tok_b_yes"),
            # no price history for market_c or market_d
        ])
        cov_repo.append_many([
            _coverage("market_a"), _coverage("market_b"),
            _coverage("market_c"), _coverage("market_d"),
        ])
        dec_repo.append(_decision("rel_01"))
        dec_repo.append(_decision("rel_02"))

        rows = run_coverage_audit(tmp_data_root)
        by_id = {r.relationship_id: r for r in rows}
        assert by_id["rel_01"].final_blocker == "none"
        assert "no_price_history" in by_id["rel_02"].final_blocker
