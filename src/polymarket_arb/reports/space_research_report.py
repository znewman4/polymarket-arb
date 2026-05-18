"""Space research report writer.

Writes the 9 output files from a SpaceSweepResult:
  space_leaderboard.csv
  space_blockers.csv
  space_strategy_summary.csv
  space_examples.md
  space_grades.md
  accepted_trades_by_space.csv
  blocked_opportunities_by_space.csv
  bundle_diagnostics_by_space.csv
  report.md

RESEARCH-ONLY.  All outputs labelled accordingly.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..research.space_sweep import SpaceSweepResult

_LABEL = "RESEARCH-ONLY — diagnostic / exploratory.  Not trading advice."


_LEADERBOARD_COLS = [
    "space_id", "space_name", "domain", "space_attribution_status",
    "strategy_families_available",
    "market_count", "relationship_count",
    "strict_relationship_count", "exploratory_relationship_count",
    "diagnostic_only_relationship_count",
    "price_history_coverage_pct", "aligned_tick_count",
    "gross_violation_count", "net_violation_count",
    "accepted_trade_count", "raw_fill_count",
    "unique_violation_window_count", "independent_violation_windows",
    "distinct_relationships_traded", "distinct_bundles_traded",
    "simulated_pnl", "average_trade_return_pct", "median_trade_return_pct",
    "total_trade_cost", "total_return_pct",
    "max_drawdown", "total_slippage",
    "avg_edge", "median_edge", "best_edge",
    "worst_trade_pnl", "best_trade_pnl",
    "primary_blocker", "secondary_blocker",
    "suspicious_flag_count", "accepted_trade_suspicious_flag_count",
    "completeness_status", "known_total_candidates",
    "dominant_relationship_share_of_pnl",
    "space_grade", "recommended_next_action",
]


def generate_space_research_report(
    result: SpaceSweepResult,
) -> Path:
    """Write all 9 output files for a space sweep result."""
    out = result.output_dir
    out.mkdir(parents=True, exist_ok=True)

    # 1. space_leaderboard.csv
    rows = []
    for s in sorted(result.summaries, key=lambda x: (-(x.simulated_pnl or 0.0), -x.accepted_trade_count, x.space_id)):
        d = s.model_dump()
        d["strategy_families_available"] = ";".join(d.get("strategy_families_available", []) or [])
        rows.append(d)
    _write_csv(out / "space_leaderboard.csv", rows, columns=_LEADERBOARD_COLS)

    # 2. space_blockers.csv — per-space blocker breakdown
    blocker_rows: list[dict[str, Any]] = []
    blockers_by_space: dict[str, Counter] = defaultdict(Counter)
    for b in result.blocked:
        blockers_by_space[b.space_id][b.blocker_category] += 1
    for space, counts in blockers_by_space.items():
        for cat, n in counts.most_common():
            blocker_rows.append({
                "space_id": space,
                "blocker_category": cat,
                "count": n,
            })
    _write_csv(out / "space_blockers.csv", blocker_rows)

    # 3. space_strategy_summary.csv — counts by (space, strategy_family)
    fam_rows: list[dict[str, Any]] = []
    fam_counts: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"trades": 0, "fills": 0, "pnl": 0.0}
    )
    for t in result.accepted_trades:
        key = (t.space_id, t.strategy_family)
        fam_counts[key]["fills"] += 1
        fam_counts[key]["pnl"] += float(t.net_edge_after_cost or 0.0) * float(t.notional_usdc or 0.0)
    for (space, family), agg in fam_counts.items():
        fam_rows.append({
            "space_id": space,
            "strategy_family": family,
            "raw_fills": agg["fills"],
            "accepted_trades": agg["fills"] // 2,
            "simulated_pnl": round(agg["pnl"], 4),
        })
    _write_csv(out / "space_strategy_summary.csv", fam_rows)

    # 4. accepted_trades_by_space.csv
    trade_rows = [t.model_dump() for t in result.accepted_trades]
    _write_csv(out / "accepted_trades_by_space.csv", trade_rows)

    # 5. blocked_opportunities_by_space.csv
    blocked_rows = [b.model_dump() for b in result.blocked]
    _write_csv(out / "blocked_opportunities_by_space.csv", blocked_rows)

    # 6. bundle_diagnostics_by_space.csv
    bundle_rows = [b.model_dump() for b in result.bundle_diagnostics]
    _write_csv(out / "bundle_diagnostics_by_space.csv", bundle_rows)

    # 7. space_examples.md — sample trades per space
    _write_examples_md(out / "space_examples.md", result)

    # 8. space_grades.md
    _write_grades_md(out / "space_grades.md", result)

    # 9. report.md
    _write_main_report_md(out / "report.md", result)

    return out


# ─── helpers ──────────────────────────────────────────────────────────────────


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    if columns is None:
        fields = sorted({k for row in rows for k in row})
    else:
        # Ensure all rows have all columns
        fields = columns + [c for c in sorted({k for row in rows for k in row}) if c not in columns]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_examples_md(path: Path, result: SpaceSweepResult) -> None:
    lines = [
        f"# Space examples — {_now_iso()}",
        "",
        f"> {_LABEL}",
        f"> run_id: `{result.run_id}`",
        "",
    ]
    by_space: dict[str, list] = defaultdict(list)
    for t in result.accepted_trades:
        by_space[t.space_id].append(t)
    for space, trades in sorted(by_space.items(), key=lambda kv: -len(kv[1])):
        lines += [
            f"## `{space}`",
            "",
            f"_trades shown: {min(len(trades), 5)} of {len(trades)}_",
            "",
            "| trade_id | relationship_id | strategy_family | leg | notional | gross_edge | net_edge | entry |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for t in trades[:5]:
            lines.append(
                f"| `{t.trade_id[:12]}` "
                f"| `{t.relationship_id[:12]}` "
                f"| {t.strategy_family} "
                f"| {t.leg} "
                f"| {t.notional_usdc:.2f} "
                f"| {t.gross_edge:.4f} "
                f"| {t.net_edge_after_cost:.4f} "
                f"| {t.first_entry_or_reentry} |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_grades_md(path: Path, result: SpaceSweepResult) -> None:
    grade_buckets: dict[str, list] = defaultdict(list)
    for s in result.summaries:
        grade_buckets[s.space_grade].append(s)

    order = [
        "A_PROFITABLE_ROBUST_CANDIDATE",
        "B_PROMISING_GATE_BLOCKED",
        "C_INFRASTRUCTURE_BLOCKED",
        "D_VALID_BUT_STRATEGICALLY_WEAK",
        "F_OVERFIT_ONE_OFF",
        "G_ECONOMICALLY_TINY_SIGNAL",
        "E_INVALID_OR_AUDIT_RISK",
        "UNGRADED",
    ]

    lines = [
        f"# Space grades — {_now_iso()}",
        "",
        f"> {_LABEL}",
        f"> run_id: `{result.run_id}`",
        f"> report_integrity: `{result.report_integrity}` | credibility: `{result.credibility}`",
        "",
        "## Counts by grade",
        "",
        "| Grade | Count | Recommended action |",
        "| --- | --- | --- |",
    ]
    for grade in order:
        spaces = grade_buckets.get(grade, [])
        if not spaces:
            continue
        action = spaces[0].recommended_next_action if spaces else ""
        lines.append(f"| `{grade}` | {len(spaces)} | {action[:80]} |")
    lines.append("")

    for grade in order:
        spaces = grade_buckets.get(grade, [])
        if not spaces:
            continue
        lines += [
            f"## {grade} ({len(spaces)} spaces)",
            "",
            "| space_id | trades | distinct_rels | windows | pnl | median_trade_return_pct | primary_blocker | action |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for s in sorted(spaces, key=lambda x: (-x.accepted_trade_count, -x.simulated_pnl, x.space_id))[:30]:
            lines.append(
                f"| `{s.space_id}` "
                f"| {s.accepted_trade_count} "
                f"| {s.distinct_relationships_traded} "
                f"| {s.independent_violation_windows} "
                f"| {s.simulated_pnl:.2f} "
                f"| {s.median_trade_return_pct:.4%} "
                f"| {s.primary_blocker or '-'} "
                f"| {s.recommended_next_action[:60]} |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_main_report_md(path: Path, result: SpaceSweepResult) -> None:
    by_grade = Counter(s.space_grade for s in result.summaries)
    total_trades = sum(s.accepted_trade_count for s in result.summaries)
    total_pnl = sum(s.simulated_pnl for s in result.summaries)
    total_trade_cost = sum(s.total_trade_cost for s in result.summaries)
    total_return_pct = total_pnl / max(total_trade_cost, 1e-9)
    total_gross = sum(s.gross_violation_count for s in result.summaries)
    total_net = sum(s.net_violation_count for s in result.summaries)
    distinct_rels = sum(s.distinct_relationships_traded for s in result.summaries)

    lines = [
        f"# Space sweep report — {_now_iso()}",
        "",
        f"> {_LABEL}",
        f"> run_id: `{result.run_id}`",
        f"> report_integrity: **{result.report_integrity}**",
        f"> credibility: **{result.credibility}**",
        "",
        "## Headline figures",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Spaces analysed | {len(result.summaries)} |",
        f"| Accepted simulated trades | {total_trades} |",
        f"| Gross violations | {total_gross} |",
        f"| Net violations | {total_net} |",
        f"| Distinct relationships traded | {distinct_rels} |",
        f"| Simulated PnL (sum across spaces) | {total_pnl:.2f} USDC |",
        f"| Total deployed trade cost | {total_trade_cost:.2f} USDC |",
        f"| Total return on trade cost | {total_return_pct:.4%} |",
        f"| Diagnostic-only relationships (excluded from totals) | {sum(s.diagnostic_only_relationship_count for s in result.summaries)} |",
        "",
        "## Grade distribution",
        "",
        "| Grade | Count |",
        "| --- | --- |",
    ]
    for grade in (
        "A_PROFITABLE_ROBUST_CANDIDATE",
        "B_PROMISING_GATE_BLOCKED",
        "C_INFRASTRUCTURE_BLOCKED",
        "D_VALID_BUT_STRATEGICALLY_WEAK",
        "F_OVERFIT_ONE_OFF",
        "G_ECONOMICALLY_TINY_SIGNAL",
        "E_INVALID_OR_AUDIT_RISK",
        "UNGRADED",
    ):
        n = by_grade.get(grade, 0)
        if n > 0:
            lines.append(f"| `{grade}` | {n} |")
    lines.append("")

    # Top spaces by trade count
    top = sorted(
        result.summaries,
        key=lambda s: (-s.accepted_trade_count, -s.simulated_pnl, s.space_id),
    )[:15]
    if top:
        lines += [
            "## Top spaces by accepted trade count",
            "",
            "| space_id | grade | trades | distinct_rels | pnl | median_trade_return_pct | primary_blocker |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for s in top:
            lines.append(
                f"| `{s.space_id}` "
                f"| `{s.space_grade}` "
                f"| {s.accepted_trade_count} "
                f"| {s.distinct_relationships_traded} "
                f"| {s.simulated_pnl:.2f} "
                f"| {s.median_trade_return_pct:.4%} "
                f"| {s.primary_blocker or '-'} |"
            )
        lines.append("")

    # Spaces by grade summaries (link to the dedicated docs)
    lines += [
        "## Files in this report",
        "",
        "| File | Contents |",
        "| --- | --- |",
        "| `space_leaderboard.csv` | Per-space summary, sortable |",
        "| `space_blockers.csv` | Blocker categories per space |",
        "| `space_strategy_summary.csv` | (space, strategy_family) rollup |",
        "| `accepted_trades_by_space.csv` | All accepted trades with full attribution |",
        "| `blocked_opportunities_by_space.csv` | All blocked candidates with blocker reason |",
        "| `bundle_diagnostics_by_space.csv` | Per-bundle scan diagnostics |",
        "| `space_examples.md` | Sample trades per space |",
        "| `space_grades.md` | Spaces grouped by grade |",
        "| `report.md` | This document |",
        "",
    ]

    if result.integrity_notes:
        lines += [
            "## Integrity notes (first 10)",
            "",
        ]
        for n in result.integrity_notes[:10]:
            lines.append(f"- {n}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
