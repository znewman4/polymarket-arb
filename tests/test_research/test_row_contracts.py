"""Tests for the typed row contracts.

These tests enforce the central rules:
- Diagnostic-only subtypes must NEVER pass StrategyEligibleRelationshipRow
- AcceptedSimulatedTradeRow must require strategy_family
- space_id_for() obeys the precedence rule
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from polymarket_arb.research.row_contracts import (
    DIAGNOSTIC_ONLY_SUBTYPES,
    AcceptedSimulatedTradeRow,
    DiagnosticOnlyRelationshipRow,
    GrossOpportunityRow,
    RelationshipAuditRow,
    SpaceSummaryRow,
    StrategyEligibleRelationshipRow,
    is_diagnostic_only_subtype,
    space_id_for,
)

# ─── is_diagnostic_only_subtype ───────────────────────────────────────────────


def test_same_topic_no_trade_is_diagnostic_only():
    assert is_diagnostic_only_subtype("same_topic_no_trade") is True


def test_mixed_party_nomination_is_diagnostic_only():
    assert is_diagnostic_only_subtype("mixed_party_nomination") is True


def test_championship_implies_conference_is_not_diagnostic_only():
    assert is_diagnostic_only_subtype("championship_implies_conference") is False


def test_empty_subtype_is_diagnostic_only():
    assert is_diagnostic_only_subtype("") is True
    assert is_diagnostic_only_subtype(None) is True


# ─── space_id_for precedence ──────────────────────────────────────────────────


def test_outcome_space_wins_over_context():
    sid, status = space_id_for(outcome_space_id="x", context_space_id="y")
    assert sid == "x"
    assert status == "outcome_space"


def test_context_space_fallback():
    sid, status = space_id_for(context_space_id="c")
    assert sid == "c"
    assert status == "context_space"


def test_bundle_space_fallback():
    sid, status = space_id_for(bundle_space_id="b")
    assert sid == "b"
    assert status == "bundle_space"


def test_synthetic_when_none():
    sid, status = space_id_for(fallback="fb")
    assert sid == "fb"
    assert status == "synthetic_or_missing"


def test_unattributed_default():
    sid, status = space_id_for()
    assert sid == "unattributed"
    assert status == "synthetic_or_missing"


# ─── RelationshipAuditRow ─────────────────────────────────────────────────────


def test_relationship_audit_row_requires_id():
    with pytest.raises(ValidationError):
        RelationshipAuditRow(relationship_id="")


def test_relationship_audit_row_defaults():
    r = RelationshipAuditRow(relationship_id="r1")
    assert r.diagnostic_only is True
    assert r.strategy_eligible is False
    assert r.space_attribution_status == "synthetic_or_missing"


# ─── StrategyEligibleRelationshipRow ─────────────────────────────────────────


def test_strategy_eligible_requires_strategy_family():
    with pytest.raises(ValidationError):
        StrategyEligibleRelationshipRow(
            relationship_id="r1",
            relationship_type="nested_a_implies_b",
            relationship_subtype="championship_implies_conference",
            relationship_family="nesting",
            strategy_family="",  # invalid
            space_id="s",
            space_attribution_status="outcome_space",
        )


def test_strategy_eligible_rejects_none_or_unknown_family():
    for bad in ("none", "unknown", "NONE", "  "):
        with pytest.raises(ValidationError):
            StrategyEligibleRelationshipRow(
                relationship_id="r1",
                relationship_type="t",
                relationship_subtype="championship_implies_conference",
                relationship_family="nesting",
                strategy_family=bad,
                space_id="s",
                space_attribution_status="outcome_space",
            )


def test_strategy_eligible_rejects_diagnostic_only_subtype():
    for bad in DIAGNOSTIC_ONLY_SUBTYPES:
        with pytest.raises(ValidationError):
            StrategyEligibleRelationshipRow(
                relationship_id="r1",
                relationship_type="t",
                relationship_subtype=bad,
                relationship_family="nesting",
                strategy_family="nesting",
                space_id="s",
                space_attribution_status="outcome_space",
            )


def test_strategy_eligible_accepts_valid_row():
    r = StrategyEligibleRelationshipRow(
        relationship_id="r1",
        relationship_type="nested_a_implies_b",
        relationship_subtype="championship_implies_conference",
        relationship_family="nesting",
        strategy_family="nesting",
        space_id="space_a",
        space_attribution_status="outcome_space",
    )
    assert r.strategy_family == "nesting"


# ─── DiagnosticOnlyRelationshipRow ───────────────────────────────────────────


def test_diagnostic_only_accepts_same_topic_no_trade():
    r = DiagnosticOnlyRelationshipRow(
        relationship_id="r1",
        relationship_subtype="same_topic_no_trade",
    )
    assert r.relationship_subtype == "same_topic_no_trade"


def test_diagnostic_only_rejects_strategy_eligible_subtype():
    with pytest.raises(ValidationError):
        DiagnosticOnlyRelationshipRow(
            relationship_id="r1",
            relationship_subtype="championship_implies_conference",
        )


# ─── GrossOpportunityRow ──────────────────────────────────────────────────────


def test_gross_opp_rejects_diagnostic_only_subtype():
    with pytest.raises(ValidationError):
        GrossOpportunityRow(
            relationship_id="r1",
            space_id="s",
            space_attribution_status="outcome_space",
            strategy_family="nesting",
            signal_ts_ms=0,
            gross_edge=0.1,
            relationship_subtype="same_topic_no_trade",
        )


def test_gross_opp_accepts_strategy_subtype():
    g = GrossOpportunityRow(
        relationship_id="r1",
        space_id="s",
        space_attribution_status="outcome_space",
        strategy_family="nesting",
        signal_ts_ms=1,
        gross_edge=0.05,
        relationship_subtype="exact_finish_implies_top_n",
    )
    assert g.gross_edge == 0.05


# ─── AcceptedSimulatedTradeRow ────────────────────────────────────────────────


def test_accepted_trade_requires_strategy_family():
    with pytest.raises(ValidationError):
        AcceptedSimulatedTradeRow(
            trade_id="t1",
            relationship_id="r1",
            space_id="s",
            space_attribution_status="outcome_space",
            strategy_family="",  # invalid
        )


def test_accepted_trade_rejects_none_family():
    with pytest.raises(ValidationError):
        AcceptedSimulatedTradeRow(
            trade_id="t1",
            relationship_id="r1",
            space_id="s",
            space_attribution_status="outcome_space",
            strategy_family="None",
        )


def test_accepted_trade_valid():
    t = AcceptedSimulatedTradeRow(
        trade_id="t1",
        relationship_id="r1",
        space_id="s",
        space_attribution_status="outcome_space",
        strategy_family="nesting",
        notional_usdc=5.0,
    )
    assert t.strategy_family == "nesting"
    assert t.notional_usdc == 5.0


# ─── SpaceSummaryRow ──────────────────────────────────────────────────────────


def test_space_summary_default_grade_is_ungraded():
    s = SpaceSummaryRow(space_id="x")
    assert s.space_grade == "UNGRADED"
    assert s.simulated_pnl == 0.0
