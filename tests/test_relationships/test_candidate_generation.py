"""Tests for candidate pair generation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from polymarket_arb.relationships.candidate_generation import (
    RelationshipMiningConfig,
    generate_candidate_pairs,
)
from polymarket_arb.storage.base import MarketRow, MarketSemanticsRow

_TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _market(
    market_id: str,
    event_id: str | None = None,
    entities: list[str] | None = None,
    question: str | None = None,
) -> MarketRow:
    return MarketRow(
        id=market_id,
        condition_id=f"cond_{market_id}",
        slug=market_id,
        question=question or f"Question for {market_id}?",
        description=None,
        end_date_ms=_TS + 30 * 24 * 3600 * 1000,
        start_date_ms=_TS,
        closed_at_ms=None,
        resolved_at_ms=None,
        active=True,
        closed=False,
        archived=False,
        outcomes=["Yes", "No"],
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=[f"tok_{market_id}_yes", f"tok_{market_id}_no"],
        volume=None,
        liquidity=None,
        event_id=event_id,
        neg_risk=False,
        text_hash="abc",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _semantics(market_id: str, entities: list[str]) -> MarketSemanticsRow:
    return MarketSemanticsRow(
        source_market_id=market_id,
        source_condition_id=None,
        question="",
        canonical_question="",
        market_type="binary",
        subject_entities=entities,
        event_entities=entities,
        temporal_phrase=None,
        temporal_phrase_normalized=None,
        temporal_resolution="date",
        exact_deadline_ms=None,
        date_constraints_json="{}",
        jurisdiction=None,
        positive_resolution_condition="resolves yes",
        negative_resolution_condition="resolves no",
        necessary_conditions_for_yes=[],
        sufficient_conditions_for_yes=[],
        necessary_conditions_for_no=[],
        sufficient_conditions_for_no=[],
        evidence_required=[],
        ambiguity_flags=[],
        ambiguity_score=0.1,
        semantic_confidence=0.9,
        needs_manual_review=False,
        explanation_summary=None,
        flag_rationales_json=None,
        uncertainty_notes_json=None,
        rule_curation_notes_json=None,
        raw_response_hash="abc",
        model_name="test",
        prompt_version="v1",
        rulebook_id=None,
        rulebook_version=None,
        extraction_id=f"ext_{market_id}",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def test_same_event_id_generates_pair():
    m1 = _market("m1", event_id="ev1")
    m2 = _market("m2", event_id="ev1")
    m3 = _market("m3", event_id="ev2")  # different event

    pairs = list(generate_candidate_pairs(
        [m1, m2, m3],
        semantics_by_market={},
        events_by_market={"m1": "ev1", "m2": "ev1", "m3": "ev2"},
        cfg=RelationshipMiningConfig(),
    ))
    # m1+m2 should be paired (same event), not m1+m3 or m2+m3
    pair_ids = [frozenset({p.market_a.id, p.market_b.id}) for p in pairs]
    assert frozenset({"m1", "m2"}) in pair_ids


def test_entity_overlap_generates_pair():
    m1 = _market("btc_100k")
    m2 = _market("btc_90k")
    sem1 = _semantics("btc_100k", ["Bitcoin", "BTC"])
    sem2 = _semantics("btc_90k", ["Bitcoin", "BTC"])

    pairs = list(generate_candidate_pairs(
        [m1, m2],
        semantics_by_market={"btc_100k": sem1, "btc_90k": sem2},
        events_by_market={"btc_100k": None, "btc_90k": None},
        cfg=RelationshipMiningConfig(),
    ))
    assert any("entity_overlap" in p.sources for p in pairs)


def test_no_self_pairs():
    m1 = _market("m1", event_id="ev1")
    pairs = list(generate_candidate_pairs(
        [m1],
        semantics_by_market={},
        events_by_market={"m1": "ev1"},
        cfg=RelationshipMiningConfig(),
    ))
    assert all(p.market_a.id != p.market_b.id for p in pairs)


def test_total_candidates_cap():
    markets = [_market(f"m{i}", event_id="ev1") for i in range(20)]
    cfg = RelationshipMiningConfig(max_total_candidates=10)
    pairs = list(generate_candidate_pairs(
        markets,
        semantics_by_market={},
        events_by_market={m.id: "ev1" for m in markets},
        cfg=cfg,
    ))
    assert len(pairs) <= 10


def test_no_duplicate_pairs():
    markets = [_market(f"m{i}", event_id="ev1") for i in range(5)]
    pairs = list(generate_candidate_pairs(
        markets,
        semantics_by_market={},
        events_by_market={m.id: "ev1" for m in markets},
    ))
    seen = set()
    for p in pairs:
        key = frozenset({p.market_a.id, p.market_b.id})
        assert key not in seen, f"Duplicate pair: {key}"
        seen.add(key)


def test_no_entities_no_entity_pair():
    """Markets with no semantics shouldn't generate entity-based pairs."""
    m1 = _market("m1")
    m2 = _market("m2")
    pairs = list(generate_candidate_pairs(
        [m1, m2],
        semantics_by_market={},
        events_by_market={"m1": None, "m2": None},
    ))
    assert len(pairs) == 0


def test_outcome_space_cluster_can_use_question_fallback_for_old_semantics():
    m1 = _market(
        "carolina",
        question="Will the Carolina Hurricanes win the 2026 NHL Stanley Cup?",
    )
    m2 = _market(
        "philadelphia",
        question="Will the Philadelphia Flyers win the 2026 NHL Stanley Cup?",
    )
    sem1 = _semantics("carolina", ["Carolina Hurricanes"])
    sem2 = _semantics("philadelphia", ["Philadelphia Flyers"])
    pairs = list(generate_candidate_pairs(
        [m1, m2],
        semantics_by_market={"carolina": sem1, "philadelphia": sem2},
        events_by_market={"carolina": None, "philadelphia": None},
    ))

    assert len(pairs) == 1
    assert pairs[0].generation_source == "mutually_exclusive_category"
    assert "outcome_space_cluster" in pairs[0].sources


def test_proposition_cluster_can_use_before_question_fallback_for_old_semantics():
    m1 = _market("rihanna", question="New Rihanna Album before GTA VI?")
    m2 = _market("bitcoin", question="Will bitcoin hit $1m before GTA VI?")
    sem1 = _semantics("rihanna", ["Rihanna"])
    sem2 = _semantics("bitcoin", ["Bitcoin"])
    pairs = list(generate_candidate_pairs(
        [m1, m2],
        semantics_by_market={"rihanna": sem1, "bitcoin": sem2},
        events_by_market={"rihanna": None, "bitcoin": None},
    ))

    assert len(pairs) == 1
    assert pairs[0].generation_source == "same_reference_clock"
    assert "proposition_cluster" in pairs[0].sources
