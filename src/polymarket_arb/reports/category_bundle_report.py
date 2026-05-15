"""Generate the Category Bundle HTML report."""

from __future__ import annotations

import contextlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .html import render_report

_CREDIBILITY_COLOR = {
    "credible_positive": "#2e7d32",
    "inconclusive": "#e65100",
    "not_credible": "#c62828",
    "data_insufficient": "#888888",
}


def generate_category_bundle_report(
    data_root: Path,
    run_id: str,
    output_dir: Path | None = None,
) -> Path:
    base_dir = output_dir or (data_root.parent / "reports" / "category_bundles" / run_id)
    base_dir.mkdir(parents=True, exist_ok=True)

    backtest_dir = data_root / "backtests" / run_id / "category_bundle"
    metrics_file = backtest_dir / "metrics.json"
    funnel_file = backtest_dir / "funnel_audit.json"
    scan_file = backtest_dir / "bundle_scan.csv"
    opportunities_file = backtest_dir / "bundle_opportunities.csv"
    trades_file = backtest_dir / "trades.csv"
    equity_file = backtest_dir / "equity_curve.csv"

    metrics = _load_json(metrics_file)
    funnel = _load_json(funnel_file)
    scan_df = _load_csv(scan_file)
    opportunities_df = _load_csv(opportunities_file)
    trades_df = _load_csv(trades_file)

    for src_path, name in [
        (metrics_file, "metrics.json"),
        (funnel_file, "funnel_audit.json"),
        (scan_file, "bundle_scan.csv"),
        (opportunities_file, "bundle_opportunities.csv"),
        (trades_file, "trades.csv"),
        (equity_file, "equity_curve.csv"),
    ]:
        if src_path.exists():
            shutil.copy2(src_path, base_dir / name)

    complete_bundles = _records(
        scan_df[scan_df["completeness_status"] == "complete"].head(25)
        if "completeness_status" in scan_df.columns
        else pd.DataFrame()
    )
    analysis_only_bundles = _records(
        scan_df[scan_df["completeness_status"] != "complete"].head(25)
        if "completeness_status" in scan_df.columns
        else pd.DataFrame()
    )
    accepted_opportunities = _records(
        opportunities_df[opportunities_df["accepted_for_simulation"].astype(str) == "True"].head(25)
        if "accepted_for_simulation" in opportunities_df.columns
        else pd.DataFrame()
    )
    example_trades = _records(trades_df.head(25))

    credibility_label = metrics.get("credibility_label", "data_insufficient")
    context = {
        "generated_at": _generated_at(),
        "run_id": run_id,
        "metrics": metrics,
        "credibility_label": credibility_label,
        "credibility_rationale": metrics.get("credibility_rationale", ""),
        "credibility_color": _CREDIBILITY_COLOR.get(credibility_label, "#888"),
        "funnel_counts": funnel.get("counts", {}) if isinstance(funnel, dict) else {},
        "funnel_rejections": funnel.get("rejections", {}) if isinstance(funnel, dict) else {},
        "complete_bundles": complete_bundles,
        "analysis_only_bundles": analysis_only_bundles,
        "accepted_opportunities": accepted_opportunities,
        "example_trades": example_trades,
        "has_opportunities_file": opportunities_file.exists(),
        "has_funnel_file": funnel_file.exists(),
    }
    html_path = render_report("category_bundle.html", context, base_dir / "index.html")

    latest_dir = data_root.parent / "reports" / "category_bundles" / "latest"
    _update_latest(base_dir, latest_dir)
    return html_path


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with contextlib.suppress(Exception):
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    with contextlib.suppress(Exception):
        return pd.read_csv(path, dtype=str)
    return pd.DataFrame()


def _records(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    return df.fillna("").to_dict(orient="records")


def _generated_at() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
