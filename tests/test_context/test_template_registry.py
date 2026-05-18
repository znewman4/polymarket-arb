"""Tests for the deterministic template registry."""

from __future__ import annotations

import textwrap
from datetime import datetime, timezone
from pathlib import Path

from polymarket_arb.context.template_registry import (
    DeterministicTemplate,
    find_matching_template,
    load_template_registry,
)
from polymarket_arb.storage.base import RelationshipCandidateRow

TS = int(datetime.now(timezone.utc).timestamp() * 1000)
TEMPLATES_PATH = Path("configs/deterministic_templates/templates_v1.yaml")


def _rel(
    *,
    relationship_type: str = "nested_a_implies_b",
    relationship_subtype: str = "same_topic_no_trade",
    outcome_subtype_a: str = "",
    outcome_subtype_b: str = "",
    team_a: str | None = None,
    team_b: str | None = None,
    candidate_a: str | None = None,
    candidate_b: str | None = None,
    shared_event: str | None = None,
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id="rel_tmpl_test",
        market_id_a="market_a",
        market_id_b="market_b",
        condition_id_a=None,
        condition_id_b=None,
        token_id_a_yes=None,
        token_id_a_no=None,
        token_id_b_yes=None,
        token_id_b_no=None,
        question_a="Q A?",
        question_b="Q B?",
        relationship_type=relationship_type,
        entity_match_score=1.0,
        time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.8,
        model_confidence=0.8,
        final_confidence=0.8,
        validation_status="accepted",
        rejection_reasons_json="[]",
        rationale_summary="test",
        evidence_json="{}",
        rulebook_id="v2",
        rulebook_version=2,
        rulebook_content_hash="hash",
        relationship_subtype=relationship_subtype,
        outcome_subtype_a=outcome_subtype_a,
        outcome_subtype_b=outcome_subtype_b,
        team_a=team_a,
        team_b=team_b,
        candidate_a=candidate_a,
        candidate_b=candidate_b,
        shared_event=shared_event,
        schema_version=1,
        ingested_ts_ms=TS,
    )


class TestLoadTemplateRegistry:
    def test_loads_from_yaml(self) -> None:
        templates = load_template_registry(TEMPLATES_PATH)
        assert len(templates) >= 7
        ids = {t.template_id for t in templates}
        assert "sports_championship_implies_conference_v1" in ids
        assert "ranking_exact_finish_implies_top_n_v1" in ids
        assert "threshold_higher_implies_lower_v1" in ids
        assert "date_earlier_implies_later_v1" in ids
        assert "electoral_same_primary_mutual_exclusion_v1" in ids

    def test_all_approved_templates_have_context_space(self) -> None:
        templates = load_template_registry(TEMPLATES_PATH)
        for t in templates:
            if t.review_status == "approved":
                assert t.context_space_id, f"{t.template_id} missing context_space_id"

    def test_templates_have_non_zero_confidence(self) -> None:
        templates = load_template_registry(TEMPLATES_PATH)
        for t in templates:
            assert t.confidence > 0, f"{t.template_id} has zero confidence"
            assert t.min_relationship_confidence >= 0

    def test_loads_from_inline_yaml(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            schema_version: 1
            templates:
              - template_id: test_inline_v1
                version: 1
                domain: test
                review_status: approved
                confidence: 0.9
                min_relationship_confidence: 0.35
                match:
                  outcome_subtype_pair_any:
                    - [team_wins_championship, team_wins_conference]
                  relationship_types: [nested_a_implies_b]
                  same_team_required: true
                context_space_id: sports_championship_conference_progression
                strategy_condition: narrow_implies_broad
                required_rule_types: [championship_implies_conference]
                evidence_summary: "test"
        """)
        p = tmp_path / "test_templates.yaml"
        p.write_text(yaml_text, encoding="utf-8")
        templates = load_template_registry(p)
        assert len(templates) == 1
        t = templates[0]
        assert t.template_id == "test_inline_v1"
        assert t.review_status == "approved"
        assert t.conditions.same_team_required is True
        assert frozenset(["team_wins_championship", "team_wins_conference"]) in t.conditions.outcome_subtype_pair_any


class TestFindMatchingTemplate:
    def _templates(self) -> list[DeterministicTemplate]:
        return load_template_registry(TEMPLATES_PATH)

    def test_sports_championship_conference_matches(self) -> None:
        templates = self._templates()
        rel = _rel(
            relationship_type="nested_a_implies_b",
            outcome_subtype_a="team_wins_championship",
            outcome_subtype_b="team_wins_conference",
            team_a="spurs",
            team_b="spurs",
        )
        match = find_matching_template(rel, templates)
        assert match is not None
        assert match.template_id == "sports_championship_implies_conference_v1"

    def test_championship_conference_wrong_types_no_match(self) -> None:
        templates = self._templates()
        rel = _rel(
            relationship_type="contradiction",  # not a nesting type
            outcome_subtype_a="team_wins_championship",
            outcome_subtype_b="team_wins_conference",
            team_a="spurs",
            team_b="spurs",
        )
        match = find_matching_template(rel, templates)
        # no template matches contradiction with championship/conference pair
        assert match is None

    def test_championship_conference_different_teams_no_match(self) -> None:
        templates = self._templates()
        rel = _rel(
            relationship_type="nested_a_implies_b",
            outcome_subtype_a="team_wins_championship",
            outcome_subtype_b="team_wins_conference",
            team_a="spurs",
            team_b="lakers",
        )
        match = find_matching_template(rel, templates)
        assert match is None

    def test_ranking_exact_finish_implies_top_n_matches(self) -> None:
        templates = self._templates()
        rel = _rel(
            relationship_type="nested_a_implies_b",
            outcome_subtype_a="team_exact_finish_position",
            outcome_subtype_b="team_top_n_finish",
            team_a="man_city",
            team_b="man_city",
        )
        match = find_matching_template(rel, templates)
        assert match is not None
        assert match.template_id == "ranking_exact_finish_implies_top_n_v1"

    def test_ranking_exact_positions_exclusive_matches(self) -> None:
        templates = self._templates()
        rel = _rel(
            relationship_type="mutually_exclusive",
            outcome_subtype_a="team_exact_finish_position",
            outcome_subtype_b="team_exact_finish_position",
            team_a="man_city",
            team_b="man_city",
        )
        match = find_matching_template(rel, templates)
        assert match is not None
        assert match.template_id == "ranking_exact_positions_mutually_exclusive_v1"

    def test_threshold_nesting_matches(self) -> None:
        templates = self._templates()
        rel = _rel(
            relationship_type="nested_a_implies_b",
            outcome_subtype_a="price_hits_level_before_reference",
            outcome_subtype_b="price_hits_level_before_reference",
        )
        match = find_matching_template(rel, templates)
        assert match is not None
        assert match.template_id == "threshold_higher_implies_lower_v1"

    def test_date_nesting_matches(self) -> None:
        templates = self._templates()
        rel = _rel(
            relationship_type="nested_a_implies_b",
            outcome_subtype_a="event_a_before_event_b",
            outcome_subtype_b="event_a_before_event_b",
        )
        match = find_matching_template(rel, templates)
        assert match is not None
        assert match.template_id == "date_earlier_implies_later_v1"

    def test_electoral_mutual_exclusion_matches(self) -> None:
        templates = self._templates()
        rel = _rel(
            relationship_type="mutually_exclusive_category",
            outcome_subtype_a="candidate_wins_nomination",
            outcome_subtype_b="candidate_wins_nomination",
            candidate_a="McMorrow",
            candidate_b="Stevens",
            shared_event="michigan_dem_primary_2026",
        )
        match = find_matching_template(rel, templates)
        assert match is not None
        assert match.template_id == "electoral_same_primary_mutual_exclusion_v1"

    def test_electoral_same_candidate_no_match(self) -> None:
        templates = self._templates()
        rel = _rel(
            relationship_type="mutually_exclusive_category",
            outcome_subtype_a="candidate_wins_nomination",
            outcome_subtype_b="candidate_wins_nomination",
            candidate_a="McMorrow",
            candidate_b="McMorrow",  # same candidate
            shared_event="michigan_dem_primary_2026",
        )
        match = find_matching_template(rel, templates)
        assert match is None

    def test_electoral_no_shared_event_no_match(self) -> None:
        templates = self._templates()
        rel = _rel(
            relationship_type="mutually_exclusive_category",
            outcome_subtype_a="candidate_wins_nomination",
            outcome_subtype_b="candidate_wins_nomination",
            candidate_a="McMorrow",
            candidate_b="Stevens",
            shared_event=None,  # no shared event
        )
        match = find_matching_template(rel, templates)
        assert match is None

    def test_pending_template_not_matched(self, tmp_path: Path) -> None:
        """Templates with review_status=pending are never returned."""
        import textwrap
        yaml_text = textwrap.dedent("""\
            schema_version: 1
            templates:
              - template_id: pending_test_v1
                version: 1
                domain: test
                review_status: pending
                confidence: 0.9
                min_relationship_confidence: 0.35
                match:
                  outcome_subtype_pair_any:
                    - [team_wins_championship, team_wins_conference]
                  relationship_types: [nested_a_implies_b]
                  same_team_required: true
                context_space_id: sports_championship_conference_progression
                strategy_condition: narrow_implies_broad
                required_rule_types: [championship_implies_conference]
                evidence_summary: "test"
        """)
        p = tmp_path / "pending.yaml"
        p.write_text(yaml_text, encoding="utf-8")
        templates = load_template_registry(p)
        rel = _rel(
            relationship_type="nested_a_implies_b",
            outcome_subtype_a="team_wins_championship",
            outcome_subtype_b="team_wins_conference",
            team_a="spurs",
            team_b="spurs",
        )
        assert find_matching_template(rel, templates) is None

    def test_unknown_subtype_pair_no_match(self) -> None:
        templates = self._templates()
        rel = _rel(
            relationship_type="nested_a_implies_b",
            outcome_subtype_a="some_unknown_subtype",
            outcome_subtype_b="another_unknown_subtype",
        )
        assert find_matching_template(rel, templates) is None

    def test_empty_outcome_subtypes_no_match_for_pair_requirement(self) -> None:
        """Pair-based templates require non-empty outcome subtypes."""
        templates = self._templates()
        rel = _rel(
            relationship_type="nested_a_implies_b",
            outcome_subtype_a="",
            outcome_subtype_b="",
        )
        assert find_matching_template(rel, templates) is None
