"""Diagnostic backtest report: per-relationship funnel markdown.

RESEARCH-ONLY / DIAGNOSTIC-ONLY. Not trading advice.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LABEL = "RESEARCH-ONLY / DIAGNOSTIC-ONLY — not trading advice"


def generate_diagnostic_report(
    result: dict[str, Any],
    output_dir: Path,
) -> Path:
    """Write a markdown funnel report from a diagnostic backtest result dict.

    Returns the path to the written file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = result.get("run_id", "unknown")
    path = output_dir / f"diagnostic_report_{run_id}_{ts}.md"

    metrics = result.get("metrics", {})
    funnel = result.get("funnel", {})
    counts = funnel.get("counts", {})
    per_rel: list[dict[str, Any]] = result.get("per_rel_funnel", [])

    lines: list[str] = [
        f"# Diagnostic Backtest Report — {ts}",
        "",
        f"> {_LABEL}",
        "",
        "## Run metadata",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| run_id | `{run_id}` |",
        f"| credibility_label | `{metrics.get('credibility_label', 'diagnostic_only_not_credible')}` |",
        f"| bypass_review_lane_checks | {metrics.get('bypass_review_lane_checks', False)} |",
        f"| bypass_min_confidence | {metrics.get('bypass_min_confidence', False)} |",
        f"| bypass_coverage_threshold | {metrics.get('bypass_coverage_threshold', False)} |",
        f"| include_all_validation_statuses | {metrics.get('include_all_validation_statuses', False)} |",
        f"| starting_cash_usdc | {metrics.get('starting_cash_usdc', 0)} |",
        f"| ending_equity_usdc | {metrics.get('ending_equity_usdc', 0)} |",
        f"| net_pnl_usdc | {metrics.get('net_pnl_usdc', 0)} |",
        f"| total_fees_usdc | {metrics.get('total_fees_usdc', 0)} |",
        f"| slippage_bps | {metrics.get('slippage_bps', 0)} |",
        "",
        "## Funnel counts",
        "",
        "| Stage | Count |",
        "| --- | --- |",
    ]
    for key, val in counts.items():
        lines.append(f"| {key} | {val} |")

    lines += [
        "",
        "## Wallet summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Relationships considered | {metrics.get('relationships_considered', 0)} |",
        f"| Positions opened | {metrics.get('positions_opened', 0)} |",
        f"| Positions closed (reversal) | {metrics.get('positions_closed_reversal', 0)} |",
        f"| Positions closed (end-of-window) | {metrics.get('positions_closed_eow', 0)} |",
        f"| Net PnL | {metrics.get('net_pnl_usdc', 0)} USDC |",
        "",
        "## Per-relationship funnel",
        "",
        "| relationship_id | lane | confidence | cov_pair | has_ph | ticks | violations | opened | closed | realized_pnl | mtm_pnl | blocker |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in sorted(per_rel, key=lambda x: -int(x.get("gross_violations", 0))):
        rid = str(r.get("relationship_id", ""))[:20]
        lines.append(
            f"| `{rid}` "
            f"| {r.get('lane', '?')} "
            f"| {float(r.get('final_confidence', 0)):.2f} "
            f"| {float(r.get('coverage_score_pair', 0)):.2f} "
            f"| {'✓' if r.get('has_price_history') else '✗'} "
            f"| {r.get('tick_count', 0)} "
            f"| {r.get('gross_violations', 0)} "
            f"| {r.get('trades_opened', 0)} "
            f"| {r.get('trades_closed', 0)} "
            f"| {r.get('realized_pnl_usdc', '0')} "
            f"| {r.get('mark_to_market_pnl_usdc', '0')} "
            f"| `{r.get('final_blocker', '?')}` |"
        )

    lines += [
        "",
        "## Detailed relationship entries",
        "",
    ]
    for r in per_rel:
        lines += [
            f"### `{r.get('relationship_id', '')[:32]}`",
            f"- **A**: {r.get('question_a', '')}",
            f"- **B**: {r.get('question_b', '')}",
            f"- **Lane**: {r.get('lane', 'unassigned')} | "
            f"**validation**: {r.get('validation_status', '?')} | "
            f"**eligibility**: {r.get('strategy_eligibility_status', '?')}",
            f"- **Confidence**: {float(r.get('final_confidence', 0)):.2f} | "
            f"**Coverage pair**: {float(r.get('coverage_score_pair', 0)):.3f}",
            f"- **Price history**: {'yes' if r.get('has_price_history') else 'no'} | "
            f"**Ticks**: {r.get('tick_count', 0)}",
            f"- **Gross violations**: {r.get('gross_violations', 0)} | "
            f"**Trades opened**: {r.get('trades_opened', 0)} | "
            f"**Trades closed**: {r.get('trades_closed', 0)}",
            f"- **Realized PnL**: {r.get('realized_pnl_usdc', '0')} USDC | "
            f"**MTM PnL**: {r.get('mark_to_market_pnl_usdc', '0')} USDC",
            f"- **Blocker**: `{r.get('final_blocker', '?')}`",
            "",
        ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
