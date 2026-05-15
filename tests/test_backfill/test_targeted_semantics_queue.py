"""Tests for targeted semantic queue generation."""

from __future__ import annotations

import csv

from polymarket_arb.backfill.targeted_semantics_queue import (
    build_targeted_semantics_queue,
    read_target_market_ids,
)
from polymarket_arb.storage.base import RelationshipCandidateRow
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

_TS = 1_700_000_000_000


def _rel() -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
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


def test_targeted_semantics_queue_contains_category_markets(tmp_data_root):
    repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
    repo.append(_rel())

    queue_path = build_targeted_semantics_queue(tmp_data_root)

    assert queue_path.exists()
    assert read_target_market_ids(queue_path) == {"m1", "m2"}
    with queue_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows
    assert all("accepted_category_group" in row["reasons"] for row in rows)
