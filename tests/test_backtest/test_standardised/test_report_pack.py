"""Tests for the standardised report-pack writer."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from polymarket_arb.backtest.standardised.contract import (
    StandardisedRunManifest,
    StandardisedTradeRow,
)
from polymarket_arb.backtest.standardised.report_pack import (
    group_performance,
    write_report_pack,
)


def _row(**overrides) -> StandardisedTradeRow:
    base = {
        "trade_id": "t",
        "trade_group_id": "g",
        "run_id": "r1",
        "subrun_id": "s",
        "source_lane": "rulebook_baseline_deterministic",
        "source_agent": "deterministic_template_strict",
        "reason": "edge violation",
        "relationship_id": "rel1",
        "relationship_family": "deterministic_mutex",
        "context_space_id": "ctx-1",
        "entry_ts_ms": 100,
        "exit_ts_ms": 200,
        "holding_period_ms": 100,
        "entry_price": 0.4,
        "exit_price": 0.5,
        "shares": 100.0,
        "stake_usdc": 40.0,
        "notional_usdc": 40.0,
        "fees_usdc": 0.0,
        "entry_cost_usdc": 40.0,
        "edge_implied_pnl_usdc": 2.0,
        "edge_implied_return_pct": 0.05,
        "mark_to_market_pnl_usdc": 10.0,
        "mark_to_market_return_pct": 0.25,
        "trade_cost_adjusted_return_pct": 0.05,
        "is_rulebook": True,
    }
    base.update(overrides)
    return StandardisedTradeRow(**base)


def test_group_performance_sorts_percentage_led_descending() -> None:
    trades = [
        _row(trade_id="t1", trade_group_id="g1", source_lane="ai_freereign_deepseek",
             relationship_family="hypothesis_a", trade_cost_adjusted_return_pct=0.20,
             mark_to_market_return_pct=0.40, edge_implied_pnl_usdc=8.0,
             mark_to_market_pnl_usdc=16.0, is_rulebook=False, is_ai_generated=True),
        _row(trade_id="t2", trade_group_id="g2", source_lane="rulebook_baseline_deterministic",
             relationship_family="deterministic_mutex", trade_cost_adjusted_return_pct=0.05,
             mark_to_market_return_pct=0.10, edge_implied_pnl_usdc=2.0,
             mark_to_market_pnl_usdc=4.0),
    ]
    rows = group_performance(trades, ("source_lane",))
    assert rows[0]["source_lane"] == "ai_freereign_deepseek"
    assert rows[1]["source_lane"] == "rulebook_baseline_deterministic"
    # Win share counts trades with cost-adjusted return > 0
    assert rows[0]["win_share"] == pytest.approx(1.0)
    assert rows[0]["unique_relationships"] >= 1
    assert rows[0]["accepted_trade_count"] == 1


def test_write_report_pack_emits_all_required_files(tmp_path: Path) -> None:
    trades = [
        _row(trade_id="t1", trade_group_id="g1"),
        _row(trade_id="t2", trade_group_id="g2",
             source_lane="ai_freereign_deepseek",
             source_agent="deepseek_generative",
             is_rulebook=False,
             is_ai_generated=True,
             justification_present=True,
             justification_relationship_claim="A and B are mutex",
             justification_why_works="overround edge",
             justification_why_may_fail="resolution criteria diverge",
             justification_outside_rulebook=True,
             hypothesis_id="hyp1"),
        _row(trade_id="t3", trade_group_id="g3",
             source_lane="control_null_baseline",
             source_agent="null_baseline_random_pairs",
             is_rulebook=False, is_control=True, trade_kind="control"),
        _row(trade_id="t4", trade_group_id="g4",
             source_lane="strict_validation",
             source_agent="strict_validation",
             is_rulebook=False, is_strict_validation=True),
        _row(trade_id="t5", trade_group_id="g5",
             source_lane="closed_form_simulator",
             source_agent="closed_form_same_yes_spread_simulator",
             is_rulebook=False, is_ai_generated=True, trade_kind="closed_form"),
    ]
    manifest = StandardisedRunManifest(run_id="rtest", created_ts_ms=1)
    out_dir = tmp_path / "out"
    paths = write_report_pack(out_dir, trades, manifest)
    written = {p.name for p in paths}
    expected = {
        "run_manifest.json",
        "standardised_trade_log.parquet",
        "standardised_trade_log.csv",
        "relationship_family_performance.csv",
        "context_space_performance.csv",
        "source_lane_performance.csv",
        "rulebook_trade_report.md",
        "ai_discovery_report.md",
        "controls_report.csv",
        "main_backtest_report.md",
    }
    assert expected.issubset(written)

    # manifest counts populated
    manifest_payload = json.loads((out_dir / "run_manifest.json").read_text())
    assert manifest_payload["total_trades"] == 5
    assert manifest_payload["trades_by_lane"]["ai_freereign_deepseek"] == 1
    assert manifest_payload["deepseek_trades_with_justification"] == 1

    # CSV is non-empty and has standardised columns
    with (out_dir / "standardised_trade_log.csv").open() as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    assert len(rows) == 5
    cols = set(reader.fieldnames or [])
    for required in (
        "source_lane",
        "source_agent",
        "reason",
        "entry_ts_ms",
        "entry_price",
        "entry_cost_usdc",
        "edge_implied_return_pct",
        "mark_to_market_return_pct",
        "trade_cost_adjusted_return_pct",
        "review_status",
        "needs_manual_review",
        "justification_present",
    ):
        assert required in cols, f"missing required column: {required}"

    # the AI discovery report mentions the justification fields by name
    ai_md = (out_dir / "ai_discovery_report.md").read_text()
    assert "Why it should work" in ai_md
    assert "Why it may fail" in ai_md
    assert "Outside rulebook" in ai_md
    assert "hyp1" in ai_md

    # main report mentions every lane group
    main_md = (out_dir / "main_backtest_report.md").read_text()
    for lane in (
        "rulebook_baseline_deterministic",
        "ai_freereign_deepseek",
        "control_null_baseline",
        "strict_validation",
        "closed_form_simulator",
    ):
        assert lane in main_md


def test_main_report_explicitly_separates_exploratory_strict_diagnostic(tmp_path: Path) -> None:
    trades = [
        _row(trade_id="t1", trade_group_id="g1", is_strict_validation=True,
             source_lane="strict_validation"),
        _row(trade_id="t2", trade_group_id="g2", is_exploratory=True,
             source_lane="ai_freereign_deepseek", is_ai_generated=True, is_rulebook=False),
        _row(trade_id="t3", trade_group_id="g3", is_diagnostic=True,
             source_lane="diagnostic_ultra_loose", is_rulebook=False),
    ]
    manifest = StandardisedRunManifest(run_id="rtest", created_ts_ms=1)
    write_report_pack(tmp_path, trades, manifest)
    md = (tmp_path / "main_backtest_report.md").read_text()
    # Exploratory / strict / diagnostic counts are surfaced separately
    assert "exploratory" in md.lower()
    assert "strict-validation" in md.lower()
    assert "diagnostic" in md.lower()
    # Report must NOT contain promotion/demotion verbs
    for verb in ("auto-promote", "auto-demote", "auto-kill", "promotion_candidate"):
        assert verb not in md.lower()


def test_report_pack_fails_when_funded_lane_group_sizing_differs(tmp_path: Path) -> None:
    trades = [
        _row(trade_id="a1", trade_group_id="g1", source_lane="rulebook_baseline_deterministic",
             entry_cost_usdc=50.0, stake_usdc=50.0, notional_usdc=50.0),
        _row(trade_id="a2", trade_group_id="g1", source_lane="rulebook_baseline_deterministic",
             entry_cost_usdc=50.0, stake_usdc=50.0, notional_usdc=50.0),
        _row(trade_id="b1", trade_group_id="g2", source_lane="rulebook_aggressive_deterministic",
             entry_cost_usdc=2.0, stake_usdc=2.0, notional_usdc=2.0),
        _row(trade_id="b2", trade_group_id="g2", source_lane="rulebook_aggressive_deterministic",
             entry_cost_usdc=2.0, stake_usdc=2.0, notional_usdc=2.0),
    ]
    manifest = StandardisedRunManifest(run_id="rtest", created_ts_ms=1)
    with pytest.raises(ValueError, match="funded replay group sizing"):
        write_report_pack(tmp_path, trades, manifest)
