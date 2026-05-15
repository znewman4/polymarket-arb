"""``polymarket-arb diagnostic ...`` — data-pipeline diagnostic commands.

RESEARCH-ONLY / DIAGNOSTIC-ONLY. Not trading advice.
All outputs are labelled diagnostic_only_not_credible and can never produce
credible_positive results. These commands exist solely to audit data coverage
and validate the simulated wallet machinery.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import click

from ..settings import Settings

_NOTE = "RESEARCH-ONLY / DIAGNOSTIC-ONLY. Not trading advice."


@click.group(name="diagnostic")
def diagnostic_cmd() -> None:
    """Data-pipeline diagnostics and bypass backtests (research-only)."""


# ── coverage-audit ────────────────────────────────────────────────────────────


@diagnostic_cmd.command(name="coverage-audit")
@click.option("--output", "output_dir", default=None, type=click.Path(),
              help="Directory for CSV + markdown output (default: data/reports/coverage_audit/).")
@click.pass_context
def coverage_audit_cmd(ctx: click.Context, output_dir: str | None) -> None:
    """Audit every semantic relationship for market-data coverage.

    Checks Gamma metadata, CLOB token IDs, price history, backfill coverage
    score, and context decisions. Reports the first/worst blocker per
    relationship so you can see exactly where the data pipeline breaks down.

    RESEARCH-ONLY / DIAGNOSTIC-ONLY.
    """
    settings: Settings = ctx.obj["settings"]
    from ..backtest.coverage_audit import run_coverage_audit
    from ..reports.coverage_audit_report import generate_coverage_audit_report

    click.echo(f"[diagnostic] {_NOTE}")
    rows = run_coverage_audit(settings.data_root)
    click.echo(f"[diagnostic] Audited {len(rows)} relationships.")

    out = Path(output_dir) if output_dir else settings.data_root / "reports" / "coverage_audit"
    csv_path, md_path = generate_coverage_audit_report(rows, out)

    both_ph = sum(1 for r in rows if r.both_have_price_history)
    no_blocker = sum(1 for r in rows if r.final_blocker == "none")

    from collections import Counter
    blockers = Counter(r.final_blocker for r in rows)

    click.echo(f"  total: {len(rows)}")
    click.echo(f"  both have price history: {both_ph}")
    click.echo(f"  fully covered (no blocker): {no_blocker}")
    click.echo("  top blockers:")
    for blocker, count in blockers.most_common(8):
        click.echo(f"    {count:3d}  {blocker}")
    click.echo(f"  CSV:  {csv_path}")
    click.echo(f"  MD:   {md_path}")


# ── diagnostic backtest ───────────────────────────────────────────────────────


@diagnostic_cmd.command(name="backtest")
@click.option("--bypass-lane/--no-bypass-lane", default=True, show_default=True,
              help="Bypass review lane / eligibility checks.")
@click.option("--bypass-confidence/--no-bypass-confidence", default=True, show_default=True,
              help="Treat min-confidence as 0.0.")
@click.option("--bypass-coverage/--no-bypass-coverage", default=True, show_default=True,
              help="Skip coverage score / recommended_for_backtest checks.")
@click.option("--all-statuses/--accepted-only", "include_all", default=False, show_default=True,
              help="Include rejected / needs_manual_review relationships.")
@click.option("--starting-cash", type=float, default=10_000.0, show_default=True)
@click.option("--slippage-bps", type=int, default=50, show_default=True)
@click.option("--fee-bps", type=int, default=0, show_default=True)
@click.option("--min-gross-edge", type=float, default=0.02, show_default=True)
@click.option("--min-net-edge", type=float, default=0.01, show_default=True)
@click.option("--run-id", default=None)
@click.option("--output", "output_dir", default=None, type=click.Path(),
              help="Override the report output directory.")
@click.pass_context
def diagnostic_backtest_cmd(
    ctx: click.Context,
    bypass_lane: bool,
    bypass_confidence: bool,
    bypass_coverage: bool,
    include_all: bool,
    starting_cash: float,
    slippage_bps: int,
    fee_bps: int,
    min_gross_edge: float,
    min_net_edge: float,
    run_id: str | None,
    output_dir: str | None,
) -> None:
    """Run the diagnostic bypass context-aware backtest.

    Uses real discovered semantic relationships. Bypass flags relax production
    guardrails to reveal whether failures are data-driven or strategy-driven.
    All results are labelled diagnostic_only_not_credible.

    RESEARCH-ONLY / DIAGNOSTIC-ONLY.
    """
    settings: Settings = ctx.obj["settings"]
    from ..backtest.diagnostic_replay import run_diagnostic_backtest
    from ..reports.diagnostic_backtest_report import generate_diagnostic_report
    from ..strategies.models import DiagnosticBacktestConfig

    click.echo(f"[diagnostic] {_NOTE}")

    cfg = DiagnosticBacktestConfig(
        run_id=run_id or uuid.uuid4().hex,
        bypass_review_lane_checks=bypass_lane,
        bypass_min_confidence=bypass_confidence,
        bypass_coverage_threshold=bypass_coverage,
        include_all_validation_statuses=include_all,
        starting_cash_usdc=Decimal(str(starting_cash)),
        slippage_bps=Decimal(str(slippage_bps)),
        fee_bps=Decimal(str(fee_bps)),
        min_gross_edge=min_gross_edge,
        min_net_edge=min_net_edge,
        include_auto_approved=True,
        lane="all_context_research",
    )

    click.echo(
        f"[diagnostic] bypass_lane={bypass_lane} bypass_confidence={bypass_confidence} "
        f"bypass_coverage={bypass_coverage} all_statuses={include_all}"
    )

    result = run_diagnostic_backtest(settings.data_root, cfg)
    m = result["metrics"]
    funnel_counts = result["funnel"]["counts"]

    click.echo(f"  run_id: {result['run_id']}")
    click.echo(f"  credibility: {m['credibility_label']}")
    click.echo(f"  relationships considered: {m['relationships_considered']}")
    click.echo(f"  positions opened: {m['positions_opened']}")
    click.echo(f"  net_pnl: {m['net_pnl_usdc']} USDC")
    click.echo(f"  gross_violations (all rels): {funnel_counts.get('gross_violations', 0)}")
    click.echo(f"  price_history_present: {funnel_counts.get('price_history_present', 0)}")

    out = Path(output_dir) if output_dir else settings.data_root / "reports" / "diagnostic"
    md_path = generate_diagnostic_report(result, out)
    click.echo(f"  report: {md_path}")
    click.echo(f"  output_dir: {result['output_dir']}")


# ── experiment: audit → backtest → combined report ────────────────────────────


@diagnostic_cmd.command(name="experiment")
@click.option("--starting-cash", type=float, default=10_000.0, show_default=True)
@click.option("--slippage-bps", type=int, default=50, show_default=True)
@click.option("--fee-bps", type=int, default=0, show_default=True)
@click.option("--run-id", default=None)
@click.option("--output", "output_dir", default=None, type=click.Path())
@click.pass_context
def diagnostic_experiment_cmd(
    ctx: click.Context,
    starting_cash: float,
    slippage_bps: int,
    fee_bps: int,
    run_id: str | None,
    output_dir: str | None,
) -> None:
    """Run the full diagnostic experiment: coverage audit → bypass backtest → combined report.

    Step 1: Coverage audit — identifies exactly where each relationship fails.
    Step 2: Diagnostic backtest — all bypasses on, uses real relationships.
    Step 3: Writes combined report to output directory.

    All results are labelled diagnostic_only_not_credible.

    RESEARCH-ONLY / DIAGNOSTIC-ONLY.
    """
    settings: Settings = ctx.obj["settings"]
    from ..backtest.coverage_audit import run_coverage_audit
    from ..backtest.diagnostic_replay import run_diagnostic_backtest
    from ..reports.coverage_audit_report import generate_coverage_audit_report
    from ..reports.diagnostic_backtest_report import generate_diagnostic_report
    from ..strategies.models import DiagnosticBacktestConfig

    click.echo(f"[diagnostic-experiment] {_NOTE}")

    exp_run_id = run_id or uuid.uuid4().hex
    out = Path(output_dir) if output_dir else settings.data_root / "reports" / "diagnostic_experiment" / exp_run_id
    out.mkdir(parents=True, exist_ok=True)

    # Step 1: coverage audit
    click.echo("[diagnostic-experiment] Step 1/2: coverage audit ...")
    audit_rows = run_coverage_audit(settings.data_root)
    _csv_path, md_path = generate_coverage_audit_report(audit_rows, out / "coverage_audit")
    both_ph = sum(1 for r in audit_rows if r.both_have_price_history)
    click.echo(f"  Audited {len(audit_rows)} relationships, {both_ph} with price history on both sides.")
    click.echo(f"  Coverage audit: {md_path}")

    # Step 2: diagnostic backtest (all bypasses on)
    click.echo("[diagnostic-experiment] Step 2/2: diagnostic bypass backtest ...")
    cfg = DiagnosticBacktestConfig(
        run_id=exp_run_id,
        bypass_review_lane_checks=True,
        bypass_min_confidence=True,
        bypass_coverage_threshold=True,
        include_all_validation_statuses=False,
        starting_cash_usdc=Decimal(str(starting_cash)),
        slippage_bps=Decimal(str(slippage_bps)),
        fee_bps=Decimal(str(fee_bps)),
        include_auto_approved=True,
        lane="all_context_research",
    )
    bt_result = run_diagnostic_backtest(settings.data_root, cfg)
    m = bt_result["metrics"]
    bt_md = generate_diagnostic_report(bt_result, out / "backtest_report")

    click.echo(f"  credibility: {m['credibility_label']}")
    click.echo(f"  relationships considered: {m['relationships_considered']}")
    click.echo(f"  price_history_present: {bt_result['funnel']['counts'].get('price_history_present', 0)}")
    click.echo(f"  gross_violations: {bt_result['funnel']['counts'].get('gross_violations', 0)}")
    click.echo(f"  positions opened: {m['positions_opened']}")
    click.echo(f"  net_pnl: {m['net_pnl_usdc']} USDC")

    # Step 3: combined summary
    _write_combined_summary(
        out / "experiment_summary.md",
        exp_run_id=exp_run_id,
        audit_rows=audit_rows,
        bt_result=bt_result,
        starting_cash=starting_cash,
        slippage_bps=slippage_bps,
    )

    click.echo(f"  Backtest report: {bt_md}")
    click.echo(f"  Combined summary: {out / 'experiment_summary.md'}")
    click.echo(f"  All outputs: {out}")


@diagnostic_cmd.command(name="coverage-debug")
@click.option("--min-score", type=float, default=0.45, show_default=True,
              help="Minimum coverage score to include.")
@click.option("--max-score", type=float, default=0.65, show_default=True,
              help="Maximum coverage score to include.")
@click.option("--output", "output_dir", default=None, type=click.Path(),
              help="Output directory for CSV + markdown.")
@click.pass_context
def coverage_debug_cmd(
    ctx: click.Context,
    min_score: float,
    max_score: float,
    output_dir: str | None,
) -> None:
    """Produce a per-market debug report for the 0.45-0.65 coverage-score cluster.

    Identifies the root cause for each market in the borderline range:
    missing NLP pipeline, insufficient price points, large gaps, etc.

    RESEARCH-ONLY / DIAGNOSTIC-ONLY.
    """
    import csv as _csv
    from collections import Counter
    from datetime import datetime, timezone

    settings: Settings = ctx.obj["settings"]
    from ..storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository

    rows = [
        r for r in ParquetBackfillCoverageRepository(settings.data_root).iter_latest()
        if min_score <= r.coverage_score <= max_score
    ]
    click.echo(f"[coverage-debug] {len(rows)} markets with coverage in [{min_score:.2f}, {max_score:.2f}]")

    def _diagnose(r) -> str:
        if not r.has_price_history:
            return "price_history_missing"
        if not r.has_semantics and not r.has_rulebook_score and not r.has_implications:
            return "missing_nlp_pipeline — run backfill semantic-pipeline"
        if r.price_points_count < 50:
            return f"insufficient_price_points (n={r.price_points_count}, need>=50)"
        if r.largest_price_gap_ms > 7 * 24 * 3600 * 1000:
            return f"large_data_gap ({r.largest_price_gap_ms // 86400000}d gap)"
        if not r.has_semantics:
            return "missing_semantics — run NLP pipeline"
        return "coverage_threshold_too_strict_or_market_too_new"

    debug_rows = []
    for r in rows:
        debug_rows.append({
            "market_id": r.market_id,
            "question": r.question,
            "coverage_score": r.coverage_score,
            "recommended_for_backtest": r.recommended_for_backtest,
            "has_gamma": r.has_gamma,
            "has_price_history": r.has_price_history,
            "price_points_count": r.price_points_count,
            "has_semantics": r.has_semantics,
            "has_rulebook_score": r.has_rulebook_score,
            "has_implications": r.has_implications,
            "first_price_ts_ms": r.first_price_ts_ms,
            "last_price_ts_ms": r.last_price_ts_ms,
            "largest_price_gap_ms": r.largest_price_gap_ms,
            "exclusion_reasons": r.exclusion_reasons_json,
            "diagnosis": _diagnose(r),
        })

    diag_counts = Counter(d["diagnosis"] for d in debug_rows)
    click.echo("  diagnosis breakdown:")
    for diag, count in diag_counts.most_common():
        click.echo(f"    {count:5d}  {diag}")

    out = Path(output_dir) if output_dir else settings.data_root / "reports" / "coverage_debug"
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    csv_path = out / f"coverage_debug_{ts}.csv"
    if debug_rows:
        fields = list(debug_rows[0].keys())
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = _csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(debug_rows)
    else:
        csv_path.write_text("", encoding="utf-8")

    md_path = out / f"coverage_debug_{ts}.md"
    lines = [
        f"# Coverage Debug Report — {ts}",
        "",
        f"> RESEARCH-ONLY / DIAGNOSTIC-ONLY. Score range [{min_score:.2f}, {max_score:.2f}]. {len(rows)} markets.",
        "",
        "## Diagnosis breakdown",
        "",
        "| Diagnosis | Count |",
        "| --- | --- |",
    ]
    for diag, count in diag_counts.most_common():
        lines.append(f"| `{diag}` | {count} |")
    lines += [
        "",
        "## Per-market detail",
        "",
        "| market_id | score | has_ph | price_pts | has_nlp | diagnosis |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for d in debug_rows:
        has_nlp = d["has_semantics"] or d["has_rulebook_score"] or d["has_implications"]
        lines.append(
            f"| `{str(d['market_id'])[:20]}` "
            f"| {d['coverage_score']:.3f} "
            f"| {'✓' if d['has_price_history'] else '✗'} "
            f"| {d['price_points_count']} "
            f"| {'✓' if has_nlp else '✗'} "
            f"| `{d['diagnosis']}` |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    click.echo(f"  CSV: {csv_path}")
    click.echo(f"  MD:  {md_path}")


def _write_combined_summary(
    path: Path,
    *,
    exp_run_id: str,
    audit_rows: list,
    bt_result: dict,
    starting_cash: float,
    slippage_bps: int,
) -> None:
    from collections import Counter
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    m = bt_result["metrics"]
    fc = bt_result["funnel"]["counts"]
    per_rel: list[dict] = bt_result.get("per_rel_funnel", [])

    blocker_counts = Counter(r.final_blocker for r in audit_rows)
    ph_rels = sum(1 for r in audit_rows if r.both_have_price_history)

    lines = [
        f"# Diagnostic Experiment Summary — {ts}",
        "",
        f"> RESEARCH-ONLY / DIAGNOSTIC-ONLY. Not trading advice. run_id=`{exp_run_id}`",
        "",
        "## Conclusions",
        "",
        "| Question | Answer |",
        "| --- | --- |",
        f"| Relationships with price history on both sides | {ph_rels} / {len(audit_rows)} |",
        f"| Relationships reaching tick evaluation | {fc.get('aligned_price_series', 0)} |",
        f"| Gross price violations found | {fc.get('gross_violations', 0)} |",
        f"| Positions opened by simulated wallet | {m['positions_opened']} |",
        f"| Net PnL (diagnostic, not credible) | {m['net_pnl_usdc']} USDC |",
        f"| Primary failure mode | {blocker_counts.most_common(1)[0][0] if blocker_counts else 'n/a'} |",
        "",
        "## Audit blocker breakdown",
        "",
        "| Blocker | Count |",
        "| --- | --- |",
    ]
    for blocker, count in blocker_counts.most_common():
        lines.append(f"| `{blocker}` | {count} |")

    lines += [
        "",
        "## Backtest configuration",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| starting_cash_usdc | {starting_cash} |",
        f"| slippage_bps | {slippage_bps} |",
        "| bypass_review_lane_checks | True |",
        "| bypass_min_confidence | True |",
        "| bypass_coverage_threshold | True |",
        f"| credibility_label | `{m['credibility_label']}` |",
        "",
        "## Per-relationship wallet outcomes",
        "",
        "| relationship_id | violations | opened | closed | realized_pnl | blocker |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in sorted(per_rel, key=lambda x: -int(x.get("gross_violations", 0))):
        rid = str(r.get("relationship_id", ""))[:20]
        lines.append(
            f"| `{rid}` "
            f"| {r.get('gross_violations', 0)} "
            f"| {r.get('trades_opened', 0)} "
            f"| {r.get('trades_closed', 0)} "
            f"| {r.get('realized_pnl_usdc', '0')} "
            f"| `{r.get('final_blocker', '?')}` |"
        )

    path.write_text("\n".join(lines), encoding="utf-8")
