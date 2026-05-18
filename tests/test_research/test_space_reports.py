"""End-to-end tests for the space research report writers + final narrative."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from polymarket_arb.reports.final_strategy_research_report import generate_final_report
from polymarket_arb.reports.space_optimisation_report import generate_space_optimisation_report
from polymarket_arb.reports.space_research_report import generate_space_research_report
from polymarket_arb.research.space_optimisation import run_space_optimisation
from polymarket_arb.research.space_sweep import run_space_sweep

_GRID_PATH = Path(__file__).resolve().parents[2] / "configs" / "research_presets" / "space_optimisation_grid_v1.yaml"


def _seed_run(data_root: Path, run_id: str) -> None:
    """Seed a backtest run with trades / signals / rejected / bundle data."""
    run_dir = data_root / "backtests" / run_id

    # Strict context lane
    strict_dir = run_dir / "context_aware" / "strict_context_valid"
    strict_dir.mkdir(parents=True, exist_ok=True)

    _write_csv(strict_dir / "trades.csv", [
        {
            "trade_id": "t1", "relationship_id": "r1",
            "outcome_space_id": "nba_finals",
            "relationship_subtype": "championship_implies_conference",
            "relationship_family": "nesting",
            "strategy_family": "nesting",
            "leg": "a", "notional_usdc": "10", "net_edge_after_cost": "0.05", "gross_edge": "0.08",
            "first_entry_or_reentry": "first", "violation_window_id": "w1",
        },
        {
            "trade_id": "t1", "relationship_id": "r1",
            "outcome_space_id": "nba_finals",
            "relationship_subtype": "championship_implies_conference",
            "relationship_family": "nesting",
            "strategy_family": "nesting",
            "leg": "b", "notional_usdc": "10", "net_edge_after_cost": "0.05", "gross_edge": "0.08",
            "first_entry_or_reentry": "first", "violation_window_id": "w1",
        },
    ])
    _write_csv(strict_dir / "signals.csv", [
        {
            "relationship_id": "r1",
            "outcome_space_id": "nba_finals",
            "relationship_subtype": "championship_implies_conference",
            "relationship_family": "nesting",
            "accepted_for_simulation": "true",
            "gross_edge": "0.08",
        },
    ])
    _write_csv(strict_dir / "rejected_candidates.csv", [
        {
            "relationship_id": "r99",
            "outcome_space_id": "nba_finals",
            "relationship_subtype": "championship_implies_conference",
            "relationship_family": "nesting",
            "rejection_reason": "missing_price_history",
        },
    ])
    _write_json(strict_dir / "funnel_audit.json", {"counts": {}})
    _write_json(strict_dir / "metrics.json", {})

    # Bundle data
    bundle_dir = run_dir / "template_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(bundle_dir / "opportunities.csv", [
        {
            "outcome_space_id": "world_cup_2026",
            "best_executable_basket": "buy_all_no",
            "candidate_count_observed": "32",
            "completeness_status": "complete",
            "sum_yes_prices": "1.05",
            "gross_edge": "0.02",
            "net_edge_after_costs": "0.01",
        },
    ])
    _write_csv(bundle_dir / "trades.csv", [
        {
            "trade_id": "bt1",
            "outcome_space_id": "world_cup_2026",
            "candidate": "Argentina",
            "market_id": "m_arg",
            "token_id": "tok_arg",
            "basket": "buy_all_no",
            "fill_ts_ms": "0",
            "fill_price": "0.5",
            "shares": "20",
            "notional_usdc": "10",
            "fees_usdc": "0.05",
        },
    ])


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({k for r in rows for k in r})
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ─── space_research_report ────────────────────────────────────────────────────


def test_sweep_report_writes_all_files(tmp_data_root):
    _seed_run(tmp_data_root, "rid")
    result = run_space_sweep(data_root=tmp_data_root, run_id="rid")
    out_dir = generate_space_research_report(result)

    expected = {
        "space_leaderboard.csv",
        "space_blockers.csv",
        "space_strategy_summary.csv",
        "space_examples.md",
        "space_grades.md",
        "accepted_trades_by_space.csv",
        "blocked_opportunities_by_space.csv",
        "bundle_diagnostics_by_space.csv",
        "report.md",
    }
    for f in expected:
        assert (out_dir / f).exists(), f"missing {f}"


def test_sweep_leaderboard_has_nba_space(tmp_data_root):
    _seed_run(tmp_data_root, "rid2")
    result = run_space_sweep(data_root=tmp_data_root, run_id="rid2")
    out_dir = generate_space_research_report(result)

    rows = list(csv.DictReader((out_dir / "space_leaderboard.csv").open()))
    space_ids = {r["space_id"] for r in rows}
    assert "nba_finals" in space_ids


def test_sweep_no_diagnostic_only_in_trade_csv(tmp_data_root):
    """Critical contract: diagnostic-only subtypes never appear in trade CSV."""
    run_id = "rid_diag"
    run_dir = tmp_data_root / "backtests" / run_id / "context_aware" / "strict_context_valid"
    _write_csv(run_dir / "trades.csv", [
        {
            "trade_id": "td", "relationship_id": "rd",
            "outcome_space_id": "junk_space",
            "relationship_subtype": "same_topic_no_trade",
            "relationship_family": "same_topic_no_trade",
            "strategy_family": "",
            "leg": "a", "notional_usdc": "5",
        },
    ])
    _write_json(run_dir / "funnel_audit.json", {"counts": {}})

    result = run_space_sweep(data_root=tmp_data_root, run_id=run_id)
    out_dir = generate_space_research_report(result)
    rows = list(csv.DictReader((out_dir / "accepted_trades_by_space.csv").open()))
    assert rows == [], "diagnostic-only subtype leaked into accepted_trades_by_space"


# ─── optimisation report ──────────────────────────────────────────────────────


def test_optimisation_report_writes_all_files(tmp_data_root):
    _seed_run(tmp_data_root, "rid3")
    sweep = run_space_sweep(data_root=tmp_data_root, run_id="rid3")
    sweep_out = generate_space_research_report(sweep)
    leaderboard_csv = sweep_out / "space_leaderboard.csv"

    opt = run_space_optimisation(
        data_root=tmp_data_root,
        leaderboard_csv=leaderboard_csv,
        grid_path=_GRID_PATH,
        run_id="opt_rid",
        top_n=2,
        sampling="slim",
    )
    out_dir = generate_space_optimisation_report(opt)

    for f in (
        "optimisation_grid_results.csv",
        "best_params_by_space.csv",
        "robustness_summary.csv",
        "report.md",
    ):
        assert (out_dir / f).exists(), f"missing {f}"


# ─── final report ─────────────────────────────────────────────────────────────


def test_final_report_writes_numbered_sections(tmp_data_root):
    _seed_run(tmp_data_root, "final_rid")
    sweep = run_space_sweep(data_root=tmp_data_root, run_id="final_rid")
    generate_space_research_report(sweep)

    path = generate_final_report(
        data_root=tmp_data_root,
        sweep_run_id="final_rid",
        optimisation_run_id=None,
    )
    text = path.read_text(encoding="utf-8")
    for i in range(1, 22):
        assert f"## {i}. " in text, f"section {i} missing"


def test_final_report_disclaimer_present(tmp_data_root):
    _seed_run(tmp_data_root, "final_rid_d")
    sweep = run_space_sweep(data_root=tmp_data_root, run_id="final_rid_d")
    generate_space_research_report(sweep)
    path = generate_final_report(
        data_root=tmp_data_root,
        sweep_run_id="final_rid_d",
    )
    text = path.read_text(encoding="utf-8")
    assert "RESEARCH-ONLY" in text
    assert "No live trading" in text or "no live trading" in text.lower()


def test_final_report_drops_robust_candidate_for_tiny_pnl(tmp_data_root):
    run_id = "final_g"
    sweep_dir = tmp_data_root.parent / "reports" / "space_sweep" / run_id
    _write_csv(sweep_dir / "space_leaderboard.csv", [
        {
            "space_id": "sports_championship_conference_progression",
            "space_grade": "G_ECONOMICALLY_TINY_SIGNAL",
            "accepted_trade_count": "4",
            "distinct_relationships_traded": "3",
            "independent_violation_windows": "1",
            "simulated_pnl": "5.98",
            "median_trade_return_pct": "0.0115",
            "total_trade_cost": "520",
            "total_return_pct": "0.0115",
        },
    ])
    _write_csv(sweep_dir / "space_blockers.csv", [])
    _write_csv(sweep_dir / "accepted_trades_by_space.csv", [])
    _write_json(sweep_dir / "report.md", {"report_integrity": "ok", "credibility": "exploratory_only_not_credible"})

    path = generate_final_report(data_root=tmp_data_root, sweep_run_id=run_id)
    text = path.read_text(encoding="utf-8").lower()
    assert "structurally robust but economically tiny" in text
    assert "robust candidate" not in text
    assert "g_economically_tiny_signal" in text
