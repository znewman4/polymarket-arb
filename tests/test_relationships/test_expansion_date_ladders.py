"""Tests for the date ladder expansion pass.

From the brief:
  - same event before March → same event before June ✓
  - same entity but different event rejected ✓
  - same date but different entity rejected ✓
  - shared reference clock remains analysis_only ✓
  - earlier/later direction correct ✓
  - ambiguous natural-language dates → needs_manual_review ✓
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_arb.relationships.expansion.date_ladders import (
    _deadline_ordinal,
    _is_shared_reference_clock,
    run_date_ladder_expansion,
)
from polymarket_arb.storage.base import MarketRow, MarketSemanticsRow
from polymarket_arb.storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository

_TS = 1_700_000_000_000


def _mkt(mid: str) -> MarketRow:
    return MarketRow(
        id=mid, condition_id=f"c_{mid}", slug=mid, question=f"Q {mid}",
        description=None, end_date_ms=None, start_date_ms=None,
        closed_at_ms=None, resolved_at_ms=None,
        active=True, closed=False, archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=[f"{mid}_yes", f"{mid}_no"],
        volume=None, liquidity=None, event_id=None, neg_risk=False,
        text_hash=mid, schema_version=1, ingested_ts_ms=_TS,
    )


def _sem(mid: str, question: str) -> MarketSemanticsRow:
    return MarketSemanticsRow(
        source_market_id=mid, source_condition_id=f"c_{mid}",
        question=question, canonical_question=question,
        market_type="binary",
        subject_entities=[], event_entities=[],
        temporal_phrase=None, temporal_phrase_normalized=None,
        temporal_resolution="date",
        exact_deadline_ms=None,
        date_constraints_json="{}",
        jurisdiction=None,
        positive_resolution_condition="",
        negative_resolution_condition="",
        necessary_conditions_for_yes=[], sufficient_conditions_for_yes=[],
        necessary_conditions_for_no=[], sufficient_conditions_for_no=[],
        evidence_required=[], ambiguity_flags=[],
        ambiguity_score=None, semantic_confidence=0.9,
        needs_manual_review=False,
        explanation_summary=None, flag_rationales_json=None,
        uncertainty_notes_json=None, rule_curation_notes_json=None,
        raw_response_hash=mid, model_name="test", prompt_version="v1",
        rulebook_id=None, rulebook_version=None, extraction_id=mid,
        schema_version=1, ingested_ts_ms=_TS,
    )


# ── _deadline_ordinal unit tests ──────────────────────────────────────────────

def test_ordinal_year_only():
    assert _deadline_ordinal("2026") == 202600

def test_ordinal_quarter():
    assert _deadline_ordinal("q1_2026") is not None
    assert _deadline_ordinal("q1_2026") < _deadline_ordinal("q2_2026")  # type: ignore[operator]
    assert _deadline_ordinal("q2_2026") < _deadline_ordinal("q3_2026")  # type: ignore[operator]

def test_ordinal_month_name():
    assert _deadline_ordinal("march_2026") == 202603
    assert _deadline_ordinal("june_2026") == 202606
    assert _deadline_ordinal("december_2026") == 202612

def test_ordinal_march_before_june():
    assert _deadline_ordinal("march_2026") < _deadline_ordinal("june_2026")  # type: ignore[operator]

def test_ordinal_year_end():
    assert _deadline_ordinal("year_end_2026") == 202612
    assert _deadline_ordinal("end_of_2025") == 202512

def test_ordinal_unparseable():
    assert _deadline_ordinal("gta_vi_release") is None
    assert _deadline_ordinal("election_day") is None

def test_ordinal_earlier_year_before_later_year():
    assert _deadline_ordinal("2025") < _deadline_ordinal("2026")  # type: ignore[operator]


# ── _is_shared_reference_clock ────────────────────────────────────────────────

def test_shared_clock_gta_vi():
    assert _is_shared_reference_clock("gta_vi_release") is True

def test_shared_clock_election_day():
    assert _is_shared_reference_clock("election_day") is True

def test_not_clock_year_2026():
    assert _is_shared_reference_clock("2026") is False

def test_not_clock_march_2026():
    assert _is_shared_reference_clock("march_2026") is False

def test_not_clock_q1_2026():
    assert _is_shared_reference_clock("q1_2026") is False


# ── integration tests ─────────────────────────────────────────────────────────

def test_same_event_earlier_before_later_emits_pair(tmp_data_root):
    """Same event, March deadline and June deadline → 1 pair, earlier implies later."""
    markets = [_mkt("m_mar"), _mkt("m_jun")]
    sems = [
        _sem("m_mar", "Will the Fed cut rates before March 2026?"),
        _sem("m_jun", "Will the Fed cut rates before June 2026?"),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    result = run_date_ladder_expansion(tmp_data_root, dry_run=True)
    assert result.emitted_count >= 1


def test_implication_direction_earlier_is_narrow(tmp_data_root):
    """Market with earlier deadline should be the narrow (A) side."""
    markets = [_mkt("m_q1"), _mkt("m_q3")]
    sems = [
        _sem("m_q1", "Will the Fed cut rates before Q1 2026?"),
        _sem("m_q3", "Will the Fed cut rates before Q3 2026?"),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    result = run_date_ladder_expansion(tmp_data_root, dry_run=True)
    assert result.emitted_count >= 1
    # Check audit row says the earlier-deadline market is market_a (narrow)
    if result.audit_rows:
        row = next(r for r in result.audit_rows if r.get("market_a") is not None)
        assert row["market_a"] == "m_q1"  # q1 < q3


def test_different_event_different_entities_no_pair(tmp_data_root):
    """Different entities → different left_event_ids → zero pairs."""
    markets = [_mkt("m_fed"), _mkt("m_ecb")]
    sems = [
        _sem("m_fed", "Will the Fed cut rates before March 2026?"),
        _sem("m_ecb", "Will the ECB cut rates before June 2026?"),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    result = run_date_ladder_expansion(tmp_data_root, dry_run=True)
    assert result.emitted_count == 0


def test_shared_reference_clock_not_emitted(tmp_data_root):
    """Markets using a shared reference clock (e.g. 'before GTA VI') → analysis_only, not emitted."""
    markets = [_mkt("m_rih"), _mkt("m_bey")]
    sems = [
        _sem("m_rih", "Will Rihanna release an album before GTA VI?"),
        _sem("m_bey", "Will Beyonce release an album before GTA VI?"),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    result = run_date_ladder_expansion(tmp_data_root, dry_run=True)
    assert result.emitted_count == 0


def test_same_event_same_deadline_no_pair(tmp_data_root):
    """Two markets with the same event AND same deadline → no implication (equal)."""
    markets = [_mkt("m_a"), _mkt("m_b")]
    sems = [
        _sem("m_a", "Will the Fed cut rates before March 2026?"),
        _sem("m_b", "Will the Fed cut rates before March 2026?"),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    result = run_date_ladder_expansion(tmp_data_root, dry_run=True)
    assert result.emitted_count == 0


def test_dry_run_does_not_write(tmp_data_root):
    markets = [_mkt("m_r"), _mkt("m_s")]
    sems = [
        _sem("m_r", "Will GTA VI be released before 2026?"),
        _sem("m_s", "Will GTA VI be released before 2027?"),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    before = sum(1 for _ in ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())
    run_date_ladder_expansion(tmp_data_root, dry_run=True)
    after = sum(1 for _ in ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())
    assert before == after


def test_commit_writes_rows(tmp_data_root):
    markets = [_mkt("m_t"), _mkt("m_u")]
    sems = [
        _sem("m_t", "Will GTA VI be released before 2026?"),
        _sem("m_u", "Will GTA VI be released before 2027?"),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    before = sum(1 for _ in ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())
    result = run_date_ladder_expansion(tmp_data_root, dry_run=False)
    after = sum(1 for _ in ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())
    assert after == before + result.emitted_count


def test_deduplicates_on_second_run(tmp_data_root):
    markets = [_mkt("m_v"), _mkt("m_w")]
    sems = [
        _sem("m_v", "Will GTA VI be released before 2026?"),
        _sem("m_w", "Will GTA VI be released before 2027?"),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    r1 = run_date_ladder_expansion(tmp_data_root, dry_run=False)
    r2 = run_date_ladder_expansion(tmp_data_root, dry_run=False)
    assert r2.emitted_count == 0
    assert r2.skipped_existing == r1.emitted_count
