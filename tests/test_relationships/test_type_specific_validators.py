"""Tests for Phase 5.5 D+ type-specific relationship validators."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from polymarket_arb.relationships.candidate_generation import CandidatePair
from polymarket_arb.relationships.validators import validate_all_pairs
from polymarket_arb.semantics.rulebook import load_rulebook
from polymarket_arb.storage.base import (
    BackfillCoverageRow,
    MarketRow,
    MarketSemanticsRow,
)

_TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _find_rulebook_v2() -> Path:
    p = Path(__file__).parent
    for _ in range(6):
        rb = p / "configs" / "semantic_rules" / "relationship_v2.yaml"
        if rb.exists():
            return rb
        p = p.parent
    raise FileNotFoundError("relationship_v2.yaml not found")


def _load_rulebook_v2():
    rb_path = _find_rulebook_v2()
    return load_rulebook(rb_path, kind="relationship"), rb_path


def _market(
    market_id: str,
    question: str,
    outcomes: list[str] | None = None,
    token_ids: list[str] | None = None,
) -> MarketRow:
    if outcomes is None:
        outcomes = ["Yes", "No"]
    if token_ids is None:
        token_ids = [f"tok_{market_id}_yes", f"tok_{market_id}_no"]
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
        outcomes=outcomes,
        gamma_outcome_prices_snapshot=[Decimal("0.5"), Decimal("0.5")],
        clob_token_ids=token_ids,
        volume=None,
        liquidity=None,
        event_id=None,
        neg_risk=False,
        text_hash=f"hash_{market_id}",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _sem(
    market_id: str,
    subject_entities: list[str] | None = None,
    event_entities: list[str] | None = None,
    question: str | None = None,
    outcome_space_json: str | None = None,
    proposition_json: str | None = None,
    event_atoms_json: str | None = None,
    semantic_confidence: float = 0.85,
    terms_confidence: float = 0.75,
    long_horizon: bool = False,
    unresolved_reference_event: bool = False,
) -> MarketSemanticsRow:
    return MarketSemanticsRow(
        source_market_id=market_id,
        source_condition_id=None,
        question=question or f"Q for {market_id}",
        canonical_question=question or f"Q for {market_id} (canonical)",
        market_type="binary",
        subject_entities=subject_entities or [],
        event_entities=event_entities or [],
        temporal_phrase=None,
        temporal_phrase_normalized=None,
        temporal_resolution="vague",
        exact_deadline_ms=None,
        date_constraints_json="{}",
        jurisdiction=None,
        positive_resolution_condition=f"Market {market_id} resolves YES",
        negative_resolution_condition=f"Market {market_id} resolves NO",
        necessary_conditions_for_yes=[],
        sufficient_conditions_for_yes=[],
        necessary_conditions_for_no=[],
        sufficient_conditions_for_no=[],
        evidence_required=[],
        ambiguity_flags=[],
        ambiguity_score=None,
        semantic_confidence=semantic_confidence,
        needs_manual_review=False,
        explanation_summary=None,
        flag_rationales_json=None,
        uncertainty_notes_json=None,
        rule_curation_notes_json=None,
        raw_response_hash="abc",
        model_name="test",
        prompt_version="market_semantics_v2",
        rulebook_id=None,
        rulebook_version=None,
        extraction_id=f"ext_{market_id}",
        outcome_space_json=outcome_space_json,
        proposition_json=proposition_json,
        event_atoms_json=event_atoms_json,
        terms_confidence=terms_confidence,
        long_horizon=long_horizon,
        unresolved_reference_event=unresolved_reference_event,
        schema_version=2,
        ingested_ts_ms=_TS,
    )


def _stanley_cup_outcome_space(team: str, competition_id: str = "nhl_stanley_cup_2026") -> str:
    return json.dumps({
        "kind": "single_winner_competition",
        "competition_id": competition_id,
        "candidate": team,
        "winner_predicate": "win",
    })


def _temporal_proposition(left: str, relation: str, right: str) -> str:
    return json.dumps({
        "type": "temporal_order",
        "left_event": left,
        "relation": relation,
        "right_event": right,
        "strictness": f"strict_{relation}",
    })


def _good_coverage(market_id: str) -> BackfillCoverageRow:
    return BackfillCoverageRow(
        market_id=market_id,
        condition_id=f"cond_{market_id}",
        question=f"Q for {market_id}",
        start_ts_ms=_TS - 180 * 86400 * 1000,
        end_ts_ms=_TS,
        requested_days=180,
        has_gamma=True,
        has_price_history=True,
        has_trade_history=False,
        has_semantics=True,
        has_rulebook_score=False,
        has_implications=False,
        has_embeddings=False,
        has_backfill_coverage=True,
        price_points_count=2000,
        trade_points_count=0,
        first_price_ts_ms=_TS - 180 * 86400 * 1000,
        last_price_ts_ms=_TS,
        missing_price_gap_count=0,
        largest_price_gap_ms=3600000,
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


class TestMutuallyExclusiveCategoryDetection:
    def setup_method(self):
        self.rulebook, self.rb_path = _load_rulebook_v2()
        self.rulebook_hash = self.rb_path.read_bytes().hex()[:16]

    def _validate(self, pairs, semantics, coverage=None):
        coverage = coverage or {}
        return list(validate_all_pairs(pairs, semantics, coverage, self.rulebook, self.rulebook_hash))

    def test_nhl_different_teams_same_cup_accepted(self):
        """Carolina Hurricanes vs Philadelphia Flyers — same Stanley Cup, different candidates."""
        hurricanes = _market("553824", "Will the Carolina Hurricanes win the 2026 NHL Stanley Cup?")
        flyers = _market("553843", "Will the Philadelphia Flyers win the 2026 NHL Stanley Cup?")
        sem_h = _sem("553824",
                     outcome_space_json=_stanley_cup_outcome_space("Carolina Hurricanes"),
                     event_atoms_json=json.dumps([{"event_id": "hurricanes_win_cup", "subject": "Carolina Hurricanes", "event_type": "sports_win", "ambiguity_flags": []}]))
        sem_f = _sem("553843",
                     outcome_space_json=_stanley_cup_outcome_space("Philadelphia Flyers"),
                     event_atoms_json=json.dumps([{"event_id": "flyers_win_cup", "subject": "Philadelphia Flyers", "event_type": "sports_win", "ambiguity_flags": []}]))
        pair = CandidatePair(
            market_a=hurricanes, market_b=flyers,
            sources=["outcome_space_cluster"],
            generation_source="mutually_exclusive_category",
        )
        results = self._validate([pair], {"553824": sem_h, "553843": sem_f})
        assert len(results) == 1
        row = results[0]
        assert row.relationship_type == "mutually_exclusive_category"
        assert row.validation_status == "accepted"
        assert row.relationship_family == "category"
        assert row.candidate_a in ("Carolina Hurricanes", "Philadelphia Flyers")
        assert row.candidate_b in ("Carolina Hurricanes", "Philadelphia Flyers")
        assert row.shared_event == "nhl_stanley_cup_2026"

    def test_different_competitions_rejected(self):
        """NHL Stanley Cup vs NBA Championship — different competition IDs."""
        market_a = _market("m1", "Will the Hurricanes win the 2026 NHL Stanley Cup?")
        market_b = _market("m2", "Will the Celtics win the 2026 NBA Championship?")
        sem_a = _sem("m1", outcome_space_json=_stanley_cup_outcome_space("Carolina Hurricanes", "nhl_stanley_cup_2026"))
        sem_b = _sem("m2", outcome_space_json=_stanley_cup_outcome_space("Boston Celtics", "nba_championship_2026"))
        pair = CandidatePair(
            market_a=market_a, market_b=market_b,
            sources=["outcome_space_cluster"],
            generation_source="mutually_exclusive_category",
        )
        results = self._validate([pair], {"m1": sem_a, "m2": sem_b})
        row = results[0]
        assert row.validation_status == "rejected"
        reasons = json.loads(row.rejection_reasons_json)
        codes = [r["code"] for r in reasons]
        assert "outcome_space_mismatch" in codes

    def test_same_candidate_duplicate_rejected(self):
        """Two markets for the same team (duplicate) should be rejected."""
        market_a = _market("m1", "Will the Hurricanes win the 2026 NHL Stanley Cup?")
        market_b = _market("m2", "Will the Hurricanes win the 2026 NHL Stanley Cup? (v2)")
        sem_a = _sem("m1", outcome_space_json=_stanley_cup_outcome_space("Carolina Hurricanes"))
        sem_b = _sem("m2", outcome_space_json=_stanley_cup_outcome_space("Carolina Hurricanes"))  # same candidate
        pair = CandidatePair(
            market_a=market_a, market_b=market_b,
            sources=["outcome_space_cluster"],
            generation_source="mutually_exclusive_category",
        )
        results = self._validate([pair], {"m1": sem_a, "m2": sem_b})
        row = results[0]
        assert row.validation_status == "rejected"
        reasons = json.loads(row.rejection_reasons_json)
        codes = [r["code"] for r in reasons]
        assert "same_candidate" in codes

    def test_global_entity_threshold_does_not_block(self):
        """min_entity_match should NOT be applied to mutually_exclusive_category pairs."""
        hurricanes = _market("553824", "Will the Carolina Hurricanes win the 2026 NHL Stanley Cup?")
        flyers = _market("553843", "Will the Philadelphia Flyers win the 2026 NHL Stanley Cup?")
        sem_h = _sem("553824",
                     subject_entities=["Carolina Hurricanes"],  # very different entities
                     outcome_space_json=_stanley_cup_outcome_space("Carolina Hurricanes"))
        sem_f = _sem("553843",
                     subject_entities=["Philadelphia Flyers"],  # zero overlap
                     outcome_space_json=_stanley_cup_outcome_space("Philadelphia Flyers"))
        pair = CandidatePair(
            market_a=hurricanes, market_b=flyers,
            sources=["outcome_space_cluster"],
            generation_source="mutually_exclusive_category",
        )
        results = self._validate([pair], {"553824": sem_h, "553843": sem_f})
        row = results[0]
        # entity_mismatch should NOT appear in rejection reasons
        reasons = json.loads(row.rejection_reasons_json or "[]")
        codes = [r["code"] for r in reasons]
        assert "entity_mismatch" not in codes

    def test_old_semantics_question_fallback_accepts_category_pair(self):
        hurricanes_q = "Will the Carolina Hurricanes win the 2026 NHL Stanley Cup?"
        flyers_q = "Will the Philadelphia Flyers win the 2026 NHL Stanley Cup?"
        hurricanes = _market("553824", hurricanes_q)
        flyers = _market("553843", flyers_q)
        sem_h = _sem(
            "553824",
            subject_entities=["Carolina Hurricanes"],
            question=hurricanes_q,
            outcome_space_json=None,
        )
        sem_f = _sem(
            "553843",
            subject_entities=["Philadelphia Flyers"],
            question=flyers_q,
            outcome_space_json=None,
        )
        pair = CandidatePair(
            market_a=hurricanes, market_b=flyers,
            sources=["outcome_space_cluster"],
            generation_source="mutually_exclusive_category",
        )
        row = self._validate([pair], {"553824": sem_h, "553843": sem_f})[0]

        assert row.relationship_type == "mutually_exclusive_category"
        assert row.validation_status == "accepted"
        assert row.outcome_space_match_score == 1.0
        assert row.candidate_a == "Carolina Hurricanes"
        assert row.candidate_b == "Philadelphia Flyers"
        assert row.shared_event == "2026_nhl_stanley_cup"


class TestTemporalValidators:
    def setup_method(self):
        self.rulebook, self.rb_path = _load_rulebook_v2()
        self.rulebook_hash = self.rb_path.read_bytes().hex()[:16]

    def _validate(self, pairs, semantics, coverage=None):
        coverage = coverage or {}
        return list(validate_all_pairs(pairs, semantics, coverage, self.rulebook, self.rulebook_hash))

    def test_inverse_temporal_order_accepted(self):
        """A before B + B before A should be detected as inverse_temporal_order."""
        market_a = _market("m1", "New Rihanna Album before GTA VI?")
        market_b = _market("m2", "GTA VI released before new Rihanna Album?")
        atoms = json.dumps([
            {"event_id": "rihanna_album_release", "subject": "Rihanna", "event_type": "album_release", "ambiguity_flags": []},
            {"event_id": "gta_vi_release", "subject": "GTA VI", "event_type": "game_release", "ambiguity_flags": []},
        ])
        sem_a = _sem("m1",
                     event_atoms_json=atoms,
                     proposition_json=_temporal_proposition("rihanna_album_release", "before", "gta_vi_release"),
                     terms_confidence=0.7,
                     long_horizon=True,
                     unresolved_reference_event=True)
        sem_b = _sem("m2",
                     event_atoms_json=atoms,
                     proposition_json=_temporal_proposition("gta_vi_release", "before", "rihanna_album_release"),
                     terms_confidence=0.7,
                     long_horizon=True,
                     unresolved_reference_event=True)
        pair = CandidatePair(
            market_a=market_a, market_b=market_b,
            sources=["proposition_cluster"],
            generation_source="inverse_temporal_order",
        )
        results = self._validate([pair], {"m1": sem_a, "m2": sem_b})
        row = results[0]
        assert row.relationship_type == "inverse_temporal_order"
        assert row.relationship_family == "temporal"

    def test_temporal_before_accepted_with_shared_reference(self):
        """Two markets sharing the same right_event (GTA VI) should be candidates."""
        market_a = _market("m1", "New Rihanna Album before GTA VI?")
        market_b = _market("m2", "Will Bitcoin hit $1m before GTA VI?")
        atoms_a = json.dumps([
            {"event_id": "rihanna_album_release", "subject": "Rihanna", "event_type": "album_release", "ambiguity_flags": []},
            {"event_id": "gta_vi_release", "subject": "GTA VI", "event_type": "game_release", "ambiguity_flags": ["release_date_unknown"]},
        ])
        atoms_b = json.dumps([
            {"event_id": "bitcoin_1m", "subject": "Bitcoin", "event_type": "price_threshold", "ambiguity_flags": []},
            {"event_id": "gta_vi_release", "subject": "GTA VI", "event_type": "game_release", "ambiguity_flags": ["release_date_unknown"]},
        ])
        sem_a = _sem("m1",
                     event_atoms_json=atoms_a,
                     proposition_json=_temporal_proposition("rihanna_album_release", "before", "gta_vi_release"),
                     terms_confidence=0.65,
                     unresolved_reference_event=True)
        sem_b = _sem("m2",
                     event_atoms_json=atoms_b,
                     proposition_json=_temporal_proposition("bitcoin_1m", "before", "gta_vi_release"),
                     terms_confidence=0.65,
                     unresolved_reference_event=True)
        pair = CandidatePair(
            market_a=market_a, market_b=market_b,
            sources=["proposition_cluster"],
            generation_source="same_reference_clock",
        )
        results = self._validate([pair], {"m1": sem_a, "m2": sem_b})
        row = results[0]
        assert row.relationship_type == "same_reference_clock"
        assert row.relationship_family == "temporal"

    def test_long_horizon_flagged_not_rejected(self):
        """Long horizon should lower confidence but NOT auto-reject."""
        market_a = _market("m1", "New Rihanna Album before GTA VI?")
        market_b = _market("m2", "GTA VI released before new Rihanna Album?")
        atoms = json.dumps([
            {"event_id": "rihanna_album_release", "subject": "Rihanna", "event_type": "album_release", "ambiguity_flags": []},
            {"event_id": "gta_vi_release", "subject": "GTA VI", "event_type": "game_release", "ambiguity_flags": []},
        ])
        sem_a = _sem("m1",
                     event_atoms_json=atoms,
                     proposition_json=_temporal_proposition("rihanna_album_release", "before", "gta_vi_release"),
                     terms_confidence=0.8,
                     long_horizon=True)
        sem_b = _sem("m2",
                     event_atoms_json=atoms,
                     proposition_json=_temporal_proposition("gta_vi_release", "before", "rihanna_album_release"),
                     terms_confidence=0.8,
                     long_horizon=True)
        pair = CandidatePair(
            market_a=market_a, market_b=market_b,
            sources=["proposition_cluster"],
            generation_source="inverse_temporal_order",
        )
        results = self._validate([pair], {"m1": sem_a, "m2": sem_b})
        row = results[0]
        # Must not be rejected purely due to long_horizon
        assert row.validation_status != "rejected" or "long_horizon" not in [
            r["code"] for r in json.loads(row.rejection_reasons_json or "[]")
        ]

    def test_temporal_before_missing_event_atoms_rejected(self):
        """If neither market has event_atoms, temporal relationship should be rejected."""
        market_a = _market("m1", "New Rihanna Album before GTA VI?")
        market_b = _market("m2", "Will Bitcoin hit $1m before GTA VI?")
        sem_a = _sem("m1", terms_confidence=0.3)   # no event_atoms, no proposition
        sem_b = _sem("m2", terms_confidence=0.3)
        pair = CandidatePair(
            market_a=market_a, market_b=market_b,
            sources=["proposition_cluster"],
            generation_source="temporal_before",
        )
        results = self._validate([pair], {"m1": sem_a, "m2": sem_b})
        row = results[0]
        assert row.validation_status == "rejected"
        reasons = json.loads(row.rejection_reasons_json or "[]")
        codes = [r["code"] for r in reasons]
        assert "missing_event_atoms" in codes

    def test_entity_mismatch_does_not_block_temporal(self):
        """Temporal types should bypass the global min_entity_match gate."""
        market_a = _market("m1", "New Rihanna Album before GTA VI?")
        market_b = _market("m2", "GTA VI released before new Rihanna Album?")
        atoms = json.dumps([
            {"event_id": "rihanna_album_release", "subject": "Rihanna", "event_type": "album_release", "ambiguity_flags": []},
            {"event_id": "gta_vi_release", "subject": "GTA VI", "event_type": "game_release", "ambiguity_flags": []},
        ])
        sem_a = _sem("m1",
                     subject_entities=["Rihanna"],  # totally different subjects
                     event_atoms_json=atoms,
                     proposition_json=_temporal_proposition("rihanna_album_release", "before", "gta_vi_release"),
                     terms_confidence=0.8)
        sem_b = _sem("m2",
                     subject_entities=["GTA VI"],  # zero entity overlap
                     event_atoms_json=atoms,
                     proposition_json=_temporal_proposition("gta_vi_release", "before", "rihanna_album_release"),
                     terms_confidence=0.8)
        pair = CandidatePair(
            market_a=market_a, market_b=market_b,
            sources=["proposition_cluster"],
            generation_source="inverse_temporal_order",
        )
        results = self._validate([pair], {"m1": sem_a, "m2": sem_b})
        row = results[0]
        reasons = json.loads(row.rejection_reasons_json or "[]")
        codes = [r["code"] for r in reasons]
        assert "entity_mismatch" not in codes

    def test_old_semantics_question_fallback_generates_temporal_relationship(self):
        rihanna_q = "New Rihanna Album before GTA VI?"
        bitcoin_q = "Will bitcoin hit $1m before GTA VI?"
        market_a = _market("m1", rihanna_q)
        market_b = _market("m2", bitcoin_q)
        sem_a = _sem("m1", subject_entities=["Rihanna"], question=rihanna_q, terms_confidence=0.7)
        sem_b = _sem("m2", subject_entities=["Bitcoin"], question=bitcoin_q, terms_confidence=0.7)
        pair = CandidatePair(
            market_a=market_a, market_b=market_b,
            sources=["proposition_cluster"],
            generation_source="same_reference_clock",
        )
        row = self._validate([pair], {"m1": sem_a, "m2": sem_b})[0]

        assert row.relationship_type == "same_reference_clock"
        assert row.validation_status in ("accepted", "needs_manual_review")
        assert row.reference_event == "gta_vi_release"


class TestStrategyEligibility:
    def setup_method(self):
        self.rulebook, self.rb_path = _load_rulebook_v2()
        self.rulebook_hash = self.rb_path.read_bytes().hex()[:16]

    def _validate(self, pairs, semantics, coverage=None):
        coverage = coverage or {}
        return list(validate_all_pairs(pairs, semantics, coverage, self.rulebook, self.rulebook_hash))

    def test_accepted_with_good_coverage_is_eligible(self):
        hurricanes = _market("553824", "Will the Carolina Hurricanes win the 2026 NHL Stanley Cup?")
        flyers = _market("553843", "Will the Philadelphia Flyers win the 2026 NHL Stanley Cup?")
        sem_h = _sem("553824", outcome_space_json=_stanley_cup_outcome_space("Carolina Hurricanes"))
        sem_f = _sem("553843", outcome_space_json=_stanley_cup_outcome_space("Philadelphia Flyers"))
        cov_h = _good_coverage("553824")
        cov_f = _good_coverage("553843")
        pair = CandidatePair(
            market_a=hurricanes, market_b=flyers,
            sources=["outcome_space_cluster"],
            generation_source="mutually_exclusive_category",
        )
        results = self._validate(
            [pair], {"553824": sem_h, "553843": sem_f},
            coverage={"553824": cov_h, "553843": cov_f}
        )
        row = results[0]
        if row.validation_status == "accepted":
            assert row.strategy_eligibility_status == "eligible"

    def test_long_horizon_makes_ineligible(self):
        """Temporal pairs with long_horizon + unresolved_ref should be ineligible for strategy."""
        market_a = _market("m1", "New Rihanna Album before GTA VI?")
        market_b = _market("m2", "GTA VI released before new Rihanna Album?")
        atoms = json.dumps([
            {"event_id": "rihanna_album_release", "subject": "Rihanna", "event_type": "album_release", "ambiguity_flags": []},
            {"event_id": "gta_vi_release", "subject": "GTA VI", "event_type": "game_release", "ambiguity_flags": []},
        ])
        sem_a = _sem("m1",
                     event_atoms_json=atoms,
                     proposition_json=_temporal_proposition("rihanna_album_release", "before", "gta_vi_release"),
                     terms_confidence=0.8,
                     long_horizon=True,
                     unresolved_reference_event=True)
        sem_b = _sem("m2",
                     event_atoms_json=atoms,
                     proposition_json=_temporal_proposition("gta_vi_release", "before", "rihanna_album_release"),
                     terms_confidence=0.8,
                     long_horizon=True,
                     unresolved_reference_event=True)
        pair = CandidatePair(
            market_a=market_a, market_b=market_b,
            sources=["proposition_cluster"],
            generation_source="inverse_temporal_order",
        )
        results = self._validate([pair], {"m1": sem_a, "m2": sem_b})
        row = results[0]
        # Strategy eligibility should be ineligible for temporal types (see evaluator logic)
        assert row.strategy_eligibility_status in ("ineligible", "needs_manual_review", "unknown")
