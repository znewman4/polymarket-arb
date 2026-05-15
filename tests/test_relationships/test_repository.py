"""Tests for relationship candidates repository."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from polymarket_arb.storage.base import RelationshipCandidateRow
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

_TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _row(
    relationship_id: str = "rel_001",
    validation_status: str = "accepted",
    relationship_type: str = "nested_a_implies_b",
    final_confidence: float = 0.75,
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=relationship_id,
        market_id_a="market_a",
        market_id_b="market_b",
        condition_id_a="cond_a",
        condition_id_b="cond_b",
        token_id_a_yes="tok_a_yes",
        token_id_a_no="tok_a_no",
        token_id_b_yes="tok_b_yes",
        token_id_b_no="tok_b_no",
        question_a="Will BTC exceed $100k?",
        question_b="Will BTC exceed $90k?",
        relationship_type=relationship_type,
        entity_match_score=0.9,
        time_scope_match_score=0.9,
        resolution_criteria_match_score=0.7,
        threshold_relation_json=json.dumps({"variable": "btc_price", "direction": "a_implies_b"}),
        semantic_similarity_score=None,
        deterministic_confidence=final_confidence,
        model_confidence=1.0,
        final_confidence=final_confidence,
        validation_status=validation_status,
        rejection_reasons_json="[]",
        rationale_summary="Nesting: A>100k implies B>90k",
        evidence_json="{}",
        rulebook_id="relationship_v1",
        rulebook_version=1,
        rulebook_content_hash="abc123",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


class TestParquetRelationshipCandidatesRepository:
    def test_parquet_roundtrip(self, tmp_data_root):
        repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        original = _row()
        repo.append(original)

        retrieved = repo.get_latest(original.relationship_id)
        assert retrieved is not None
        assert retrieved.relationship_id == original.relationship_id
        assert retrieved.final_confidence == original.final_confidence
        assert retrieved.validation_status == original.validation_status

    def test_append_many(self, tmp_data_root):
        repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        rows = [_row(f"rel_{i:03d}", validation_status="accepted") for i in range(5)]
        count = repo.append_many(rows)
        assert count == 5

    def test_iter_latest_returns_newest(self, tmp_data_root):
        """Re-appending with same relationship_id → iter_latest shows newest."""
        repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        r1 = _row("rel_001", final_confidence=0.6)
        r2 = RelationshipCandidateRow(**{**r1.__dict__, "final_confidence": 0.9, "ingested_ts_ms": _TS + 1000})
        repo.append(r1)
        repo.append(r2)

        latest = list(repo.iter_latest())
        assert len(latest) == 1
        assert abs(latest[0].final_confidence - 0.9) < 0.001

    def test_iter_accepted_filters_correctly(self, tmp_data_root):
        repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        repo.append(_row("rel_001", validation_status="accepted"))
        repo.append(_row("rel_002", validation_status="rejected"))
        repo.append(_row("rel_003", validation_status="needs_manual_review"))

        accepted = list(repo.iter_accepted())
        assert len(accepted) == 1
        assert accepted[0].relationship_id == "rel_001"

    def test_empty_returns_empty(self, tmp_data_root):
        repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        assert repo.get_latest("nonexistent") is None
        assert list(repo.iter_latest()) == []
        assert list(repo.iter_accepted()) == []
