from __future__ import annotations

from dataclasses import replace

from click.testing import CliRunner

from polymarket_arb.cli import cli
from polymarket_arb.storage.base import MarketSemanticsRow
from polymarket_arb.storage.parquet.market_semantics_repo import (
    ParquetMarketSemanticsRepository,
)


def _env_for(tmp_path) -> dict[str, str]:
    return {
        "POLYMARKET_ARB_STORAGE__DATA_ROOT": str(tmp_path / "data"),
        "POLYMARKET_ARB_LOGGING__JSON_LOG_PATH": str(tmp_path / "logs" / "test.jsonl"),
    }


def _row() -> MarketSemanticsRow:
    return MarketSemanticsRow(
        source_market_id="m1",
        source_condition_id="0xc",
        question="Will X happen by June 2028?",
        canonical_question="Will X happen by June 2028?",
        market_type="binary",
        subject_entities=[],
        event_entities=[],
        temporal_phrase="by June 2028",
        temporal_phrase_normalized=None,
        temporal_resolution="month",
        exact_deadline_ms=None,
        date_constraints_json="{}",
        jurisdiction=None,
        positive_resolution_condition="Yes if X happens",
        negative_resolution_condition="No otherwise",
        necessary_conditions_for_yes=[],
        sufficient_conditions_for_yes=[],
        necessary_conditions_for_no=[],
        sufficient_conditions_for_no=[],
        evidence_required=[],
        ambiguity_flags=[],
        ambiguity_score=None,
        semantic_confidence=0.4,
        needs_manual_review=False,
        explanation_summary=None,
        flag_rationales_json=None,
        uncertainty_notes_json=None,
        rule_curation_notes_json=None,
        raw_response_hash="r" * 64,
        model_name="mock",
        prompt_version="market_semantics_v1",
        rulebook_id=None,
        rulebook_version=None,
        extraction_id="e1",
        schema_version=1,
        ingested_ts_ms=1,
    )


def test_score_semantics_cli_scores_latest_rows(tmp_path):
    env = _env_for(tmp_path)
    data_root = tmp_path / "data"
    repo = ParquetMarketSemanticsRepository(data_root)
    repo.upsert(_row())

    result = CliRunner().invoke(cli, ["nlp", "score-semantics", "--limit", "10"], env=env)
    assert result.exit_code == 0, result.output
    assert "scored 1 semantics rows" in result.output

    scored = repo.get_latest("m1")
    assert scored is not None
    assert scored.rulebook_id == "ambiguity"
    assert scored.rulebook_version == 1
    assert scored.ambiguity_score is not None


def test_extract_and_show_implications_cli(tmp_path):
    env = _env_for(tmp_path)
    data_root = tmp_path / "data"
    sem_repo = ParquetMarketSemanticsRepository(data_root)
    sem_repo.upsert(
        replace(
            _row(),
            sufficient_conditions_for_yes=["X happens before deadline"],
            ambiguity_score=0.2,
            rulebook_id="semantic_ambiguity",
            rulebook_version=1,
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["nlp", "extract-implications", "--limit", "10"], env=env)
    assert result.exit_code == 0, result.output
    assert "extracted 1 market implications" in result.output

    result = runner.invoke(cli, ["nlp", "show-implications", "m1"], env=env)
    assert result.exit_code == 0, result.output
    assert "sufficient_for_yes" in result.output
    assert "X happens before deadline" in result.output
