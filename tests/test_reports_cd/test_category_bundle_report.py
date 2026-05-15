"""Tests for the category bundle HTML report."""

from __future__ import annotations

import json

from polymarket_arb.reports.category_bundle_report import generate_category_bundle_report


def test_category_bundle_report_renders_sections(tmp_data_root):
    run_id = "category_report_test"
    out_dir = tmp_data_root / "backtests" / run_id / "category_bundle"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(
        json.dumps({
            "run_id": run_id,
            "outcome_spaces_scanned": 1,
            "complete_outcome_spaces": 0,
            "analysis_only_outcome_spaces": 1,
            "opportunities_found": 0,
            "trades_executed": 0,
            "net_pnl_usdc": "0",
            "credibility_label": "data_insufficient",
            "credibility_rationale": "No complete bundles.",
        }),
        encoding="utf-8",
    )
    (out_dir / "funnel_audit.json").write_text(
        json.dumps({"counts": {"ticks_evaluated": 0}, "rejections": {"missing_price_history": 1}}),
        encoding="utf-8",
    )
    (out_dir / "bundle_scan.csv").write_text(
        "outcome_space_id,display_name,candidate_count,known_total_candidates,"
        "completeness_status,missing_candidate_warning,best_executable_basket,"
        "sum_yes_prices,sum_no_prices,net_edge_after_costs,rejection_reason\n"
        "space,Test Space,2,,unknown,known_total_candidates is not configured,none,0.8,,0,"
        "incomplete_or_unknown_outcome_space\n",
        encoding="utf-8",
    )
    (out_dir / "bundle_opportunities.csv").write_text("opportunity_id,accepted_for_simulation\n", encoding="utf-8")
    (out_dir / "trades.csv").write_text("trade_id,basket,candidate,token_id,fill_price,shares,notional_usdc\n", encoding="utf-8")

    html_path = generate_category_bundle_report(tmp_data_root, run_id)

    html = html_path.read_text(encoding="utf-8")
    assert "Category Bundle Report" in html
    assert "Hard-arb Eligible Complete Bundles" in html
    assert "Interesting But Incomplete/Uncertain Bundles" in html
    assert "No simulated trades" in html
    assert (html_path.parent / "metrics.json").exists()
