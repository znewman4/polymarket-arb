"""Generate the Strategy Backtest HTML report."""

from __future__ import annotations

import contextlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..storage.parquet.backtest_metrics_repo import ParquetBacktestMetricsRepository
from .charts import bar, histogram
from .html import render_report

_CREDIBILITY_COLOR = {
    "credible_positive": "#2e7d32",
    "inconclusive": "#e65100",
    "not_credible": "#c62828",
    "data_insufficient": "#888888",
}


def _generated_at() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_strategy_backtest_report(
    data_root: Path,
    run_id: str,
    output_dir: Path | None = None,
) -> Path:
    base_dir = output_dir or (data_root.parent / "reports" / "strategy_backtests" / run_id)
    base_dir.mkdir(parents=True, exist_ok=True)

    family_summaries = _load_family_summaries(data_root, run_id)

    # Load metrics
    metrics_repo = ParquetBacktestMetricsRepository(data_root)
    metrics = metrics_repo.get_latest_for_run(run_id)

    # Load from files if repo is empty (prefer file-based)
    backtest_dir = data_root / "backtests" / run_id / "relationship_strategy"
    metrics_file = backtest_dir / "metrics.json"
    funnel_file = backtest_dir / "funnel_audit.json"
    if family_summaries:
        metrics_dict = _combined_metrics(family_summaries)
    elif metrics is None and metrics_file.exists():
        raw = json.loads(metrics_file.read_text(encoding="utf-8"))
        # Use raw dict for rendering
        metrics_dict = raw
    elif metrics is not None:
        from dataclasses import asdict
        metrics_dict = asdict(metrics)
    else:
        metrics_dict = {}

    # Load trades and candidates from files
    trades_file = backtest_dir / "trades.csv"
    signals_file = backtest_dir / "signals.csv"
    rejected_file = backtest_dir / "rejected_candidates.csv"
    equity_file = backtest_dir / "equity_curve.csv"

    trades_df = _load_csv(trades_file)
    signals_df = _load_csv(signals_file)
    rejected_df = _load_csv(rejected_file)
    equity_df = _load_csv(equity_file)

    # Copy CSVs to report dir
    for src_path, name in [
        (trades_file, "trades.csv"),
        (signals_file, "signals.csv"),
        (rejected_file, "rejected_candidates.csv"),
    ]:
        if src_path.exists():
            shutil.copy2(src_path, base_dir / name)

    if metrics_file.exists():
        shutil.copy2(metrics_file, base_dir / "metrics.json")
    funnel_audit = {}
    if funnel_file.exists():
        shutil.copy2(funnel_file, base_dir / "funnel_audit.json")
        with contextlib.suppress(Exception):
            funnel_audit = json.loads(funnel_file.read_text(encoding="utf-8"))

    # Charts
    charts_dir = base_dir
    equity_chart: str | None = None
    rejection_chart: str | None = None
    edge_chart: str | None = None

    if not equity_df.empty and "equity_usdc" in equity_df.columns:
        try:
            from .charts import scatter_or_line
            eq_vals = [float(v) for v in equity_df["equity_usdc"].tolist() if v]
            if eq_vals:
                p = scatter_or_line(
                    list(range(len(eq_vals))), eq_vals,
                    title="Equity Curve",
                    xlabel="Time", ylabel="Equity (USDC)",
                    output_path=charts_dir / "chart_equity.png",
                )
                equity_chart = str(p)
        except Exception:
            pass

    if not signals_df.empty and "net_edge_after_costs" in signals_df.columns:
        try:
            net_edges = [float(v) for v in signals_df["net_edge_after_costs"].tolist() if v]
            if net_edges:
                p = histogram(
                    net_edges,
                    title="Net Edge Distribution",
                    xlabel="Net Edge", ylabel="Count",
                    output_path=charts_dir / "chart_net_edge.png",
                )
                edge_chart = str(p)
        except Exception:
            pass

    if not rejected_df.empty and "rejection_reason" in rejected_df.columns:
        try:
            from collections import Counter
            counts = Counter(rejected_df["rejection_reason"].tolist())
            if counts:
                labels = [k for k, _ in counts.most_common(8)]
                values = [float(counts[k]) for k in labels]
                p = bar(
                    labels, values,
                    title="Rejection Reason Counts",
                    xlabel="Reason", ylabel="Count",
                    output_path=charts_dir / "chart_rejections.png",
                )
                rejection_chart = str(p)
        except Exception:
            pass

    # Example trades section
    example_trades = []
    if not trades_df.empty:
        top_trades = trades_df.head(10)
        for _, row in top_trades.iterrows():
            example_trades.append({
                "token_id": row.get("token_id", ""),
                "market_id": row.get("market_id", ""),
                "fill_price": row.get("fill_price", ""),
                "shares": row.get("shares", ""),
                "notional_usdc": row.get("notional_usdc", ""),
                "resolution_outcome": row.get("resolution_outcome", "unresolved"),
                "realised_pnl_usdc": row.get("realised_pnl_usdc", ""),
            })

    # Rejection reason counts from metrics
    rejection_counts: dict = {}
    with contextlib.suppress(Exception):
        if metrics_dict.get("rejection_reason_counts_json"):
            rejection_counts = json.loads(metrics_dict["rejection_reason_counts_json"])

    # PnL by type
    pnl_by_type: dict = {}
    with contextlib.suppress(Exception):
        if metrics_dict.get("pnl_by_relationship_type_json"):
            pnl_by_type = json.loads(metrics_dict["pnl_by_relationship_type_json"])

    credibility_label = metrics_dict.get("credibility_label", "data_insufficient")
    credibility_color = _CREDIBILITY_COLOR.get(credibility_label, "#888")

    context = {
        "generated_at": _generated_at(),
        "run_id": run_id,
        "metrics": metrics_dict,
        "credibility_label": credibility_label,
        "credibility_rationale": metrics_dict.get("credibility_rationale", ""),
        "credibility_color": credibility_color,
        "example_trades": example_trades,
        "rejection_counts": dict(sorted(rejection_counts.items(), key=lambda x: -x[1])[:10]),
        "pnl_by_type": pnl_by_type,
        "equity_chart": equity_chart,
        "edge_chart": edge_chart,
        "rejection_chart": rejection_chart,
        "has_resolutions": any(t.get("resolution_outcome") not in (None, "", "unresolved")
                               for t in example_trades),
        "funnel_audit": funnel_audit,
        "funnel_counts": funnel_audit.get("counts", {}) if isinstance(funnel_audit, dict) else {},
        "funnel_rejections": funnel_audit.get("rejections", {}) if isinstance(funnel_audit, dict) else {},
        "family_summaries": family_summaries,
        "classification_audit_path": _latest_report_path(
            data_root.parent / "reports" / "classification_audit" / "latest" / "index.html"
        ),
    }

    html_path = render_report("strategy_backtest.html", context, base_dir / "index.html")

    # Latest symlink/copy
    latest_dir = data_root.parent / "reports" / "strategy_backtests" / "latest"
    _update_latest(base_dir, latest_dir)

    return html_path


def _load_family_summaries(data_root: Path, run_id: str) -> list[dict]:
    run_dir = data_root / "backtests" / run_id
    families = ["mutual_exclusion", "nesting", "category_bundle", "dependency", "temporal"]
    summaries: list[dict] = []
    for family in families:
        family_dir = run_dir / family
        metrics_file = family_dir / "metrics.json"
        funnel_file = family_dir / "funnel_audit.json"
        if not metrics_file.exists() and not funnel_file.exists():
            continue
        metrics = _load_json(metrics_file)
        funnel = _load_json(funnel_file)
        counts = funnel.get("counts", {}) if isinstance(funnel, dict) else {}
        rejections = funnel.get("rejections", {}) if isinstance(funnel, dict) else {}
        trades = _int_metric(metrics, "trades_executed")
        gross_violations = int(counts.get("gross_violations_found", 0) or 0)
        blocker = _family_blocker(family, counts, rejections, trades, gross_violations)
        summaries.append({
            "family": family,
            "metrics": metrics,
            "counts": counts,
            "rejections": rejections,
            "trades_executed": trades,
            "net_pnl_usdc": metrics.get("net_pnl_usdc", "0"),
            "credibility_label": metrics.get("credibility_label", "data_insufficient"),
            "credibility_rationale": metrics.get("credibility_rationale", ""),
            "gross_violations_found": gross_violations,
            "blocker": blocker,
            "artifact_dir": str(family_dir),
        })
    return summaries


def _combined_metrics(family_summaries: list[dict]) -> dict:
    total_trades = sum(int(f.get("trades_executed", 0) or 0) for f in family_summaries)
    total_pnl = sum(float(f.get("net_pnl_usdc", 0) or 0) for f in family_summaries)
    labels = [f.get("credibility_label", "data_insufficient") for f in family_summaries]
    label = "data_insufficient"
    if any(v == "credible_positive" for v in labels):
        label = "credible_positive"
    elif any(v == "inconclusive" for v in labels):
        label = "inconclusive"
    elif any(v == "not_credible" for v in labels):
        label = "not_credible"
    return {
        "starting_cash_usdc": "10000",
        "ending_equity_usdc": f"{10000 + total_pnl:.4f}",
        "total_return_pct": total_pnl / 10000 * 100,
        "trades_executed": total_trades,
        "candidates_accepted": sum(
            int((f.get("counts") or {}).get("candidates_accepted_for_simulation", 0) or 0)
            for f in family_summaries
        ),
        "max_drawdown_pct": 0.0,
        "gross_pnl_usdc": f"{total_pnl:.4f}",
        "net_pnl_usdc": f"{total_pnl:.4f}",
        "total_fees_usdc": "0",
        "total_slippage_usdc": "0",
        "relationships_considered": sum(
            int((f.get("counts") or {}).get("relationships_loaded", 0) or 0)
            for f in family_summaries
        ),
        "signals_generated": sum(
            int((f.get("counts") or {}).get("ticks_evaluated", 0) or 0)
            for f in family_summaries
        ),
        "candidates_rejected": 0,
        "avg_gross_edge": 0.0,
        "avg_net_edge": 0.0,
        "credibility_label": label,
        "credibility_rationale": (
            "Combined family report. No alpha is claimed unless family-level "
            "funnels and credibility labels support it."
        ),
    }


def _family_blocker(
    family: str,
    counts: dict,
    rejections: dict,
    trades: int,
    gross_violations: int,
) -> str:
    if trades > 0:
        return "trades executed; inspect trades.csv and metrics.json"
    if family == "category_bundle" and int(counts.get("complete_outcome_spaces", 0) or 0) == 0:
        return "no registry-approved complete outcome spaces"
    if int(counts.get("relationships_loaded", counts.get("accepted_relationships_loaded", 0)) or 0) == 0:
        return "no eligible relationships loaded"
    if int(counts.get("relationships_with_aligned_price_series", 0) or 0) == 0:
        return "no aligned price series"
    if gross_violations == 0:
        return "no gross price violations"
    if int(rejections.get("net_edge_below_threshold", 0) or 0):
        return "violations rejected by costs/net edge"
    if int(rejections.get("incomplete_or_unknown_outcome_space", 0) or 0):
        return "blocked by completeness gating"
    return "no simulated trades after funnel gates"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _int_metric(metrics: dict, key: str) -> int:
    try:
        return int(metrics.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _latest_report_path(path: Path) -> str | None:
    return str(path.resolve()) if path.exists() else None


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str)
    except Exception:
        return pd.DataFrame()


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
