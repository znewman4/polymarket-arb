"""Tests for the sports progression (stage ordering) expansion pass."""

from __future__ import annotations

from decimal import Decimal

from polymarket_arb.relationships.expansion.sports_progression import (
    _competition_family,
    run_sports_progression_expansion,
)
from polymarket_arb.storage.base import MarketRow, MarketSemanticsRow
from polymarket_arb.storage.parquet.market_semantics_repo import ParquetMarketSemanticsRepository
from polymarket_arb.storage.parquet.markets_repo import ParquetMarketsRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

_TS = 1_700_000_000_000


# ── _competition_family ───────────────────────────────────────────────────────

def test_competition_family_nba():
    assert _competition_family("nba_finals") == "nba"
    assert _competition_family("nba_eastern_conference_winner") == "nba"

def test_competition_family_nhl():
    assert _competition_family("nhl_stanley_cup") == "nhl"
    assert _competition_family("stanley_cup_finals") == "nhl"

def test_competition_family_nfl():
    assert _competition_family("nfl_super_bowl") == "nfl"

def test_competition_family_premier_league():
    assert _competition_family("premier_league_champion") == "premier_league"
    assert _competition_family("epl_finish_position") == "premier_league"

def test_competition_family_unknown():
    assert _competition_family("random_competition_xyz") is None


# ── stage registry YAML ───────────────────────────────────────────────────────

def test_stage_ordering_yaml_exists():
    from polymarket_arb.relationships.expansion.sports_progression import _STAGE_REGISTRY_PATH
    assert _STAGE_REGISTRY_PATH.exists(), f"Stage ordering YAML not found at {_STAGE_REGISTRY_PATH}"


def test_stage_ordering_yaml_valid():
    import yaml

    from polymarket_arb.relationships.expansion.sports_progression import _STAGE_REGISTRY_PATH
    data = yaml.safe_load(_STAGE_REGISTRY_PATH.read_text(encoding="utf-8"))
    assert "competition_families" in data
    families = data["competition_families"]
    assert "nba" in families
    assert "nhl" in families


def test_nba_championship_implies_conference():
    """NBA registry must permit championship→conference implication."""
    import yaml

    from polymarket_arb.relationships.expansion.sports_progression import _STAGE_REGISTRY_PATH
    data = yaml.safe_load(_STAGE_REGISTRY_PATH.read_text(encoding="utf-8"))
    nba = data["competition_families"]["nba"]
    stages = {s["stage_id"]: s for s in nba.get("stages", [])}

    champion_stage = stages.get("nba_champion")
    assert champion_stage is not None
    assert champion_stage.get("implication_allowed") is True
    assert "nba_conference_winner" in champion_stage.get("prerequisite_stage_ids", [])


def test_premier_league_champion_no_cross_competition_implication():
    """Premier League champion should NOT imply Champions League participation via the registry."""
    import yaml

    from polymarket_arb.relationships.expansion.sports_progression import _STAGE_REGISTRY_PATH
    data = yaml.safe_load(_STAGE_REGISTRY_PATH.read_text(encoding="utf-8"))
    pl = data["competition_families"]["premier_league"]
    stages = {s["stage_id"]: s for s in pl.get("stages", [])}
    pl_champ = stages.get("premier_league_champion")
    if pl_champ:
        assert pl_champ.get("implication_allowed") is False or not pl_champ.get("prerequisite_stage_ids")


# ── integration tests ─────────────────────────────────────────────────────────

def _mkt(mid: str, question: str) -> MarketRow:
    return MarketRow(
        id=mid,
        condition_id=f"cond_{mid}",
        slug=mid,
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
        clob_token_ids=[f"{mid}_yes", f"{mid}_no"],
        volume=None,
        liquidity=None,
        event_id=None,
        neg_risk=False,
        text_hash=mid,
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _sem(mid: str, question: str) -> MarketSemanticsRow:
    return MarketSemanticsRow(
        source_market_id=mid,
        source_condition_id=f"cond_{mid}",
        question=question,
        canonical_question=question,
        market_type="binary",
        subject_entities=[],
        event_entities=[],
        temporal_phrase=None,
        temporal_phrase_normalized=None,
        temporal_resolution="end_of_event",
        exact_deadline_ms=None,
        date_constraints_json="{}",
        jurisdiction=None,
        positive_resolution_condition="Yes",
        negative_resolution_condition="No",
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
        raw_response_hash=mid,
        model_name="test",
        prompt_version="v1",
        rulebook_id=None,
        rulebook_version=None,
        extraction_id=mid,
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def test_progression_nba_champion_implies_conference(tmp_data_root):
    """NBA champion → conference winner pair emitted when both markets present."""
    markets = [
        _mkt("m_champ", "Will the Oklahoma City Thunder win the 2026 NBA Finals?"),
        _mkt("m_conf", "Will Oklahoma City Thunder win the NBA Western Conference?"),
    ]
    sems = [_sem(m.id, m.question) for m in markets]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    result = run_sports_progression_expansion(tmp_data_root, dry_run=True)
    assert result.emitted_count >= 1
    # Check the audit row subtype
    if result.audit_rows:
        assert any("championship_implies_conference" in str(row) for row in result.audit_rows)


def test_progression_different_years_rejected(tmp_data_root):
    """Different-year championship/conference markets produce zero pairs."""
    markets = [
        _mkt("m_champ25", "Will Oklahoma City Thunder win the 2025 NBA Finals?"),
        _mkt("m_conf26", "Will Oklahoma City Thunder win the 2026 NBA Western Conference?"),
    ]
    sems = [_sem(m.id, m.question) for m in markets]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    result = run_sports_progression_expansion(tmp_data_root, dry_run=True)
    assert result.emitted_count == 0


def test_progression_dry_run_does_not_write(tmp_data_root):
    markets = [
        _mkt("m_champ", "Will Oklahoma City Thunder win the 2026 NBA Finals?"),
        _mkt("m_conf", "Will Oklahoma City Thunder win the 2026 NBA Western Conference?"),
    ]
    sems = [_sem(m.id, m.question) for m in markets]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    before = sum(1 for _ in ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())
    run_sports_progression_expansion(tmp_data_root, dry_run=True)
    after = sum(1 for _ in ParquetRelationshipCandidatesRepository(tmp_data_root).iter_latest())
    assert before == after


def test_progression_deduplicates_on_second_run(tmp_data_root):
    markets = [
        _mkt("m_champ", "Will Oklahoma City Thunder win the 2026 NBA Finals?"),
        _mkt("m_conf", "Will Oklahoma City Thunder win the 2026 NBA Western Conference?"),
    ]
    sems = [_sem(m.id, m.question) for m in markets]
    ParquetMarketsRepository(tmp_data_root).upsert_markets(markets)
    ParquetMarketSemanticsRepository(tmp_data_root).upsert_many(sems)

    r1 = run_sports_progression_expansion(tmp_data_root, dry_run=False)
    r2 = run_sports_progression_expansion(tmp_data_root, dry_run=False)
    assert r2.emitted_count == 0
    assert r2.skipped_existing == r1.emitted_count
