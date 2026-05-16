"""Tests for the threshold ladder expansion pass.

From the brief:
  - BTC > 200k by Dec 31 → BTC > 150k by Dec 31 ✓
  - BTC > 200k by June ≠> BTC > 100k by December unless same date ✓ (guarded by deadline)
  - BTC > 200k ≠> ETH > 150k (different entity) ✓
  - below 2% implies below 4%; below 4% ≠> below 2% ✓
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_arb.relationships.expansion.threshold_ladders import run_threshold_ladder_expansion
from polymarket_arb.relationships.threshold_extraction import ThresholdClaim, detect_threshold_nesting
from polymarket_arb.storage.base import MarketRow, MarketSemanticsRow
from polymarket_arb.storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository

_TS = 1_700_000_000_000
_JAN_2026 = 1767225600000  # approx 2026-01-01 in ms
_JUN_2026 = 1767225600000 + 180 * 24 * 3600 * 1000  # approx Jun 2026
_DEC_2026 = 1767225600000 + 365 * 24 * 3600 * 1000  # approx Dec 2026


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


def _sem(mid: str, question: str, deadline_ms: int | None = None) -> MarketSemanticsRow:
    return MarketSemanticsRow(
        source_market_id=mid, source_condition_id=f"c_{mid}",
        question=question, canonical_question=question,
        market_type="binary",
        subject_entities=[], event_entities=[],
        temporal_phrase=None, temporal_phrase_normalized=None,
        temporal_resolution="date",
        exact_deadline_ms=deadline_ms,
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


# ── detect_threshold_nesting guard tests (from existing module) ───────────────

def test_btc_200k_implies_150k_same_deadline():
    a = ThresholdClaim("btc_price", ">", Decimal("200000"), "usd", _JAN_2026, "BTC > 200k")
    b = ThresholdClaim("btc_price", ">", Decimal("150000"), "usd", _JAN_2026, "BTC > 150k")
    assert detect_threshold_nesting(a, b) == "a_implies_b"

def test_btc_200k_does_not_imply_eth_150k():
    a = ThresholdClaim("btc_price", ">", Decimal("200000"), "usd", _JAN_2026, "BTC > 200k")
    b = ThresholdClaim("eth_price", ">", Decimal("150000"), "usd", _JAN_2026, "ETH > 150k")
    assert detect_threshold_nesting(a, b) == "none"

def test_btc_200k_by_june_no_imply_100k_by_december():
    a = ThresholdClaim("btc_price", ">", Decimal("200000"), "usd", _JUN_2026, "BTC > 200k by June")
    b = ThresholdClaim("btc_price", ">", Decimal("100000"), "usd", _DEC_2026, "BTC > 100k by Dec")
    # Deadlines are > 2 weeks apart → "none" per detect_threshold_nesting
    assert detect_threshold_nesting(a, b) == "none"

def test_below_2pct_implies_below_4pct():
    a = ThresholdClaim("cpi", "<", Decimal("2"), "pct", None, "CPI < 2%")
    b = ThresholdClaim("cpi", "<", Decimal("4"), "pct", None, "CPI < 4%")
    assert detect_threshold_nesting(a, b) == "a_implies_b"

def test_below_4pct_does_not_imply_below_2pct():
    a = ThresholdClaim("cpi", "<", Decimal("4"), "pct", None, "CPI < 4%")
    b = ThresholdClaim("cpi", "<", Decimal("2"), "pct", None, "CPI < 2%")
    assert detect_threshold_nesting(a, b) == "b_implies_a"  # b < a → b implies a

def test_mixed_direction_no_nesting():
    a = ThresholdClaim("btc_price", ">", Decimal("200000"), "usd", None, "BTC > 200k")
    b = ThresholdClaim("btc_price", "<", Decimal("150000"), "usd", None, "BTC < 150k")
    assert detect_threshold_nesting(a, b) == "none"


# ── integration: run_threshold_ladder_expansion ───────────────────────────────

def test_expansion_btc_same_deadline_emits_pair(tmp_data_root):
    """BTC > 200k and BTC > 150k with the same deadline → one pair."""
    markets = [_mkt("m_200k"), _mkt("m_150k")]
    sems = [
        _sem("m_200k", "Will BTC exceed $200,000 by end of year?", _JAN_2026),
        _sem("m_150k", "Will BTC reach $150,000 by end of year?", _JAN_2026),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    result = run_threshold_ladder_expansion(tmp_data_root, dry_run=True)
    assert result.emitted_count >= 1


def test_expansion_btc_different_deadlines_no_pair(tmp_data_root):
    """BTC markets with deadlines > 2 weeks apart → zero pairs (deadline mismatch)."""
    markets = [_mkt("m_200k_jun"), _mkt("m_100k_dec")]
    sems = [
        _sem("m_200k_jun", "Will BTC exceed $200,000 by June?", _JUN_2026),
        _sem("m_100k_dec", "Will BTC reach $100,000 by December?", _DEC_2026),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    result = run_threshold_ladder_expansion(tmp_data_root, dry_run=True)
    assert result.emitted_count == 0


def test_expansion_different_asset_no_pair(tmp_data_root):
    """BTC and ETH threshold markets → zero pairs (different variable)."""
    markets = [_mkt("m_btc"), _mkt("m_eth")]
    sems = [
        _sem("m_btc", "Will BTC exceed $200,000 by end of year?", _JAN_2026),
        _sem("m_eth", "Will ETH reach $10,000 by end of year?", _JAN_2026),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    result = run_threshold_ladder_expansion(tmp_data_root, dry_run=True)
    assert result.emitted_count == 0


def test_expansion_no_deadline_still_emits_with_lower_confidence(tmp_data_root):
    """BTC markets with no deadline → emitted as needs_manual_review."""
    markets = [_mkt("m_200k_nd"), _mkt("m_150k_nd")]
    sems = [
        _sem("m_200k_nd", "Will BTC exceed $200,000?", None),
        _sem("m_150k_nd", "Will BTC reach $150,000?", None),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    result = run_threshold_ladder_expansion(tmp_data_root, dry_run=True)
    assert result.emitted_count >= 1
    assert result.needs_review_count >= 1


def test_expansion_dry_run_does_not_write(tmp_data_root):
    markets = [_mkt("m_a"), _mkt("m_b")]
    sems = [
        _sem("m_a", "Will BTC exceed $200,000 by end of year?", _JAN_2026),
        _sem("m_b", "Will BTC reach $150,000 by end of year?", _JAN_2026),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    before = sum(1 for _ in ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())
    run_threshold_ladder_expansion(tmp_data_root, dry_run=True)
    after = sum(1 for _ in ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())
    assert before == after


def test_expansion_commit_writes_rows(tmp_data_root):
    markets = [_mkt("m_c"), _mkt("m_d")]
    sems = [
        _sem("m_c", "Will BTC exceed $200,000 by end of year?", _JAN_2026),
        _sem("m_d", "Will BTC reach $150,000 by end of year?", _JAN_2026),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    before = sum(1 for _ in ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())
    result = run_threshold_ladder_expansion(tmp_data_root, dry_run=False)
    after = sum(1 for _ in ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())
    assert after == before + result.emitted_count


def test_expansion_deduplicates_on_second_run(tmp_data_root):
    markets = [_mkt("m_e"), _mkt("m_f")]
    sems = [
        _sem("m_e", "Will BTC exceed $200,000 by end of year?", _JAN_2026),
        _sem("m_f", "Will BTC reach $150,000 by end of year?", _JAN_2026),
    ]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    r1 = run_threshold_ladder_expansion(tmp_data_root, dry_run=False)
    r2 = run_threshold_ladder_expansion(tmp_data_root, dry_run=False)
    assert r2.emitted_count == 0
    assert r2.skipped_existing == r1.emitted_count
