"""Context-aware strategy backtest report."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

from .html import render_report
from .tables import df_to_html, write_csv

LANES = [
    "strict_context_valid",
    "reviewed_context_valid",
    "exploratory_context_unreviewed",
    "all_context_research",
]


def generate_context_strategy_backtest_report(
    data_root: Path,
    run_id: str,
    output_dir: Path | None = None,
) -> Path:
    base_dir = output_dir or (data_root.parent / "reports" / "context_strategy_backtests" / run_id)
    base_dir.mkdir(parents=True, exist_ok=True)
    run_dir = data_root / "backtests" / run_id / "context_aware"

    lane_summaries = []
    strict_trades = pd.DataFrame()
    reviewed_trades = pd.DataFrame()
    exploratory_trades = pd.DataFrame()
    rejected_frames = []
    for lane in LANES:
        lane_dir = run_dir / lane
        metrics = _load_json(lane_dir / "metrics.json")
        funnel = _load_json(lane_dir / "funnel_audit.json")
        concentration = _load_json(lane_dir / "concentration.json")
        no_lookahead = _load_json(lane_dir / "no_lookahead_audit.json")
        trades = _load_csv(lane_dir / "trades.csv")
        rejected = _load_csv(lane_dir / "rejected_candidates.csv")
        if not rejected.empty:
            rejected_frames.append(rejected)
        if lane == "strict_context_valid":
            strict_trades = trades
        elif lane == "reviewed_context_valid":
            reviewed_trades = trades
        elif lane == "exploratory_context_unreviewed":
            exploratory_trades = trades
        if metrics or funnel:
            lane_summaries.append({
                "lane": lane,
                "metrics": metrics,
                "funnel": funnel,
                "concentration": concentration,
                "no_lookahead": no_lookahead,
                "artifact_dir": str(lane_dir),
            })

    sensitivity = _load_csv(run_dir / "sensitivity" / "sensitivity_grid.csv")
    null_metrics = _load_json(run_dir / "null_baseline" / "metrics.json")
    rejected_all = pd.concat(rejected_frames, ignore_index=True) if rejected_frames else pd.DataFrame()
    analysis_only = _analysis_only_relationships(data_root)

    write_csv(strict_trades, base_dir / "strict_trades.csv")
    write_csv(reviewed_trades, base_dir / "reviewed_trades.csv")
    write_csv(exploratory_trades, base_dir / "exploratory_trades.csv")
    write_csv(analysis_only, base_dir / "analysis_only_relationships.csv")
    write_csv(rejected_all, base_dir / "rejected_candidates.csv")
    write_csv(sensitivity, base_dir / "sensitivity_grid.csv")
    metrics = _combined_metrics(lane_summaries, null_metrics, sensitivity)
    (base_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    concentration = _combined_concentration(lane_summaries)
    (base_dir / "concentration.json").write_text(json.dumps(concentration, indent=2), encoding="utf-8")
    no_lookahead = _combined_no_lookahead(lane_summaries)
    (base_dir / "no_lookahead_audit.json").write_text(
        json.dumps(no_lookahead, indent=2),
        encoding="utf-8",
    )

    context = {
        "run_id": run_id,
        "metrics": metrics,
        "lane_summaries": lane_summaries,
        "null_metrics": null_metrics,
        "sensitivity_table": _table(sensitivity),
        "strict_table": _table(strict_trades),
        "reviewed_table": _table(reviewed_trades),
        "exploratory_table": _table(exploratory_trades),
        "analysis_only_table": _table(analysis_only),
        "rejected_table": _table(rejected_all),
        "concentration": concentration,
        "no_lookahead": no_lookahead,
    }
    html_path = render_report("context_strategy_backtest.html", context, base_dir / "index.html")
    _update_latest(base_dir, data_root.parent / "reports" / "context_strategy_backtests" / "latest")
    return html_path


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(path)


def _analysis_only_relationships(data_root: Path) -> pd.DataFrame:
    path = data_root / "normalised" / "context_relationship_decisions"
    if not path.exists():
        return pd.DataFrame()
    rows = []
    for file_path in path.glob("dt=*/*.parquet"):
        try:
            df = pd.read_parquet(file_path)
        except Exception:
            continue
        rows.extend(df[df["strategy_lane"] == "analysis_only"].to_dict("records"))
    return pd.DataFrame(rows)


def _combined_metrics(
    lane_summaries: list[dict[str, Any]],
    null_metrics: dict[str, Any],
    sensitivity: pd.DataFrame,
) -> dict[str, Any]:
    strict_reviewed = [
        s for s in lane_summaries
        if s["lane"] in {"strict_context_valid", "reviewed_context_valid"}
    ]
    total_pnl = sum(float((s.get("metrics") or {}).get("net_pnl_usdc", 0) or 0) for s in strict_reviewed)
    total_trades = sum(int((s.get("metrics") or {}).get("trades_executed", 0) or 0) for s in strict_reviewed)
    context_modes = {
        (s.get("metrics") or {}).get("context_time_mode", "ex_post_research")
        for s in strict_reviewed
    }
    positive_cells = 0
    if not sensitivity.empty and "net_pnl_usdc" in sensitivity.columns:
        positive_cells = int((pd.to_numeric(sensitivity["net_pnl_usdc"], errors="coerce") > 0).sum())
    label = "data_insufficient"
    rationale = "strict/reviewed lanes did not produce enough trades"
    if total_trades >= 30 and total_pnl > 0:
        if "ex_post_research" in context_modes:
            label = "inconclusive"
            rationale = "positive result is ex-post research"
        elif positive_cells > 0 and float(null_metrics.get("net_pnl_usdc", 0) or 0) < total_pnl:
            label = "credible_positive"
            rationale = "strict/reviewed lanes beat local gates"
        else:
            label = "inconclusive"
            rationale = "positive result did not pass robustness checks"
    elif total_trades >= 30:
        label = "not_credible"
        rationale = "strict/reviewed lanes are not profitable"
    return {
        "strict_reviewed_net_pnl_usdc": total_pnl,
        "strict_reviewed_trades_executed": total_trades,
        "null_baseline": null_metrics,
        "sensitivity_positive_cells": positive_cells,
        "credibility_label": label,
        "credibility_rationale": rationale,
    }


def _combined_concentration(lane_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    return {s["lane"]: s.get("concentration", {}) for s in lane_summaries}


def _combined_no_lookahead(lane_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    rows = sum(int((s.get("no_lookahead") or {}).get("rows_checked", 0)) for s in lane_summaries)
    violations = sum(int((s.get("no_lookahead") or {}).get("violations", 0)) for s in lane_summaries)
    return {"rows_checked": rows, "violations": violations}


def _table(df: pd.DataFrame) -> str | None:
    if df.empty:
        return None
    return df_to_html(df.head(50))


def _update_latest(run_dir: Path, latest_dir: Path) -> None:
    try:
        if latest_dir.is_symlink():
            latest_dir.unlink()
        elif latest_dir.exists():
            shutil.rmtree(latest_dir)
        latest_dir.parent.mkdir(parents=True, exist_ok=True)
        latest_dir.symlink_to(run_dir.resolve())
    except (OSError, NotImplementedError):
        if latest_dir.exists():
            shutil.rmtree(latest_dir)
        shutil.copytree(run_dir, latest_dir)
