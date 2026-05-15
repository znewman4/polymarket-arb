"""Tests for the strategy backtest HTML report."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from polymarket_arb.reports.strategy_backtest_report import generate_strategy_backtest_report

_TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _write_fixture_metrics(backtest_dir: Path, run_id: str) -> None:
    """Write a minimal metrics.json fixture for testing."""
    backtest_dir.mkdir(parents=True, exist_ok=True)
    metrics = {
        "run_id": run_id,
        "config_hash": "abc123",
        "starting_cash_usdc": "10000",
        "ending_cash_usdc": "9800",
        "ending_equity_usdc": "10200",
        "total_return_pct": 2.0,
        "gross_pnl_usdc": "200",
        "net_pnl_usdc": "150",
        "total_fees_usdc": "30",
        "total_slippage_usdc": "20",
        "relationships_considered": 5,
        "signals_generated": 50,
        "candidates_accepted": 10,
        "candidates_rejected": 40,
        "trades_executed": 10,
        "rejection_reason_counts_json": json.dumps({"net_edge_too_low": 30, "coverage_too_low": 10}),
        "win_rate_when_resolved": None,
        "avg_gross_edge": 0.08,
        "avg_net_edge": 0.05,
        "avg_hold_time_ms": None,
        "max_drawdown_pct": 2.0,
        "sharpe_like": None,
        "pnl_by_relationship_type_json": json.dumps({"nested_a_implies_b": 150.0}),
        "pnl_by_execution_model_json": json.dumps({"price_history_only": 150.0}),
        "pnl_by_confidence_bucket_json": json.dumps({}),
        "null_baseline_pnl_usdc": None,
        "null_baseline_win_rate": None,
        "credibility_label": "data_insufficient",
        "credibility_rationale": "Only 10 trade pairs executed; need >= 30 for credibility.",
        "schema_version": 1,
        "ingested_ts_ms": _TS,
    }
    (backtest_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (backtest_dir / "trades.csv").write_text("trade_id,market_id,fill_price\n")
    (backtest_dir / "signals.csv").write_text("candidate_id,net_edge_after_costs\n")
    (backtest_dir / "rejected_candidates.csv").write_text("candidate_id,rejection_reason\n")
    (backtest_dir / "equity_curve.csv").write_text("ts_ms,equity_usdc\n")
    (backtest_dir / "funnel_audit.json").write_text(json.dumps({
        "counts": {
            "accepted_relationships_loaded": 5,
            "strategy_eligible_relationships": 4,
            "ticks_evaluated": 10,
            "trades_executed": 0,
        },
        "rejections": {
            "no_price_violation": 10,
            "missing_price_history": 1,
        },
    }))


class TestStrategyBacktestReport:
    def test_report_writes_html_and_required_files(self, tmp_data_root):
        """Report generates HTML + metric files."""
        run_id = "test_run_001"
        backtest_dir = tmp_data_root / "backtests" / run_id / "relationship_strategy"
        _write_fixture_metrics(backtest_dir, run_id)

        report_dir = tmp_data_root / "test_strategy_report"
        html_path = generate_strategy_backtest_report(
            tmp_data_root, run_id=run_id, output_dir=report_dir
        )

        assert html_path.exists()
        assert html_path.suffix == ".html"
        assert (report_dir / "metrics.json").exists()

    def test_report_includes_credibility_verdict(self, tmp_data_root):
        """HTML must include credibility label."""
        run_id = "test_run_002"
        backtest_dir = tmp_data_root / "backtests" / run_id / "relationship_strategy"
        _write_fixture_metrics(backtest_dir, run_id)

        report_dir = tmp_data_root / "test_strategy_report2"
        html_path = generate_strategy_backtest_report(
            tmp_data_root, run_id=run_id, output_dir=report_dir
        )

        html_content = html_path.read_text(encoding="utf-8")
        assert "data_insufficient" in html_content.lower() or "DATA_INSUFFICIENT" in html_content

    def test_report_includes_example_trades_section(self, tmp_data_root):
        """HTML must include the Example Trades section."""
        run_id = "test_run_003"
        backtest_dir = tmp_data_root / "backtests" / run_id / "relationship_strategy"
        _write_fixture_metrics(backtest_dir, run_id)

        report_dir = tmp_data_root / "test_strategy_report3"
        html_path = generate_strategy_backtest_report(
            tmp_data_root, run_id=run_id, output_dir=report_dir
        )

        html_content = html_path.read_text(encoding="utf-8")
        assert "Example Trades" in html_content

    def test_report_includes_zoomed_out_analysis(self, tmp_data_root):
        """HTML must include the Zoomed-out Analysis section."""
        run_id = "test_run_004"
        backtest_dir = tmp_data_root / "backtests" / run_id / "relationship_strategy"
        _write_fixture_metrics(backtest_dir, run_id)

        report_dir = tmp_data_root / "test_strategy_report4"
        html_path = generate_strategy_backtest_report(
            tmp_data_root, run_id=run_id, output_dir=report_dir
        )

        html_content = html_path.read_text(encoding="utf-8")
        assert "Zoomed-out" in html_content

    def test_no_thinking_in_report(self, tmp_data_root):
        """Report must not contain <think> content."""
        run_id = "test_run_005"
        backtest_dir = tmp_data_root / "backtests" / run_id / "relationship_strategy"
        _write_fixture_metrics(backtest_dir, run_id)

        report_dir = tmp_data_root / "test_strategy_report5"
        html_path = generate_strategy_backtest_report(
            tmp_data_root, run_id=run_id, output_dir=report_dir
        )

        html_content = html_path.read_text(encoding="utf-8")
        assert "<think>" not in html_content

    def test_report_copies_and_renders_funnel_audit(self, tmp_data_root):
        """HTML includes the strategy funnel audit artifact."""
        run_id = "test_run_006"
        backtest_dir = tmp_data_root / "backtests" / run_id / "relationship_strategy"
        _write_fixture_metrics(backtest_dir, run_id)

        report_dir = tmp_data_root / "test_strategy_report6"
        html_path = generate_strategy_backtest_report(
            tmp_data_root, run_id=run_id, output_dir=report_dir
        )

        html_content = html_path.read_text(encoding="utf-8")
        assert "Strategy Funnel Audit" in html_content
        assert "accepted_relationships_loaded" in html_content
        assert (report_dir / "funnel_audit.json").exists()
