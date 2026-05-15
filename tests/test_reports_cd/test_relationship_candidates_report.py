"""Tests for the relationship candidates HTML report."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from polymarket_arb.reports.relationship_candidates_report import (
    generate_relationship_candidates_report,
)
from polymarket_arb.storage.base import RelationshipCandidateRow
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

_TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _row(
    relationship_id: str,
    status: str = "accepted",
    rel_type: str = "nested_a_implies_b",
    question_a: str = "Will BTC exceed $100k?",
    question_b: str = "Will BTC exceed $90k?",
    final_confidence: float = 0.8,
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=relationship_id,
        market_id_a=f"mkt_a_{relationship_id}",
        market_id_b=f"mkt_b_{relationship_id}",
        condition_id_a=None,
        condition_id_b=None,
        token_id_a_yes=f"tok_a_yes_{relationship_id}",
        token_id_a_no=f"tok_a_no_{relationship_id}",
        token_id_b_yes=f"tok_b_yes_{relationship_id}",
        token_id_b_no=f"tok_b_no_{relationship_id}",
        question_a=question_a,
        question_b=question_b,
        relationship_type=rel_type,
        entity_match_score=0.9,
        time_scope_match_score=0.9,
        resolution_criteria_match_score=0.7,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=final_confidence,
        model_confidence=1.0,
        final_confidence=final_confidence,
        validation_status=status,
        rejection_reasons_json=json.dumps([{"code": "entity_mismatch", "detail": "test"}]) if status == "rejected" else "[]",
        rationale_summary="Test rationale.",
        evidence_json="{}",
        rulebook_id="relationship_v1",
        rulebook_version=1,
        rulebook_content_hash="abc",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


class TestRelationshipCandidatesReport:
    def test_report_writes_html_and_csv(self, tmp_data_root):
        """Report generates HTML file and all required CSV files."""
        repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        repo.append_many([
            _row("r001", status="accepted"),
            _row("r002", status="rejected"),
            _row("r003", status="needs_manual_review"),
        ])

        report_dir = tmp_data_root / "test_report"
        html_path = generate_relationship_candidates_report(
            tmp_data_root, output_dir=report_dir
        )

        assert html_path.exists()
        assert html_path.suffix == ".html"
        assert (report_dir / "relationships.csv").exists()
        assert (report_dir / "accepted_relationships.csv").exists()
        assert (report_dir / "rejected_relationships.csv").exists()
        assert (report_dir / "manual_review_relationships.csv").exists()

    def test_report_tables_include_relationship_id_and_market_ids(self, tmp_data_root):
        """HTML and CSV tables must contain trace IDs."""
        repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        repo.append(_row("r001", status="accepted"))

        report_dir = tmp_data_root / "test_report2"
        html_path = generate_relationship_candidates_report(
            tmp_data_root, output_dir=report_dir
        )

        html_content = html_path.read_text(encoding="utf-8")
        # relationship_id should appear in HTML
        assert "r001" in html_content

        # CSVs should have relationship_id column
        import csv
        csv_path = report_dir / "relationships.csv"
        with csv_path.open() as f:
            reader = csv.DictReader(f)
            cols = reader.fieldnames or []
        assert "relationship_id" in cols
        assert "market_id_a" in cols
        assert "market_id_b" in cols

    def test_long_questions_truncated_in_html_not_csv(self, tmp_data_root):
        """Long question text → truncated in HTML table, full in CSV."""
        long_question = "A" * 150  # > 80 chars
        repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        repo.append(_row("r001", status="accepted", question_a=long_question))

        report_dir = tmp_data_root / "test_report3"
        html_path = generate_relationship_candidates_report(
            tmp_data_root, output_dir=report_dir
        )

        html_content = html_path.read_text(encoding="utf-8")
        # HTML should contain truncated version (not the full 150 chars of A)
        assert long_question not in html_content  # full text should not appear

        # CSV should have full text
        import csv
        csv_path = report_dir / "relationships.csv"
        with csv_path.open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) > 0
        assert long_question in rows[0]["question_a"]

    def test_empty_lake_generates_report_with_empty_state(self, tmp_data_root):
        """Report generates even when there's no relationship data (empty-state messaging)."""
        report_dir = tmp_data_root / "empty_report"
        html_path = generate_relationship_candidates_report(
            tmp_data_root, output_dir=report_dir
        )
        assert html_path.exists()
        html_content = html_path.read_text(encoding="utf-8")
        assert "0" in html_content  # total_considered should be 0
