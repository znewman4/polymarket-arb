"""Tests for the standardised trade contract.

These cover the acceptance criteria: every accepted trade has a source_lane,
source_agent, reason, entry timestamps/prices, entry cost, percentage returns,
and (for DeepSeek) a structured pre-trade justification.  No automatic
promote/kill/demote fields are present.
"""

from __future__ import annotations

from polymarket_arb.backtest.standardised.contract import (
    SCHEMA_VERSION,
    SOURCE_LANES,
    DeepSeekPreTradeJustification,
    StandardisedRunManifest,
    StandardisedTradeRow,
)


def test_source_lanes_cover_all_required_lanes() -> None:
    required = {
        "rulebook_baseline_deterministic",
        "rulebook_aggressive_deterministic",
        "ai_freereign_deepseek",
        "ai_embedding_only",
        "closed_form_simulator",
        "synthetic_control",
        "control_null_baseline",
        "strict_validation",
        "diagnostic_ultra_loose",
    }
    assert required.issubset(set(SOURCE_LANES))


def test_minimal_trade_row_has_required_fields() -> None:
    row = StandardisedTradeRow(
        trade_id="t1",
        trade_group_id="g1",
        run_id="r1",
        subrun_id="r1__sub",
        source_lane="rulebook_baseline_deterministic",
        source_agent="deterministic_template_strict",
        reason="violation observed",
        entry_ts_ms=10,
        entry_price=0.4,
        shares=100,
        stake_usdc=40.0,
        notional_usdc=40.0,
        fees_usdc=0.0,
        entry_cost_usdc=40.0,
    )
    assert row.schema_version == SCHEMA_VERSION
    assert row.source_lane == "rulebook_baseline_deterministic"
    assert row.source_agent == "deterministic_template_strict"
    assert row.reason
    assert row.entry_ts_ms == 10
    assert row.entry_price == 0.4
    assert row.entry_cost_usdc == 40.0
    assert row.review_status == "unreviewed"
    assert row.needs_manual_review is False


def test_no_promotion_fields_exist_on_contract() -> None:
    """We must NOT have promote/demote/kill fields yet — only neutral review fields."""
    fields = set(StandardisedTradeRow.model_fields.keys())
    forbidden = {
        "rulebook_action",
        "promotion_candidate",
        "kill_or_tighten_candidate",
        "post_trade_verdict",
        "promote",
        "demote",
        "kill",
    }
    assert forbidden.isdisjoint(fields), (
        f"contract leaks promotion-style fields: {fields & forbidden}"
    )
    assert {
        "review_status",
        "review_notes",
        "observed_failure_mode",
        "observed_pattern_tag",
        "needs_manual_review",
    }.issubset(fields)


def test_deepseek_justification_requires_failure_mode_argument() -> None:
    j = DeepSeekPreTradeJustification(
        hypothesis_id="h1",
        relationship_claim="A and B resolve identically",
        why_trade_should_work="prices should converge",
        why_trade_may_fail="resolution criteria differ in fine print",
        confidence=0.6,
        outside_existing_rulebook=True,
        inside_existing_rulebook=False,
    )
    assert j.why_trade_may_fail
    assert j.outside_existing_rulebook is True
    assert j.inside_existing_rulebook is False


def test_manifest_round_trips() -> None:
    m = StandardisedRunManifest(run_id="r1", created_ts_ms=1)
    payload = m.model_dump(mode="json")
    again = StandardisedRunManifest.model_validate(payload)
    assert again.run_id == "r1"
    assert again.schema_version == SCHEMA_VERSION


def test_lane_flag_combinations() -> None:
    """A trade can be any combination of rulebook/AI/exploratory/strict/control/diagnostic."""
    row = StandardisedTradeRow(
        trade_id="t1",
        trade_group_id="g1",
        run_id="r1",
        subrun_id="s",
        source_lane="control_null_baseline",
        source_agent="null_baseline_random_pairs",
        reason="random pair control",
        is_control=True,
        trade_kind="control",
    )
    assert row.is_control is True
    assert row.is_rulebook is False
    assert row.is_ai_generated is False
