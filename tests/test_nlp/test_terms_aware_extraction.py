"""Tests for Phase 5.5 D+ terms-aware semantic extraction."""

from __future__ import annotations

from polymarket_arb.nlp.schemas import (
    EventAtom,
    MarketSemantics,
    OutcomeSpace,
    Proposition,
)


def _base_fields() -> dict:
    """Minimum valid MarketSemantics fields."""
    return {
        "source_market_id": "test_market",
        "source_condition_id": None,
        "question": "Test question?",
        "canonical_question": "Test question normalised?",
        "market_type": "binary",
        "subject_entities": [],
        "event_entities": [],
        "temporal_resolution": "vague",
        "positive_resolution_condition": "YES happens",
        "negative_resolution_condition": "YES does not happen",
        "ambiguity_flags": [],
        "semantic_confidence": 0.8,
        "needs_manual_review": False,
    }


class TestEventAtom:
    def test_minimal_event_atom(self):
        atom = EventAtom(
            event_id="hurricanes_win_stanley_cup",
            subject="Carolina Hurricanes",
            event_type="sports_win",
        )
        assert atom.event_id == "hurricanes_win_stanley_cup"
        assert atom.definition is None
        assert atom.ambiguity_flags == []

    def test_event_atom_with_all_fields(self):
        atom = EventAtom(
            event_id="rihanna_album_release",
            subject="Rihanna",
            event_type="album_release",
            definition="Official release of a new studio album",
            source_of_truth="Billboard / RIAA",
            ambiguity_flags=["promotional_release_ambiguity"],
        )
        assert atom.source_of_truth == "Billboard / RIAA"
        assert len(atom.ambiguity_flags) == 1


class TestProposition:
    def test_temporal_order_proposition(self):
        prop = Proposition(
            type="temporal_order",
            left_event="rihanna_album_release",
            relation="before",
            right_event="gta_vi_release",
            strictness="strict_before",
        )
        assert prop.type == "temporal_order"
        assert prop.relation == "before"

    def test_proposition_null_events_ok(self):
        prop = Proposition(type="other")
        assert prop.left_event is None
        assert prop.right_event is None


class TestOutcomeSpace:
    def test_single_winner_competition(self):
        os = OutcomeSpace(
            kind="single_winner_competition",
            competition_id="nhl_stanley_cup_2026",
            candidate="Carolina Hurricanes",
            winner_predicate="win",
        )
        assert os.kind == "single_winner_competition"
        assert os.competition_id == "nhl_stanley_cup_2026"
        assert os.candidate == "Carolina Hurricanes"

    def test_binary_event_kind(self):
        os = OutcomeSpace(kind="binary_event")
        assert os.competition_id is None
        assert os.candidate is None


class TestMarketSemanticsTermsAware:
    def test_nhl_market_with_full_terms(self):
        """A Stanley Cup market should parse with outcome_space + event_atoms."""
        fields = _base_fields()
        fields.update({
            "source_market_id": "553824",
            "question": "Will the Carolina Hurricanes win the 2026 NHL Stanley Cup?",
            "canonical_question": "Carolina Hurricanes wins 2026 NHL Stanley Cup?",
            "event_entities": ["Carolina Hurricanes", "NHL Stanley Cup"],
            "event_atoms": [
                {
                    "event_id": "hurricanes_win_stanley_cup",
                    "subject": "Carolina Hurricanes",
                    "event_type": "sports_win",
                    "definition": "Hurricanes defeat all opponents in NHL playoffs",
                    "source_of_truth": "NHL official results",
                    "ambiguity_flags": [],
                }
            ],
            "outcome_space": {
                "kind": "single_winner_competition",
                "competition_id": "nhl_stanley_cup_2026",
                "candidate": "Carolina Hurricanes",
                "winner_predicate": "win",
            },
            "terms_confidence": 0.82,
            "long_horizon": False,
            "unresolved_reference_event": False,
        })
        sem = MarketSemantics.model_validate(fields)
        assert sem.outcome_space is not None
        assert sem.outcome_space.kind == "single_winner_competition"
        assert sem.outcome_space.competition_id == "nhl_stanley_cup_2026"
        assert sem.outcome_space.candidate == "Carolina Hurricanes"
        assert len(sem.event_atoms) == 1
        assert sem.event_atoms[0].event_id == "hurricanes_win_stanley_cup"
        assert sem.terms_confidence == 0.82

    def test_temporal_order_market_rihanna_gta(self):
        """A temporal market should parse with proposition."""
        fields = _base_fields()
        fields.update({
            "source_market_id": "540817",
            "question": "New Rihanna Album before GTA VI?",
            "canonical_question": "Rihanna album released before GTA VI release?",
            "event_atoms": [
                {
                    "event_id": "rihanna_album_release",
                    "subject": "Rihanna",
                    "event_type": "album_release",
                    "definition": None,
                    "source_of_truth": None,
                    "ambiguity_flags": [],
                },
                {
                    "event_id": "gta_vi_release",
                    "subject": "GTA VI",
                    "event_type": "game_release",
                    "definition": "Official public commercial release",
                    "source_of_truth": None,
                    "ambiguity_flags": ["release_date_unconfirmed"],
                },
            ],
            "proposition": {
                "type": "temporal_order",
                "left_event": "rihanna_album_release",
                "relation": "before",
                "right_event": "gta_vi_release",
                "strictness": "strict_before",
            },
            "outcome_space": {
                "kind": "temporal_order",
                "competition_id": None,
                "candidate": None,
                "winner_predicate": None,
            },
            "long_horizon": True,
            "unresolved_reference_event": True,
            "terms_confidence": 0.55,
        })
        sem = MarketSemantics.model_validate(fields)
        assert sem.proposition is not None
        assert sem.proposition.type == "temporal_order"
        assert sem.proposition.left_event == "rihanna_album_release"
        assert sem.proposition.right_event == "gta_vi_release"
        assert sem.long_horizon is True
        assert sem.unresolved_reference_event is True
        assert sem.terms_confidence == 0.55

    def test_optional_terms_fields_default_to_none(self):
        """A v1-style market with no terms fields should still validate."""
        fields = _base_fields()
        sem = MarketSemantics.model_validate(fields)
        assert sem.event_atoms == []
        assert sem.proposition is None
        assert sem.outcome_space is None
        assert sem.tie_rule is None
        assert sem.if_event_never_occurs_rule is None
        assert sem.terms_confidence == 0.0
        assert sem.long_horizon is False
        assert sem.unresolved_reference_event is False

    def test_terms_fields_round_trip(self):
        """Terms fields survive model_dump → model_validate round-trip."""
        fields = _base_fields()
        fields.update({
            "tie_rule": "Resolves NO on tie.",
            "if_event_never_occurs_rule": "Resolves NO if GTA VI never releases.",
            "resolution_source": "Rockstar Games official press release",
            "timezone_or_boundary": "ET midnight",
            "terms_confidence": 0.72,
        })
        sem = MarketSemantics.model_validate(fields)
        dumped = sem.model_dump()
        sem2 = MarketSemantics.model_validate(dumped)
        assert sem2.tie_rule == "Resolves NO on tie."
        assert sem2.if_event_never_occurs_rule == "Resolves NO if GTA VI never releases."
        assert sem2.terms_confidence == 0.72

    def test_no_thinking_in_terms_fields(self):
        """<think> content must not survive into terms fields."""
        fields = _base_fields()
        fields.update({
            "tie_rule": "Resolves NO on tie.",
        })
        sem = MarketSemantics.model_validate(fields)
        assert "<think>" not in (sem.tie_rule or "")
        assert "<think>" not in (sem.if_event_never_occurs_rule or "")
        assert "<think>" not in (sem.resolution_source or "")
