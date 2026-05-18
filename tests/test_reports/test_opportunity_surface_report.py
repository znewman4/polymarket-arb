"""Tests for the opportunity surface report generator."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from polymarket_arb.reports.opportunity_surface_report import (
    generate_opportunity_surface_report,
)

_EXPECTED_FILES = {
    "summary.md",
    "opportunity_surface.csv",
    "trade_candidates.csv",
    "accepted_simulated_trades.csv",
    "blocked_opportunities.csv",
    "expansion_family_summary.csv",
    "suspicious_matches.csv",
    "before_after_counts.csv",
    "master_report.md",
    "suspicious_match_audit.csv",
    "suspicious_match_audit.md",
}


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({k for row in rows for k in row})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _build_mock_run(data_root: Path, run_id: str) -> None:
    """Write a minimal mock backtest run that the report can consume."""
    lane = "strict_context_valid"
    lane_dir = data_root / "backtests" / run_id / "context_aware" / lane
    lane_dir.mkdir(parents=True, exist_ok=True)

    signals = [
        {
            "relationship_id": "rel_001",
            "relationship_type": "nested_a_implies_b",
            "relationship_subtype": "championship_implies_conference",
            "outcome_space_id": "nba_2026_okc",
            "team_a": "Oklahoma City Thunder",
            "team_b": "Oklahoma City Thunder",
            "competition": "nba",
            "season": "2026",
            "final_confidence": "0.95",
            "signal_ts_ms": 1_700_000_000_000,
            "accepted_for_simulation": "True",
        },
        {
            "relationship_id": "rel_002",
            "relationship_type": "mutually_exclusive_category",
            "relationship_subtype": "sports_title_winners_mutually_exclusive",
            "final_confidence": "0.20",
            "evidence_json": "{}",
            "signal_ts_ms": 1_700_000_001_000,
            "accepted_for_simulation": "False",
        },
    ]
    trades = [
        {
            "trade_id": "t1",
            "relationship_id": "rel_001",
            "token_id": "tok_a",
            "side": "buy",
            "fill_ts_ms": 1_700_000_000_000,
            "notional_usdc": "5",
            "leg": "a",
        },
        {
            "trade_id": "t2",
            "relationship_id": "rel_001",
            "token_id": "tok_b",
            "side": "buy",
            "fill_ts_ms": 1_700_000_000_000,
            "notional_usdc": "5",
            "leg": "b",
        },
    ]
    rejected = [
        {
            "relationship_id": "rel_003",
            "relationship_type": "mutually_exclusive_category",
            "validation_status": "rejected",
            "rejection_reason": "missing_price_history",
        },
        {
            "relationship_id": "rel_004",
            "relationship_type": "nested_a_implies_b",
            "rejection_reason": "alignment_failed",
        },
    ]
    funnel = {
        "counts": {
            "relationships_loaded": 20,
            "price_history_present": 12,
            "aligned_price_series": 8,
            "gross_violations": 4,
            "candidates_accepted": 2,
        }
    }
    metrics = {
        "run_id": run_id,
        "lane": lane,
        "net_pnl_usdc": "10.00",
        "trades_executed": 1,
        "credibility_label": "data_insufficient",
    }

    _write_csv(lane_dir / "signals.csv", signals)
    _write_csv(lane_dir / "trades.csv", trades)
    _write_csv(lane_dir / "rejected_candidates.csv", rejected)
    _write_json(lane_dir / "funnel_audit.json", funnel)
    _write_json(lane_dir / "metrics.json", metrics)

    research_dir = data_root / "backtests" / run_id / "research" / "exploratory_trade_surface"
    research_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        research_dir / "signals.csv",
        [
            {
                "relationship_id": "rel_005",
                "relationship_type": "nested_a_implies_b",
                "relationship_subtype": "threshold_ladder",
                "signal_ts_ms": 1_700_000_002_000,
                "accepted_for_simulation": "False",
                "strategy_lane": "exploratory_context_unreviewed",
                "preset_label": "EXPLORATORY",
            }
        ],
    )
    _write_csv(research_dir / "trades.csv", [])
    _write_csv(research_dir / "rejected_candidates.csv", [])
    _write_json(
        research_dir / "funnel.json",
        {"gross_violations": 1, "relationships_loaded": 1},
    )
    _write_json(
        research_dir / "metrics.json",
        {
            "run_id": run_id,
            "preset_name": "exploratory_trade_surface",
            "credibility_label": "data_insufficient",
        },
    )

    bundle_dir = data_root / "backtests" / run_id / "template_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(bundle_dir / "opportunities.csv", [])
    _write_csv(bundle_dir / "trades.csv", [])
    _write_csv(
        bundle_dir / "bundle_diagnostics.csv",
        [
            {
                "bundle_event_id": "bundle_world_cup",
                "basket": "buy_all_yes",
                "observed_count": "50",
                "known_total": "48",
                "completeness_status": "unknown",
                "blocker": "incomplete_bundle_buy_all_yes_blocked",
            }
        ],
    )
    _write_json(bundle_dir / "funnel.json", {})


# ── tests ─────────────────────────────────────────────────────────────────────


def test_all_eight_files_are_written(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "test_run_001"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(data_root, run_id)

    for fname in _EXPECTED_FILES:
        assert (out_dir / fname).exists(), f"Missing output: {fname}"


def test_output_dir_default_path(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_default_path"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(data_root, run_id)
    expected = tmp_path / "reports" / "opportunity_surface" / run_id
    assert out_dir == expected


def test_output_dir_override(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_override"
    _build_mock_run(data_root, run_id)

    custom_out = tmp_path / "custom_out"
    out_dir = generate_opportunity_surface_report(data_root, run_id, output_dir=custom_out)
    assert out_dir == custom_out
    assert (custom_out / "summary.md").exists()


def test_accepted_simulated_trades_has_expected_rows(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_trades"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(data_root, run_id)
    rows = list(csv.DictReader((out_dir / "accepted_simulated_trades.csv").open()))
    assert len(rows) == 2  # the 2 trade legs we wrote


def test_blocked_opportunities_has_rejected_rows(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_blocked"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(data_root, run_id)
    rows = list(csv.DictReader((out_dir / "blocked_opportunities.csv").open()))
    assert len(rows) == 2
    reasons = {r["rejection_reason"] for r in rows}
    assert "missing_price_history" in reasons
    assert "alignment_failed" in reasons


def test_before_after_counts_has_one_row(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_counts"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(data_root, run_id)
    rows = list(csv.DictReader((out_dir / "before_after_counts.csv").open()))
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == run_id
    assert int(row["relationships_loaded"]) == 21
    assert int(row["gross_violations"]) == 5


def test_summary_md_contains_trade_count(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_summary"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(data_root, run_id)
    text = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert "Simulated trades executed" in text
    assert "RESEARCH-ONLY" in text


def test_summary_md_pnl_is_secondary(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_pnl_secondary"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(data_root, run_id)
    text = (out_dir / "summary.md").read_text(encoding="utf-8")
    # PnL should appear but be clearly flagged as secondary
    assert "SECONDARY" in text


def test_family_summary_ranked_by_violations(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_family"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(data_root, run_id)
    rows = list(csv.DictReader((out_dir / "expansion_family_summary.csv").open()))
    # Row with the accepted signal should appear first (highest gross_violations)
    violations = [int(r["gross_violations"]) for r in rows]
    assert violations == sorted(violations, reverse=True)


def test_report_tolerates_empty_run_dir(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True)
    run_id = "empty_run"

    out_dir = generate_opportunity_surface_report(data_root, run_id)
    # All 8 files should exist even when there's no data
    for fname in _EXPECTED_FILES:
        assert (out_dir / fname).exists(), f"Missing output for empty run: {fname}"


def test_preset_label_appears_in_summary(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_preset_label"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(
        data_root, run_id, preset_label="EXPLORATORY"
    )
    text = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert "EXPLORATORY" in text


def test_master_report_contains_narrative_sections(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_master_report"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(data_root, run_id)
    text = (out_dir / "master_report.md").read_text(encoding="utf-8")

    assert "Main Achievements" in text
    assert "Main Issues" in text
    assert "Main Improvement Points" in text
    assert "| Metric | Value |" in text
    assert "Top Blockers" in text


def test_suspicious_matches_has_expected_columns_and_flags(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_suspicious"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(data_root, run_id)
    rows = list(csv.DictReader((out_dir / "suspicious_matches.csv").open()))

    assert rows
    assert {"relationship_id", "flags", "suggested_action", "source_row_json"} <= set(rows[0])
    all_flags = "; ".join(row["flags"] for row in rows)
    assert "low_confidence" in all_flags
    assert "validation_rejected" in all_flags
    assert "observed_gt_known_total" in all_flags


def test_suspicious_match_audit_samples_expected_buckets(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_audit"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(data_root, run_id)
    rows = list(csv.DictReader((out_dir / "suspicious_match_audit.csv").open()))
    buckets = {row["bucket"] for row in rows}

    assert "accepted_strict" in buckets
    assert "exploratory" in buckets
    assert "rejected" in buckets
    assert "traded" in buckets
    assert "suspicious" in buckets
    assert (out_dir / "suspicious_match_audit.md").exists()


def test_research_replay_rows_are_included_in_opportunity_surface(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_research_rows"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(data_root, run_id)
    rows = list(csv.DictReader((out_dir / "opportunity_surface.csv").open()))

    research_rows = [row for row in rows if row.get("replay_path") == "research"]
    assert research_rows
    assert research_rows[0]["preset"] == "exploratory_trade_surface"


def test_core_csvs_have_expected_columns_and_fixture_rows(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    run_id = "run_columns"
    _build_mock_run(data_root, run_id)

    out_dir = generate_opportunity_surface_report(data_root, run_id)
    expected_columns = {
        "opportunity_surface.csv": {"relationship_id", "signal_ts_ms", "flags", "label"},
        "trade_candidates.csv": {"relationship_id", "accepted_for_simulation", "flags"},
        "accepted_simulated_trades.csv": {"trade_id", "relationship_id", "token_id"},
        "blocked_opportunities.csv": {"relationship_id", "rejection_reason", "flags"},
        "expansion_family_summary.csv": {"strategy_family", "gross_violations"},
        "suspicious_matches.csv": {"relationship_id", "flags", "suggested_action"},
        "before_after_counts.csv": {"run_id", "gross_violations", "trades_executed"},
        "suspicious_match_audit.csv": {"bucket", "source_file", "relationship_id"},
    }
    for filename, columns in expected_columns.items():
        with (out_dir / filename).open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            assert columns <= set(reader.fieldnames or []), filename
            assert list(reader), filename
