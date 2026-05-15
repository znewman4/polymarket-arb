"""Tests for the Semantic Quality HTML report."""

from __future__ import annotations

from polymarket_arb.reports.semantic_quality_report import generate_semantic_quality_report
from polymarket_arb.storage.base import MarketSemanticsRow
from polymarket_arb.storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository


def _sem_row(market_id: str = "m1") -> MarketSemanticsRow:
    return MarketSemanticsRow(
        source_market_id=market_id,
        source_condition_id=None,
        question=f"Will {market_id} resolve?",
        canonical_question=f"Will {market_id} resolve?",
        market_type="binary",
        subject_entities=[],
        event_entities=[],
        temporal_phrase=None,
        temporal_phrase_normalized=None,
        temporal_resolution="vague",
        exact_deadline_ms=None,
        date_constraints_json="{}",
        jurisdiction=None,
        positive_resolution_condition="If yes",
        negative_resolution_condition="If no",
        necessary_conditions_for_yes=[],
        sufficient_conditions_for_yes=[],
        necessary_conditions_for_no=[],
        sufficient_conditions_for_no=[],
        evidence_required=[],
        ambiguity_flags=["vague_deadline"],
        ambiguity_score=0.3,
        semantic_confidence=0.75,
        needs_manual_review=False,
        explanation_summary="Test semantics.",
        flag_rationales_json=None,
        uncertainty_notes_json=None,
        rule_curation_notes_json=None,
        raw_response_hash="deadbeef",
        model_name="mock-llm",
        prompt_version="v1",
        rulebook_id="ambiguity_v1",
        rulebook_version=1,
        extraction_id="ext001",
        schema_version=1,
        ingested_ts_ms=1_700_000_000_000,
    )


def test_semantic_quality_report_writes_tables_and_graphs(tmp_data_root, tmp_path):
    sem_repo = ParquetMarketSemanticsRepository(tmp_data_root)
    for i in range(3):
        sem_repo.upsert(_sem_row(f"m{i}"))

    output_dir = tmp_path / "sem_report"
    path = generate_semantic_quality_report(tmp_data_root, output_dir)

    assert path.exists()
    html = path.read_text()
    assert "<!DOCTYPE html>" in html
    assert "Semantic Quality" in html
    assert "m0" in html or "m1" in html or "m2" in html


def test_semantic_quality_report_empty_lake(tmp_data_root, tmp_path):
    """Report should not crash on empty lake."""
    output_dir = tmp_path / "sem_report_empty"
    path = generate_semantic_quality_report(tmp_data_root, output_dir)
    assert path.exists()
    html = path.read_text()
    assert "<!DOCTYPE html>" in html


def test_semantic_quality_report_no_thinking_in_output(tmp_data_root, tmp_path):
    """Rendered HTML must not contain <think> markers."""
    sem_repo = ParquetMarketSemanticsRepository(tmp_data_root)
    # Inject a row with a "safe" explanation (no thinking)
    row = _sem_row("safe-market")
    sem_repo.upsert(row)

    output_dir = tmp_path / "sem_report_think"
    path = generate_semantic_quality_report(tmp_data_root, output_dir)
    html = path.read_text()
    assert "<think>" not in html.lower()
