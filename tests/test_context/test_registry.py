"""Context registry validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from polymarket_arb.context.models import COMPLETENESS_CLASSES
from polymarket_arb.context.source_registry import load_context_registry, registry_audit

CONFIG = Path("configs/context_spaces/context_spaces_v1.yaml")


def test_registry_parses_required_spaces():
    registry = load_context_registry(CONFIG)
    assert "nba_championship_conference_progression" in registry.context_spaces
    assert "premier_league_finish_position" in registry.context_spaces
    assert "same_reference_clock_before_gta_vi" in registry.context_spaces
    assert registry.context_spaces["balance_of_power_combinations"].completeness_class in COMPLETENESS_CLASSES


def test_registry_audit_counts_completeness_classes():
    audit = registry_audit(CONFIG)
    assert audit["spaces"] >= 7
    assert audit["completeness_class_counts"]["open_world"] >= 1
    assert audit["completeness_class_counts"]["known_complete"] >= 1


def test_invalid_completeness_class_fails(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        """
context_spaces:
  bad_space:
    display_name: Bad
    context_type: bad
    completeness_class: maybe_complete
    source_requirements:
      min_source_tier: 1
      allowed_source_tiers: [1]
      required_domains: []
    required_world_rule_types: []
    required_market_terms_rule_types: []
    allowed_strategy_lanes: [analysis_only]
    human_review_required: false
    deterministic_implications: []
    deterministic_exclusions: []
    notes: []
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="completeness"):
        load_context_registry(path)
