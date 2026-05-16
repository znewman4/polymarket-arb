"""Phase E election ladder expansion tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from polymarket_arb.relationships.expansion.election_ladders import (
    run_election_ladder_expansion,
)
from polymarket_arb.storage.base import MarketRow, MarketSemanticsRow
from polymarket_arb.storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _market(market_id: str, question: str) -> MarketRow:
    return MarketRow(
        id=market_id,
        condition_id=f"cond_{market_id}",
        slug=market_id,
        question=question,
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
        clob_token_ids=[f"{market_id}_yes", f"{market_id}_no"],
        volume=None,
        liquidity=None,
        event_id=None,
        neg_risk=False,
        text_hash=f"hash_{market_id}",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _sem(market_id: str, question: str) -> MarketSemanticsRow:
    return MarketSemanticsRow(
        source_market_id=market_id,
        source_condition_id=f"cond_{market_id}",
        question=question,
        canonical_question=question,
        market_type="binary",
        subject_entities=[],
        event_entities=[],
        temporal_phrase=None,
        temporal_phrase_normalized=None,
        temporal_resolution="vague",
        exact_deadline_ms=None,
        date_constraints_json="{}",
        jurisdiction=None,
        positive_resolution_condition="yes",
        negative_resolution_condition="no",
        necessary_conditions_for_yes=[],
        sufficient_conditions_for_yes=[],
        necessary_conditions_for_no=[],
        sufficient_conditions_for_no=[],
        evidence_required=[],
        ambiguity_flags=[],
        ambiguity_score=None,
        semantic_confidence=0.9,
        needs_manual_review=False,
        explanation_summary=None,
        flag_rationales_json=None,
        uncertainty_notes_json=None,
        rule_curation_notes_json=None,
        raw_response_hash=f"hash_{market_id}",
        model_name="test",
        prompt_version="market_semantics_v2",
        rulebook_id=None,
        rulebook_version=None,
        extraction_id=f"ext_{market_id}",
        terms_confidence=0.9,
        schema_version=2,
        ingested_ts_ms=TS,
    )


def _write_markets(tmp_data_root, rows: list[MarketRow]) -> None:
    ParquetMarketsRepository(tmp_data_root).upsert_markets(rows)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(
        [_sem(row.id, row.question) for row in rows]
    )


def test_general_election_candidates_emit_pair(tmp_data_root):
    rows = [
        _market("m_a", "Will LeBron James win the 2028 US Presidential Election?"),
        _market("m_b", "Will JD Vance win the 2028 US Presidential Election?"),
    ]
    _write_markets(tmp_data_root, rows)

    result = run_election_ladder_expansion(tmp_data_root, dry_run=True)

    assert result.emitted_count == 1
    audit = result.audit_rows[0]
    assert audit["relationship_subtype"] == "primary_candidates_mutually_exclusive"


def test_nomination_candidates_same_party_emit_pair(tmp_data_root):
    rows = [
        _market("m_a", "Will Gretchen Whitmer win the 2028 Democratic presidential nomination?"),
        _market("m_b", "Will Pete Buttigieg win the 2028 Democratic presidential nomination?"),
    ]
    _write_markets(tmp_data_root, rows)

    result = run_election_ladder_expansion(tmp_data_root, dry_run=False)
    stored = list(ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())

    assert result.emitted_count == 1
    assert len(stored) == 1
    row = stored[0]
    assert row.relationship_type == "mutually_exclusive_category"
    assert row.outcome_subtype_a == row.outcome_subtype_b == "candidate_wins_nomination"
    assert row.party_a == row.party_b == "Democrats"
    assert row.shared_event == "2028_democrats_presidential_nomination"
    evidence = json.loads(row.evidence_json)
    assert evidence["generated_by"] == "deterministic_expansion"
    assert evidence["expansion_pass_id"] == "election_ladders_v1"


def test_different_nomination_parties_do_not_emit(tmp_data_root):
    rows = [
        _market("m_a", "Will Marco Rubio win the 2028 Republican presidential nomination?"),
        _market("m_b", "Will Pete Buttigieg win the 2028 Democratic presidential nomination?"),
    ]
    _write_markets(tmp_data_root, rows)

    result = run_election_ladder_expansion(tmp_data_root, dry_run=True)

    assert result.emitted_count == 0


def test_nomination_to_general_same_candidate_analysis_only(tmp_data_root):
    rows = [
        _market("m_nom", "Will Marco Rubio win the 2028 Republican presidential nomination?"),
        _market("m_gen", "Will Marco Rubio win the 2028 US Presidential Election?"),
    ]
    _write_markets(tmp_data_root, rows)

    result = run_election_ladder_expansion(tmp_data_root, dry_run=True)

    assert result.emitted_count == 0
    assert any(
        row.get("note") == "analysis_only_cross_stage_dependency"
        for row in result.audit_rows
    )


def test_replacement_or_withdrawal_ambiguity_blocks_auto_emit(tmp_data_root):
    rows = [
        _market("m_a", "Will Candidate A win the 2028 US Presidential Election after replacing the nominee?"),
        _market("m_b", "Will Candidate B win the 2028 US Presidential Election after replacing the nominee?"),
    ]
    _write_markets(tmp_data_root, rows)

    result = run_election_ladder_expansion(tmp_data_root, dry_run=True)

    assert result.emitted_count == 0
    assert result.guard_failure_counts["replacement_or_withdrawal_ambiguity"] >= 1


def test_commit_deduplicates_on_second_run(tmp_data_root):
    rows = [
        _market("m_a", "Will LeBron James win the 2028 US Presidential Election?"),
        _market("m_b", "Will JD Vance win the 2028 US Presidential Election?"),
    ]
    _write_markets(tmp_data_root, rows)

    first = run_election_ladder_expansion(tmp_data_root, dry_run=False)
    second = run_election_ladder_expansion(tmp_data_root, dry_run=False)

    assert first.emitted_count == 1
    assert second.emitted_count == 0
    assert second.skipped_existing == 1
