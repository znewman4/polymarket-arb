"""Context report generation tests."""

from __future__ import annotations

import json

from polymarket_arb.context.manual_rules import import_manual_rules
from polymarket_arb.reports.context_classification_audit_report import (
    generate_context_classification_audit_report,
)
from polymarket_arb.reports.context_rules_report import generate_context_rules_report
from polymarket_arb.reports.context_strategy_backtest_report import (
    generate_context_strategy_backtest_report,
)


def test_context_reports_exist_and_do_not_persist_thinking_marker(tmp_data_root):
    import_manual_rules(tmp_data_root, "configs/context_spaces/manual_rules_v1.yaml")

    rules_path = generate_context_rules_report(tmp_data_root)
    audit_path = generate_context_classification_audit_report(tmp_data_root)

    run_root = tmp_data_root / "backtests" / "report_run" / "context_aware"
    for lane in ("strict_context_valid", "reviewed_context_valid", "exploratory_context_unreviewed"):
        lane_dir = run_root / lane
        lane_dir.mkdir(parents=True)
        (lane_dir / "metrics.json").write_text(
            json.dumps({
                "lane": lane,
                "context_time_mode": "ex_post_research",
                "trades_executed": 0,
                "net_pnl_usdc": "0",
                "credibility_label": "data_insufficient",
            }),
            encoding="utf-8",
        )
        (lane_dir / "trades.csv").write_text("", encoding="utf-8")
        (lane_dir / "signals.csv").write_text("", encoding="utf-8")
        (lane_dir / "rejected_candidates.csv").write_text("", encoding="utf-8")
        (lane_dir / "funnel_audit.json").write_text("{}", encoding="utf-8")
        (lane_dir / "concentration.json").write_text("{}", encoding="utf-8")
        (lane_dir / "no_lookahead_audit.json").write_text('{"violations": 0}', encoding="utf-8")

    strategy_path = generate_context_strategy_backtest_report(tmp_data_root, run_id="report_run")

    for path in (rules_path, audit_path, strategy_path):
        assert path.exists()
        text = path.read_text(encoding="utf-8").lower()
        assert "<think>" not in text

    html = strategy_path.read_text(encoding="utf-8")
    assert "Strict Context Valid" in html
    assert "Reviewed Context Valid" in html
    assert "Exploratory Context Unreviewed" in html
    assert (strategy_path.parent / "strict_trades.csv").exists()
    assert (strategy_path.parent / "metrics.json").exists()
