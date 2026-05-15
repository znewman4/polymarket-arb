"""Tests for the relationship data funnel report."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from polymarket_arb.reports.relationship_funnel_report import generate_relationship_funnel_report
from polymarket_arb.storage.base import PriceHistoryRow, RelationshipCandidateRow
from polymarket_arb.storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _rel(rel_id: str = "rel_f01") -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=rel_id,
        market_id_a="market_a", market_id_b="market_b",
        condition_id_a="cond_a", condition_id_b="cond_b",
        token_id_a_yes="a_yes", token_id_a_no="a_no",
        token_id_b_yes="b_yes", token_id_b_no="b_no",
        question_a="Will A happen?", question_b="Will B happen?",
        relationship_type="mutually_exclusive_category",
        entity_match_score=1.0, time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0, threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.8, model_confidence=0.8, final_confidence=0.8,
        validation_status="accepted", rejection_reasons_json="[]",
        rationale_summary="test", evidence_json="{}",
        rulebook_id="v2", rulebook_version=2, rulebook_content_hash="hash",
        schema_version=1, ingested_ts_ms=TS,
    )


def _price(market_id: str, token_id: str, price: str = "0.5") -> PriceHistoryRow:
    return PriceHistoryRow(
        market_id=market_id, condition_id=f"cond_{market_id}", token_id=token_id,
        outcome="Yes", ts_ms=TS, price=Decimal(price), source="test",
        fidelity="hourly", interval="1h", schema_version=1, ingested_ts_ms=TS,
    )


def test_funnel_report_empty_store(tmp_data_root: Path, tmp_path: Path) -> None:
    """Report generates without crashing on an empty store."""
    out = tmp_path / "funnel"
    csv_p, md_p = generate_relationship_funnel_report(tmp_data_root, out)
    assert csv_p.exists()
    assert md_p.exists()
    assert csv_p.read_text() == ""  # empty store → empty CSV


def test_funnel_report_seeded_relationship(tmp_data_root: Path, tmp_path: Path) -> None:
    """Report has one row per relationship with correct aligned_tick_count."""
    ParquetRelationshipCandidatesRepository(tmp_data_root).append(_rel())
    ParquetPriceHistoryRepository(tmp_data_root).append_many([
        _price("market_a", "a_yes", "0.60"),
        _price("market_b", "b_yes", "0.50"),
    ])

    out = tmp_path / "funnel"
    csv_p, md_p = generate_relationship_funnel_report(tmp_data_root, out)

    import csv
    rows = list(csv.DictReader(csv_p.open(encoding="utf-8")))
    assert len(rows) == 1
    row = rows[0]
    assert row["relationship_id"] == "rel_f01"
    assert row["relationship_type"] == "mutually_exclusive_category"
    # Both tokens have price history → aligned_tick_count ≥ 1
    assert int(row["aligned_tick_count"]) >= 1
    # violations not evaluated by default
    assert int(row["gross_violations"]) == -1
    assert md_p.exists()
