"""Tests for the standardised adapters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from polymarket_arb.backtest.resolutions import InferredResolution
from polymarket_arb.backtest.standardised.adapters import (
    adapt_closed_form_simulator_trades,
    adapt_replay_trade_rows,
    load_deepseek_justifications_iterable,
    load_deepseek_justifications_jsonl,
)
from polymarket_arb.research.closed_form_simulators import SimulatedTrade


def _baseline_raw_row(**overrides) -> dict:
    base = {
        "trade_id": "abc123",
        "trade_group_id": "cand-1",
        "candidate_id": "cand-1",
        "run_id": "r1",
        "relationship_id": "rel-1",
        "relationship_type": "mutually_exclusive_category",
        "relationship_subtype": "deepseek_hypothesis_mutex",
        "relationship_family": "hypothesis",
        "context_space_id": "ctx-1",
        "outcome_space_id": "out-1",
        "strategy_lane": "exploratory_context_unreviewed",
        "leg": "a",
        "token_id": "tok-a",
        "market_id": "mkt-a",
        "side": "buy",
        "fill_ts_ms": "1000",
        "fill_price": "0.45",
        "shares": "100",
        "notional_usdc": "45",
        "fees_usdc": "0",
        "slippage_cost_usdc": "0.50",
        "gross_edge": "0.08",
        "net_edge_after_cost": "0.05",
        "execution_model": "price_history_only",
        "hypothesis_source": "deepseek_generative",
        "hypothesis_id": "hyp-7",
    }
    base.update(overrides)
    return base


def test_adapt_replay_trade_rows_attaches_percentages_and_mark_to_market() -> None:
    rows = adapt_replay_trade_rows(
        [_baseline_raw_row()],
        run_id="r1",
        subrun_id="r1__sub",
        source_lane="ai_freereign_deepseek",
        source_agent="deepseek_generative",
        is_ai_generated=True,
        is_exploratory=True,
        reason_default="default reason",
        latest_price_lookup={"tok-a": (5000, 0.50)},
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.source_lane == "ai_freereign_deepseek"
    assert r.is_ai_generated is True
    assert r.entry_price == pytest.approx(0.45)
    assert r.exit_price == pytest.approx(0.50)  # mark from latest_price_lookup
    assert r.mark_price == pytest.approx(0.50)
    assert r.entry_cost_usdc == pytest.approx(45.0)
    # edge-implied PnL = net_edge * stake = 0.05 * 45 = 2.25
    assert r.edge_implied_pnl_usdc == pytest.approx(2.25)
    # edge_implied_return_pct = edge_implied_pnl / entry_cost = 2.25 / 45 = 0.05
    assert r.edge_implied_return_pct == pytest.approx(0.05)
    # mark_to_market_pnl = shares*exit - stake - fees = 100*0.5 - 45 - 0 = 5
    assert r.mark_to_market_pnl_usdc == pytest.approx(5.0)
    assert r.mark_to_market_return_pct == pytest.approx(5.0 / 45.0)
    # holding period: entry_ts=1000, latest_price_ts=5000 → 4000ms
    assert r.holding_period_ms == 4000


def test_replay_adapter_scopes_trade_group_id_by_lane_and_subrun() -> None:
    raw = _baseline_raw_row(trade_group_id="same-candidate", candidate_id="same-candidate")
    baseline = adapt_replay_trade_rows(
        [raw],
        run_id="r1",
        subrun_id="r1__baseline",
        source_lane="rulebook_baseline_deterministic",
        source_agent="deterministic_template_strict",
    )[0]
    aggressive = adapt_replay_trade_rows(
        [raw],
        run_id="r1",
        subrun_id="r1__aggressive",
        source_lane="rulebook_aggressive_deterministic",
        source_agent="deterministic_template_aggressive",
    )[0]
    assert baseline.raw_trade_group_id == "same-candidate"
    assert aggressive.raw_trade_group_id == "same-candidate"
    assert baseline.trade_group_id != aggressive.trade_group_id
    assert len(baseline.trade_group_id) == 32


def test_adapt_replay_attaches_deepseek_justification() -> None:
    justification_map = load_deepseek_justifications_iterable([
        {
            "hypothesis_id": "hyp-7",
            "relationship_type": "mutually_exclusive",
            "why_relationship_may_be_tradeable": "they should sum > 1",
            "why_relationship_may_be_invalid": "diff resolution criteria",
            "evidence_summary": "title wording differs in question A",
            "proposed_simulator": "mutex_yes_yes_overround_simulator",
            "confidence": 0.7,
            "uncertainty_flags": ["different_endpoints"],
            "outside_existing_deterministic_rulebook": True,
            "model_name": "deepseek-r1:8b",
            "prompt_version": "v2",
        }
    ])
    rows = adapt_replay_trade_rows(
        [_baseline_raw_row()],
        run_id="r1",
        subrun_id="r1__deepseek",
        source_lane="ai_freereign_deepseek",
        source_agent="deepseek_generative",
        is_ai_generated=True,
        is_exploratory=True,
        justification_by_hypothesis=justification_map,
    )
    r = rows[0]
    assert r.justification_present is True
    assert r.justification_relationship_claim
    assert r.justification_why_works == "they should sum > 1"
    assert r.justification_why_may_fail == "diff resolution criteria"
    assert r.justification_outside_rulebook is True
    assert r.justification_inside_rulebook is False
    assert r.justification_confidence == pytest.approx(0.7)
    # full justification re-emitted as JSON for audit
    payload = json.loads(r.justification_json)
    assert payload["hypothesis_id"] == "hyp-7"


def test_adapt_replay_rulebook_trade_has_no_justification() -> None:
    raw = _baseline_raw_row(
        hypothesis_source="deterministic_relationship",
        hypothesis_id="",
        relationship_subtype="deterministic_mutex",
    )
    rows = adapt_replay_trade_rows(
        [raw],
        run_id="r1",
        subrun_id="r1__rb",
        source_lane="rulebook_baseline_deterministic",
        source_agent="deterministic_template_strict",
        is_rulebook=True,
    )
    r = rows[0]
    assert r.is_rulebook is True
    assert r.is_ai_generated is False
    assert r.justification_present is False


def test_adapt_closed_form_simulator_trades_supports_wild_diagnostic_overrides() -> None:
    """The wild-diagnostic lane reuses adapt_closed_form_simulator_trades with
    overridden source_lane/source_agent/is_diagnostic flags."""
    t = SimulatedTrade(
        hypothesis_id="h-wild",
        simulator="mutex_yes_yes_overround_simulator",
        relationship_type="invalid_same_topic_only",  # what DeepSeek said
        market_id_a="ma", market_id_b="mb",
        token_id_a="tka", token_id_b="tkb",
        entry_ts_ms=100, exit_ts_ms=200,
        entry_price_a=0.6, entry_price_b=0.6,
        exit_price_a=0.55, exit_price_b=0.55,
        entry_cost_per_dollar=1.2,
        edge_implied_return_pct=0.15, realised_return_pct=0.08,
        slippage_haircut_pct=0.005, notes="mutex overround",
    )
    rows = adapt_closed_form_simulator_trades(
        [t], run_id="r1",
        source_lane="ai_freereign_deepseek_wild_diagnostic",
        source_agent="deepseek_wild_mutex_overround_simulator",
        source_preset="deepseek_wild_diagnostic",
        is_diagnostic=True,
    )
    assert len(rows) == 2
    for r in rows:
        assert r.source_lane == "ai_freereign_deepseek_wild_diagnostic"
        assert r.source_agent == "deepseek_wild_mutex_overround_simulator"
        assert r.is_diagnostic is True
        assert r.is_exploratory is False  # diagnostic takes precedence
        assert r.is_ai_generated is True
        assert r.trade_kind == "closed_form"


def test_adapt_closed_form_simulator_trades_emits_two_legs_per_trade() -> None:
    t = SimulatedTrade(
        hypothesis_id="hyp-9",
        simulator="same_yes_spread_simulator",
        relationship_type="duplicate_same_yes",
        market_id_a="ma",
        market_id_b="mb",
        token_id_a="toka",
        token_id_b="tokb",
        entry_ts_ms=100,
        exit_ts_ms=200,
        entry_price_a=0.40,
        entry_price_b=0.50,
        exit_price_a=0.45,
        exit_price_b=0.46,
        entry_cost_per_dollar=0.45,
        edge_implied_return_pct=0.10,
        realised_return_pct=0.06,
        slippage_haircut_pct=0.005,
        notes="spread collapse",
    )
    rows = adapt_closed_form_simulator_trades(
        [t],
        run_id="r1",
    )
    assert len(rows) == 2
    a, b = rows
    assert a.source_lane == "closed_form_simulator"
    assert a.source_agent == "closed_form_same_yes_spread_simulator"
    assert a.trade_kind == "closed_form"
    assert a.is_ai_generated is True
    assert a.leg == "a"
    assert b.leg == "b"
    assert a.holding_period_ms == 100
    assert a.realised_return_pct == pytest.approx(0.06)


def test_load_deepseek_justifications_jsonl(tmp_path: Path) -> None:
    p = tmp_path / "deepseek_hypotheses.jsonl"
    p.write_text(
        "\n".join([
            json.dumps({
                "hypothesis_id": "h1",
                "relationship_type": "mutually_exclusive",
                "why_relationship_may_be_tradeable": "mech",
                "why_relationship_may_be_invalid": "fail-mode",
                "confidence": 0.4,
            }),
            json.dumps({"hypothesis_id": "h2", "relationship_type": "duplicate_same_yes"}),
            "",  # tolerate blank lines
            "not-json",  # tolerate garbage
        ]),
        encoding="utf-8",
    )
    out = load_deepseek_justifications_jsonl(p)
    assert set(out.keys()) == {"h1", "h2"}
    assert out["h1"].why_trade_may_fail == "fail-mode"
    # missing fields produce a flagged placeholder so the row is still auditable
    assert "flag for manual review" in out["h2"].why_trade_may_fail.lower()


def test_adapter_pays_yes_token_at_one_when_outcome_yes() -> None:
    """The realised PnL flow: bought YES, market resolved YES -> payout = shares * 1.0."""
    raw = _baseline_raw_row(token_id="tok-a", market_id="mkt-a")
    resolution = {
        "mkt-a": InferredResolution(
            market_id="mkt-a",
            token_id_yes="tok-a",
            token_id_no="tok-a-no",
            resolution_ts_ms=2_000_000,
            yes_outcome="yes",
            confidence=0.98,
            inference_method="price_convergence",
        )
    }
    rows = adapt_replay_trade_rows(
        [raw],
        run_id="r1",
        subrun_id="rb",
        source_lane="rulebook_baseline_deterministic",
        source_agent="deterministic_template_strict",
        is_rulebook=True,
        resolution_lookup=resolution,
    )
    r = rows[0]
    # 100 shares * 1.0 - 45 stake - 0 fees = 55 realised PnL
    assert r.realised_pnl_usdc == pytest.approx(55.0)
    # 55 / 45 entry cost = 1.222...
    assert r.realised_return_pct == pytest.approx(55.0 / 45.0)
    assert r.resolution_outcome == "yes"
    assert r.resolution_ts_ms == 2_000_000


def test_adapter_pays_yes_token_at_zero_when_outcome_no() -> None:
    raw = _baseline_raw_row(token_id="tok-a", market_id="mkt-a")
    resolution = {
        "mkt-a": InferredResolution(
            market_id="mkt-a",
            token_id_yes="tok-a",
            token_id_no="tok-a-no",
            resolution_ts_ms=3_000_000,
            yes_outcome="no",
            confidence=0.99,
            inference_method="price_convergence",
        )
    }
    rows = adapt_replay_trade_rows(
        [raw],
        run_id="r1",
        subrun_id="rb",
        source_lane="rulebook_baseline_deterministic",
        source_agent="deterministic_template_strict",
        is_rulebook=True,
        resolution_lookup=resolution,
    )
    r = rows[0]
    # 100 * 0 - 45 = -45 (lost the stake)
    assert r.realised_pnl_usdc == pytest.approx(-45.0)
    assert r.realised_return_pct == pytest.approx(-1.0)
    assert r.resolution_outcome == "no"


def test_adapter_leaves_realised_pnl_none_when_market_unresolved() -> None:
    raw = _baseline_raw_row(token_id="tok-a", market_id="mkt-a")
    resolution = {
        "mkt-a": InferredResolution(
            market_id="mkt-a",
            token_id_yes="tok-a",
            token_id_no="tok-a-no",
            resolution_ts_ms=0,
            yes_outcome="unresolved",
            confidence=0.5,
            inference_method="missing",
        )
    }
    rows = adapt_replay_trade_rows(
        [raw],
        run_id="r1",
        subrun_id="rb",
        source_lane="rulebook_baseline_deterministic",
        source_agent="deterministic_template_strict",
        is_rulebook=True,
        resolution_lookup=resolution,
    )
    r = rows[0]
    assert r.realised_pnl_usdc is None
    assert r.realised_return_pct is None
    assert r.resolution_outcome == "unresolved"


def test_adapter_falls_back_to_default_reason_when_raw_has_none() -> None:
    raw = _baseline_raw_row(reason="")
    rows = adapt_replay_trade_rows(
        [raw],
        run_id="r1",
        subrun_id="x",
        source_lane="rulebook_baseline_deterministic",
        source_agent="deterministic_template_strict",
        reason_default="rulebook deterministic edge violation",
    )
    assert rows[0].reason == "rulebook deterministic edge violation"
