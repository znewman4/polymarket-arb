"""Strict taxonomy regression tests for known semantic relationship failures."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from polymarket_arb.backtest.price_alignment import align_price_series
from polymarket_arb.relationships.candidate_generation import CandidatePair
from polymarket_arb.relationships.taxonomy import classify_relationship
from polymarket_arb.relationships.validators import validate_all_pairs
from polymarket_arb.semantics.rulebook import load_rulebook
from polymarket_arb.storage.base import (
    BackfillCoverageRow,
    MarketRow,
    MarketSemanticsRow,
    PriceHistoryRow,
)
from polymarket_arb.strategies.category_bundle_scanner import (
    CategoryPricePoint,
    scan_category_bundle,
)
from polymarket_arb.strategies.category_outcome_spaces import (
    CategoryCandidate,
    CategoryOutcomeSpace,
)

_TS = int(datetime.now(timezone.utc).timestamp() * 1000)


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
        ingested_ts_ms=_TS,
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
        semantic_confidence=0.85,
        needs_manual_review=False,
        explanation_summary=None,
        flag_rationales_json=None,
        uncertainty_notes_json=None,
        rule_curation_notes_json=None,
        raw_response_hash="hash",
        model_name="test",
        prompt_version="market_semantics_v2",
        rulebook_id=None,
        rulebook_version=None,
        extraction_id=f"ext_{market_id}",
        terms_confidence=0.85,
        schema_version=2,
        ingested_ts_ms=_TS,
    )


def _coverage(market_id: str) -> BackfillCoverageRow:
    return BackfillCoverageRow(
        market_id=market_id,
        condition_id=f"cond_{market_id}",
        question="q",
        start_ts_ms=_TS - 1000,
        end_ts_ms=_TS,
        requested_days=1,
        has_gamma=True,
        has_price_history=True,
        has_trade_history=False,
        has_semantics=True,
        has_rulebook_score=False,
        has_implications=False,
        has_embeddings=False,
        has_backfill_coverage=True,
        price_points_count=10,
        trade_points_count=0,
        first_price_ts_ms=_TS - 1000,
        last_price_ts_ms=_TS,
        missing_price_gap_count=0,
        largest_price_gap_ms=100,
        price_min=Decimal("0.1"),
        price_max=Decimal("0.9"),
        price_out_of_bounds_count=0,
        duplicate_timestamp_count=0,
        coverage_score=0.9,
        recommended_for_backtest=True,
        exclusion_reasons_json="[]",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _validate(q_a: str, q_b: str, *, source: str = "entity_overlap"):
    rulebook = load_rulebook(Path("configs/semantic_rules/relationship_v2.yaml"), kind="relationship")
    a = _market("a", q_a)
    b = _market("b", q_b)
    rows = list(validate_all_pairs(
        [CandidatePair(a, b, ["test"], source)],
        {"a": _sem("a", q_a), "b": _sem("b", q_b)},
        {"a": _coverage("a"), "b": _coverage("b")},
        rulebook,
        "hash",
    ))
    return rows[0]


def test_candidate_party_not_mutually_exclusive():
    row = _validate(
        "Will LeBron James win the 2028 US Presidential Election?",
        "Will the Democrats win the 2028 US Presidential Election?",
        source="mutually_exclusive_category",
    )
    assert row.relationship_family in {"mixed_subtype", "dependency", "rejected"}
    assert row.relationship_subtype in {"candidate_to_party_dependency", "mixed_candidate_party"}
    assert row.relationship_type != "mutually_exclusive_category"
    assert row.strategy_eligibility_status == "ineligible"


def test_two_candidates_same_election_mutually_exclusive():
    row = _validate(
        "Will LeBron James win the 2028 US Presidential Election?",
        "Will JD Vance win the 2028 US Presidential Election?",
        source="mutually_exclusive_category",
    )
    assert row.outcome_space_id == "2028_us_presidential_election_candidate_winner"
    assert row.outcome_subtype_a == row.outcome_subtype_b == "candidate_winner_same_election"
    assert row.relationship_type == "mutually_exclusive_category"
    assert row.strategy_family == "pairwise_mutual_exclusion"


def test_two_parties_same_election_inverse():
    row = _validate(
        "Will Democrats win the 2028 US Presidential Election?",
        "Will Republicans win the 2028 US Presidential Election?",
        source="mutually_exclusive_category",
    )
    assert row.outcome_subtype_a == row.outcome_subtype_b == "party_winner_same_election"
    assert row.relationship_type == "inverse"
    assert row.strategy_family == "party_inverse"


def test_nomination_to_general_not_contradiction():
    row = _validate(
        "Will Marco Rubio win the 2028 Republican presidential nomination?",
        "Will Marco Rubio win the 2028 US Presidential Election?",
    )
    assert row.relationship_subtype == "nomination_to_general_dependency"
    assert row.relationship_type != "contradiction"
    assert row.strategy_eligibility_status == "ineligible"


def test_first_round_to_general_not_contradiction():
    tax = classify_relationship(
        "Will Gustavo Petro win 1st round of the Colombian presidential election?",
        "Will Gustavo Petro win the Colombian presidential election?",
    )
    assert tax.relationship_subtype == "first_round_to_general_dependency"
    assert tax.relationship_family == "dependency"


def test_championship_implies_conference():
    tax = classify_relationship(
        "Will Oklahoma City Thunder win NBA Finals?",
        "Will Oklahoma City Thunder win Western Conference Finals?",
    )
    assert tax.relationship_family == "nesting"
    assert tax.relationship_subtype == "championship_implies_conference"


def test_exact_finish_implies_top_n():
    tax = classify_relationship("Will Liverpool finish 3rd?", "Will Liverpool finish top 4?")
    assert tax.relationship_family == "nesting"
    assert tax.relationship_subtype == "exact_finish_implies_top_n"


def test_same_reference_clock_only_not_tradable():
    row = _validate(
        "New Rihanna Album before GTA VI?",
        "Will bitcoin hit $1m before GTA VI?",
        source="same_reference_clock",
    )
    assert row.relationship_subtype == "same_reference_clock_only"
    assert row.strategy_eligibility_status == "ineligible"


def test_same_person_dem_nomination_vs_rep_nomination_manual_review():
    row = _validate(
        "Will Kim Kardashian win the 2028 Democratic presidential nomination?",
        "Will Kim Kardashian win the 2028 Republican presidential nomination?",
    )
    assert row.relationship_subtype == "mixed_party_nomination"
    assert row.relationship_type != "mutually_exclusive_category"


def test_category_bundle_incomplete_not_simulated():
    space = CategoryOutcomeSpace(
        outcome_space_id="2028_democratic_nomination",
        display_name="2028 Democratic nomination",
        candidates=tuple(
            CategoryCandidate(f"m{i}", f"Candidate {i}", "q", f"yes{i}", f"no{i}") for i in range(19)
        ),
        known_total_candidates=None,
        completeness_policy="incomplete_unknown",
        allow_bundle_backtest=False,
    )
    row = scan_category_bundle(space, yes_prices={}, no_prices={}, min_net_edge=Decimal("0.01"), fee_bps=Decimal("0"), slippage_bps=Decimal("50"))
    assert row.strategy_allowed is False
    assert row.rejection_reason == "missing_price_history"


def test_complete_registry_required_for_bundle_backtest():
    candidates = tuple(
        CategoryCandidate(f"m{i}", name, "q", f"yes{i}", f"no{i}")
        for i, name in enumerate(["Democrats", "Republicans"])
    )
    space = CategoryOutcomeSpace(
        outcome_space_id="2028_us_presidential_party_winner",
        display_name="2028 US party winner",
        candidates=candidates,
        known_total_candidates=2,
        completeness_policy="complete_if_required_options_present",
        allow_bundle_backtest=True,
        completeness_reason="registry completeness policy satisfied",
    )
    yes = {c.yes_token_id: CategoryPricePoint(c.yes_token_id, Decimal("0.4"), _TS) for c in candidates}
    no = {c.no_token_id: CategoryPricePoint(c.no_token_id, Decimal("0.6"), _TS) for c in candidates}
    row = scan_category_bundle(space, yes_prices=yes, no_prices=no, min_net_edge=Decimal("0.01"), fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
    assert row.strategy_allowed is True


def test_no_lookahead_after_classifier_changes():
    rows_a = [
        PriceHistoryRow("a", "ca", "ta", "Yes", 1000, Decimal("0.2"), "clob", "1h", "1h", 1, _TS),
        PriceHistoryRow("a", "ca", "ta", "Yes", 3000, Decimal("0.9"), "clob", "1h", "1h", 1, _TS),
    ]
    rows_b = [PriceHistoryRow("b", "cb", "tb", "Yes", 1000, Decimal("0.3"), "clob", "1h", "1h", 1, _TS)]
    aligned = align_price_series(rows_a, rows_b, start_ts_ms=2000, end_ts_ms=2000, signal_interval_ms=1000)
    assert aligned[0].price_a == Decimal("0.2")
    assert aligned[0].price_a_ts_ms == 1000


def test_no_short_selling():
    from polymarket_arb.strategies.nesting_contradiction import (
        AlignedPricePoint,
        evaluate_relationship_at_tick,
    )

    row = _validate(
        "Will LeBron James win the 2028 US Presidential Election?",
        "Will JD Vance win the 2028 US Presidential Election?",
        source="mutually_exclusive_category",
    )
    candidate = evaluate_relationship_at_tick(
        row,
        AlignedPricePoint(_TS, Decimal("0.7"), Decimal("0.6"), _TS, _TS),
        "run",
        0.01,
        Decimal("0"),
        Decimal("0"),
        0.01,
    )
    position = json.loads(candidate.simulated_position_json)
    assert {leg["side"] for leg in position} == {"buy"}
