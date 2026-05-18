"""Smoke test for expanded-universe orchestration."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from polymarket_arb.cli import _subprocess, cli


def _env_for(tmp_path: Path) -> dict[str, str]:
    return {
        "POLYMARKET_ARB_STORAGE__DATA_ROOT": str(tmp_path / "data"),
        "POLYMARKET_ARB_LOGGING__JSON_LOG_PATH": str(tmp_path / "logs" / "test.jsonl"),
    }


def test_expanded_universe_run_skip_discovery_calls_steps_in_order(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    run_dir = data_root / "raw" / "market_universe" / "fixture_disc"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "markets.jsonl").write_text(json.dumps({"payload": {"id": "m1"}}) + "\n", encoding="utf-8")

    calls: list[list[str]] = []

    def _fake_run(args, settings):
        calls.append(list(args))
        if args[:2] == ["research", "final-report"]:
            out = settings.data_root.parent / "reports" / "final_strategy_research" / "expanded_rid"
            out.mkdir(parents=True, exist_ok=True)
            (out / "final_report.md").write_text("ok", encoding="utf-8")

    monkeypatch.setattr(_subprocess, "run_cli_subcommand", _fake_run)

    result = CliRunner().invoke(
        cli,
        [
            "research",
            "expanded-universe-run",
            "--discovery-run-id",
            "fixture_disc",
            "--starting-cash",
            "10000",
            "--preset",
            "exploratory_trade_surface",
            "--run-id",
            "expanded_rid",
            "--skip-discovery",
        ],
        env=_env_for(tmp_path),
    )
    assert result.exit_code == 0, result.output
    assert calls == [
        ["ingest", "discover-spaces", "--discovery-run-id", "fixture_disc"],
        ["backfill", "discovered-universe", "--discovery-run-id", "fixture_disc", "--semantic", "--prices"],
        [
            "strategy",
            "context-aware",
            "backtest",
            "--preset",
            "exploratory_trade_surface",
            "--starting-cash",
            "10000.0",
            "--run-id",
            "expanded_rid",
        ],
        [
            "strategy",
            "template-bundle",
            "backtest",
            "--preset",
            "exploratory_trade_surface",
            "--starting-cash",
            "10000.0",
            "--run-id",
            "expanded_rid",
        ],
        ["research", "space-sweep", "--run-id", "expanded_rid"],
        ["research", "final-report", "--sweep-run-id", "expanded_rid"],
    ]
    assert (tmp_path / "reports" / "final_strategy_research" / "expanded_rid" / "final_report.md").exists()
