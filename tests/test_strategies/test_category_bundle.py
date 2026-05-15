"""Tests for conservative N-way category bundle grouping and scanning."""

from __future__ import annotations

from decimal import Decimal

from polymarket_arb.backtest.category_bundle_replay import run_category_bundle_backtest
from polymarket_arb.storage.base import PriceHistoryRow, RelationshipCandidateRow
from polymarket_arb.storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)
from polymarket_arb.strategies.category_bundle_scanner import (
    CategoryPricePoint,
    scan_category_bundle,
)
from polymarket_arb.strategies.category_outcome_spaces import (
    OutcomeSpaceMetadata,
    group_category_outcome_spaces,
)
from polymarket_arb.strategies.models import CategoryBundleBacktestConfig

_TS = 1_700_000_000_000


def _rel(**kwargs) -> RelationshipCandidateRow:
    defaults = dict(
        relationship_id="rel1",
        market_id_a="m1",
        market_id_b="m2",
        condition_id_a="c1",
        condition_id_b="c2",
        token_id_a_yes="y1",
        token_id_a_no="n1",
        token_id_b_yes="y2",
        token_id_b_no="n2",
        question_a="Will Alpha win the 2026 Test Championship?",
        question_b="Will Beta win the 2026 Test Championship?",
        relationship_type="mutually_exclusive_category",
        entity_match_score=0.1,
        time_scope_match_score=0.9,
        resolution_criteria_match_score=0.9,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.9,
        model_confidence=1.0,
        final_confidence=0.9,
        validation_status="accepted",
        rejection_reasons_json="[]",
        rationale_summary="same outcome space",
        evidence_json="{}",
        rulebook_id="relationship_v2",
        rulebook_version=2,
        rulebook_content_hash="abc",
        strategy_eligibility_status="eligible",
        relationship_family="category",
        outcome_space_match_score=0.95,
        candidate_a="Alpha",
        candidate_b="Beta",
        shared_event="2026 Test Championship",
        ingested_ts_ms=_TS,
    )
    defaults.update(kwargs)
    return RelationshipCandidateRow(**defaults)


def _price(market_id: str, token_id: str, price: str, ts_ms: int = _TS) -> PriceHistoryRow:
    return PriceHistoryRow(
        market_id=market_id,
        condition_id="condition",
        token_id=token_id,
        outcome="Yes",
        ts_ms=ts_ms,
        price=Decimal(price),
        source="clob",
        fidelity="60",
        interval="1h",
        schema_version=1,
        ingested_ts_ms=ts_ms,
    )


def test_group_category_outcome_spaces_dedupes_candidates():
    metadata = {
        "2026_test_championship": OutcomeSpaceMetadata(
            outcome_space_id="2026_test_championship",
            name="2026 Test Championship",
            known_total_candidates=2,
        )
    }
    spaces = group_category_outcome_spaces([_rel()], metadata=metadata)

    assert len(spaces) == 1
    assert spaces[0].outcome_space_id == "2026_test_championship"
    assert spaces[0].known_total_candidates == 2
    assert {c.candidate for c in spaces[0].candidates} == {"Alpha", "Beta"}


def test_different_competitions_do_not_merge():
    rels = [
        _rel(relationship_id="rel1", shared_event="2026 Test Championship"),
        _rel(
            relationship_id="rel2",
            market_id_a="m3",
            market_id_b="m4",
            token_id_a_yes="y3",
            token_id_a_no="n3",
            token_id_b_yes="y4",
            token_id_b_no="n4",
            candidate_a="Gamma",
            candidate_b="Delta",
            shared_event="2027 Test Championship",
        ),
    ]
    spaces = group_category_outcome_spaces(rels)

    assert {s.outcome_space_id for s in spaces} == {
        "2026_test_championship",
        "2027_test_championship",
    }


def test_missing_known_total_is_analysis_only():
    space = group_category_outcome_spaces([_rel()])[0]
    row = scan_category_bundle(
        space,
        yes_prices={
            "y1": CategoryPricePoint("y1", Decimal("0.40"), _TS),
            "y2": CategoryPricePoint("y2", Decimal("0.45"), _TS),
        },
        no_prices={},
        min_net_edge=Decimal("0.01"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert row.completeness_status == "unknown"
    assert not row.strategy_allowed
    assert row.rejection_reason == "incomplete_or_unknown_outcome_space"


def test_complete_yes_basket_creates_accepted_opportunity():
    metadata = {
        "2026_test_championship": OutcomeSpaceMetadata(
            outcome_space_id="2026_test_championship",
            name="2026 Test Championship",
            known_total_candidates=2,
        )
    }
    space = group_category_outcome_spaces([_rel()], metadata=metadata)[0]
    row = scan_category_bundle(
        space,
        yes_prices={
            "y1": CategoryPricePoint("y1", Decimal("0.40"), _TS),
            "y2": CategoryPricePoint("y2", Decimal("0.45"), _TS),
        },
        no_prices={},
        min_net_edge=Decimal("0.01"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert row.completeness_status == "complete"
    assert row.best_executable_basket == "buy_all_yes"
    assert row.rejection_reason is None


def test_costs_can_reject_bundle_opportunity():
    metadata = {
        "2026_test_championship": OutcomeSpaceMetadata(
            outcome_space_id="2026_test_championship",
            name="2026 Test Championship",
            known_total_candidates=2,
        )
    }
    space = group_category_outcome_spaces([_rel()], metadata=metadata)[0]
    row = scan_category_bundle(
        space,
        yes_prices={
            "y1": CategoryPricePoint("y1", Decimal("0.49"), _TS),
            "y2": CategoryPricePoint("y2", Decimal("0.49"), _TS),
        },
        no_prices={},
        min_net_edge=Decimal("0.01"),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("100"),
    )

    assert row.rejection_reason == "net_edge_below_threshold"


def test_category_backtest_writes_artifacts_and_trades(tmp_data_root, tmp_path):
    rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
    rel_repo.append(_rel())
    price_repo = ParquetPriceHistoryRepository(tmp_data_root)
    price_repo.append_many([
        _price("m1", "y1", "0.40"),
        _price("m2", "y2", "0.45"),
        _price("m1", "n1", "0.60"),
        _price("m2", "n2", "0.55"),
    ])
    metadata_path = tmp_path / "category_outcome_spaces.yaml"
    metadata_path.write_text(
        "outcome_spaces:\n"
        "  2026_test_championship:\n"
        "    name: 2026 Test Championship\n"
        "    known_total_candidates: 2\n",
        encoding="utf-8",
    )

    result = run_category_bundle_backtest(
        tmp_data_root,
        CategoryBundleBacktestConfig(
            run_id="category_test",
            metadata_path=str(metadata_path),
            min_net_edge=Decimal("0.01"),
            slippage_bps=Decimal("0"),
        ),
    )

    out_dir = result["output_dir"]
    assert (out_dir / "funnel_audit.json").exists()
    assert (out_dir / "bundle_scan.csv").exists()
    assert (out_dir / "bundle_opportunities.csv").exists()
    assert (out_dir / "trades.csv").exists()
    assert result["metrics"]["trades_executed"] == 1
