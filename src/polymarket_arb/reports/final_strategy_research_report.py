"""Final strategy-research narrative report.

Combines space-sweep + space-optimisation outputs into one human-readable
final_report.md, with 20 research-only sections:

  1. Research-only disclaimer
  2. What was implemented
  3. What changed from previous Phase H
  4. Report integrity status
  5. Overall backtest summary
  6. Best spaces by simulated PnL
  7. Best spaces by robust parameter performance
  8. Best spaces by trade count
  9. Spaces blocked by infrastructure
  10. Spaces needing looser economic/replay settings
  11. Spaces that appear strategically dead
  12. Spaces rejected as invalid/audit-risk
  13. Per-space optimisation findings
  14. Parameter sensitivity findings
  15. Main sources of simulated PnL
  16. Main blockers
  17. Overfit warnings
  18. Credibility status
  19. Recommended next work
  20. Structurally robust but economically tiny spaces
  21. Expanded-universe comparison snapshot

Output: data/reports/final_strategy_research/<run_id>/final_report.md

RESEARCH-ONLY.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DISCLAIMER = (
    "**RESEARCH-ONLY simulation platform.**  No live trading, no wallets, "
    "no order placement, no on-chain transactions.  All numbers below are "
    "simulated/backtested.  Credibility ranges over `data_insufficient` / "
    "`exploratory_only_not_credible` / `report_invalid_for_strategy_conclusions`.  "
    "Never use as trading advice."
)


def generate_final_report(
    data_root: Path,
    sweep_run_id: str,
    optimisation_run_id: str | None = None,
    output_dir: Path | None = None,
) -> Path:
    """Generate the final narrative report.

    Args:
        data_root: Root of the Parquet data lake.
        sweep_run_id: The space_sweep run_id (must exist under data/reports/space_sweep/).
        optimisation_run_id: Optional space_optimisation run_id (must exist if provided).
        output_dir: Override output path.
    """
    sweep_dir = data_root.parent / "reports" / "space_sweep" / sweep_run_id
    opt_dir = (
        data_root.parent / "reports" / "space_optimisation" / optimisation_run_id
        if optimisation_run_id
        else None
    )

    out_dir = output_dir or (
        data_root.parent / "reports" / "final_strategy_research" / sweep_run_id
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    leaderboard = _read_csv(sweep_dir / "space_leaderboard.csv")
    sweep_report_md = _safe_read(sweep_dir / "report.md")
    blockers = _read_csv(sweep_dir / "space_blockers.csv")
    accepted_trades = _read_csv(sweep_dir / "accepted_trades_by_space.csv")

    optimisation_classifications: dict[str, str] = {}
    best_params = []
    robust_summary = []
    if opt_dir and opt_dir.exists():
        robust_summary = _read_csv(opt_dir / "robustness_summary.csv")
        for row in robust_summary:
            optimisation_classifications[row.get("space_id", "")] = row.get("classification", "")
        best_params = _read_csv(opt_dir / "best_params_by_space.csv")

    md = _build_markdown(
        sweep_run_id=sweep_run_id,
        optimisation_run_id=optimisation_run_id,
        leaderboard=leaderboard,
        blockers=blockers,
        accepted_trades=accepted_trades,
        best_params=best_params,
        robust_summary=robust_summary,
        optimisation_classes=optimisation_classifications,
        sweep_report_md=sweep_report_md,
        comparison=_comparison_snapshot(data_root, sweep_run_id, leaderboard),
    )

    path = out_dir / "final_report.md"
    path.write_text(md, encoding="utf-8")
    return path


# ─── markdown builder ─────────────────────────────────────────────────────────


def _build_markdown(
    *,
    sweep_run_id: str,
    optimisation_run_id: str | None,
    leaderboard: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    accepted_trades: list[dict[str, Any]],
    best_params: list[dict[str, Any]],
    robust_summary: list[dict[str, Any]],
    optimisation_classes: dict[str, str],
    sweep_report_md: str,
    comparison: dict[str, Any],
) -> str:
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    by_grade: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in leaderboard:
        by_grade[row.get("space_grade", "UNGRADED")].append(row)

    total_trades = sum(_int(r.get("accepted_trade_count")) for r in leaderboard)
    total_pnl = sum(_float(r.get("simulated_pnl")) for r in leaderboard)
    total_trade_cost = sum(_float(r.get("total_trade_cost")) for r in leaderboard)
    total_return_pct = total_pnl / max(total_trade_cost, 1e-9)
    median_space_trade_return = _median([
        _float(r.get("median_trade_return_pct"))
        for r in leaderboard
        if r.get("median_trade_return_pct") not in (None, "")
    ])
    distinct_rels_total = sum(_int(r.get("distinct_relationships_traded")) for r in leaderboard)

    sections: list[str] = []

    # 1. Disclaimer
    sections.append(
        f"# Final strategy research report — {ts}\n\n"
        f"> sweep_run_id: `{sweep_run_id}`\n"
        f"> optimisation_run_id: `{optimisation_run_id or 'none'}`\n"
        f"\n## 1. Research-only disclaimer\n\n{_DISCLAIMER}\n"
    )

    # 2. What was implemented
    sections.append(
        "## 2. What was implemented\n\n"
        "This run produced two new typed reporting pipelines:\n\n"
        "1. **Space sweep** — aggregates an existing backtest run by outcome/context/bundle\n"
        "   space, enforces typed row contracts (no diagnostic-only subtypes in trade totals),\n"
        "   and grades every space into A/B/C/D/E/F/G, where Grade G means\n"
        "   structurally clean but economically tiny on per-trade returns.\n"
        "2. **Per-space optimisation** — runs a parameter grid per top space and ranks\n"
        "   by a robustness score that combines positive-share, median PnL, drawdown,\n"
        "   slippage sensitivity, and dominant-trade share.\n\n"
        "All emissions are research-only and never claim live viability.\n"
    )

    # 3. What changed from previous Phase H
    sections.append(
        "## 3. What changed from the previous Phase H report\n\n"
        "* The new pipeline uses **typed Pydantic row contracts** that reject\n"
        "  diagnostic-only subtypes (e.g. `same_topic_no_trade`) from ever entering\n"
        "  trade or PnL totals.\n"
        "* Reports are keyed by **space_id** with the precedence "
        "`outcome_space > context_space > bundle_space > synthetic`.\n"
        "* Every accepted trade carries explicit `relationship_id`, `space_id`, and\n"
        "  `strategy_family`.  Trades without attribution are dropped at the contract\n"
        "  boundary and recorded in `integrity_notes`.\n"
        "* Bundle diagnostics are now a separate typed stream (`BundleDiagnosticRow`)\n"
        "  loaded into the space report.\n"
    )

    # 4. Report integrity
    integrity_line = _extract_field(sweep_report_md, "report_integrity") or "unknown"
    credibility_line = _extract_field(sweep_report_md, "credibility") or "unknown"
    sections.append(
        "## 4. Report integrity status\n\n"
        f"* report_integrity: **{integrity_line}**\n"
        f"* credibility: **{credibility_line}**\n"
    )

    # 5. Overall summary
    sections.append(
        "## 5. Overall backtest summary\n\n"
        f"| Metric | Value |\n"
        f"| --- | --- |\n"
        f"| Spaces analysed | {len(leaderboard)} |\n"
        f"| Accepted simulated trades | {total_trades} |\n"
        f"| Distinct relationships traded | {distinct_rels_total} |\n"
        f"| Simulated PnL (sum) | {total_pnl:.2f} USDC |\n"
        f"| Total deployed trade cost | {total_trade_cost:.2f} USDC |\n"
        f"| Total return on trade cost | {total_return_pct:.4%} |\n"
        f"| Median per-space trade return | {median_space_trade_return:.4%} |\n"
        f"| Grade A (passes per-trade economic gates) | {len(by_grade.get('A_PROFITABLE_ROBUST_CANDIDATE', []))} |\n"
        f"| Grade B (promising blocked) | {len(by_grade.get('B_PROMISING_GATE_BLOCKED', []))} |\n"
        f"| Grade C (infrastructure blocked) | {len(by_grade.get('C_INFRASTRUCTURE_BLOCKED', []))} |\n"
        f"| Grade D (strategically weak) | {len(by_grade.get('D_VALID_BUT_STRATEGICALLY_WEAK', []))} |\n"
        f"| Grade E (invalid / audit risk) | {len(by_grade.get('E_INVALID_OR_AUDIT_RISK', []))} |\n"
        f"| Grade F (overfit / one-off) | {len(by_grade.get('F_OVERFIT_ONE_OFF', []))} |\n"
        f"| Grade G (economically tiny signal) | {len(by_grade.get('G_ECONOMICALLY_TINY_SIGNAL', []))} |\n"
    )

    # 6. Best spaces by PnL
    pnl_sorted = sorted(leaderboard, key=lambda r: -_float(r.get("simulated_pnl")))[:10]
    sections.append(
        "## 6. Best spaces by simulated PnL / median trade return\n\n"
        + _md_table(
            pnl_sorted,
            ("space_id", "space_grade", "accepted_trade_count", "distinct_relationships_traded", "simulated_pnl", "median_trade_return_pct"),
            ("space_id", "grade", "trades", "distinct_rels", "pnl / median_trade_return_pct", "median_trade_return_pct"),
        )
    )

    # 7. Best by robust parameter performance
    rob_sorted = sorted(robust_summary, key=lambda r: -_float(r.get("robustness_score")))[:10]
    if rob_sorted:
        sections.append(
            "## 7. Best spaces by robust parameter performance / median trade return\n\n"
            + _md_table(
                rob_sorted,
                ("space_id", "classification", "robustness_score", "positive_share", "median_pnl", "median_trade_return_pct"),
                ("space_id", "class", "robustness", "positive_share", "median_pnl", "median_trade_return_pct"),
            )
        )
    else:
        sections.append("## 7. Best spaces by robust parameter performance\n\nNo optimisation results available.\n")

    # 8. Best by trade count
    trade_sorted = sorted(leaderboard, key=lambda r: -_int(r.get("accepted_trade_count")))[:10]
    sections.append(
        "## 8. Best spaces by trade count\n\n"
        + _md_table(
            trade_sorted,
            ("space_id", "space_grade", "accepted_trade_count", "distinct_relationships_traded", "simulated_pnl", "median_trade_return_pct"),
            ("space_id", "grade", "trades", "distinct_rels", "pnl", "median_trade_return_pct"),
        )
    )

    # 9. Spaces blocked by infrastructure
    grade_c = by_grade.get("C_INFRASTRUCTURE_BLOCKED", [])
    sections.append(
        "## 9. Spaces blocked by infrastructure\n\n"
        + (_md_table(
            grade_c[:15],
            ("space_id", "primary_blocker", "secondary_blocker", "recommended_next_action"),
            ("space_id", "blocker", "secondary", "action"),
        ) if grade_c else "None.\n")
    )

    # 10. Spaces needing looser economic/replay settings
    grade_b = by_grade.get("B_PROMISING_GATE_BLOCKED", [])
    sections.append(
        "## 10. Spaces needing looser economic/replay settings\n\n"
        + (_md_table(
            grade_b[:15],
            ("space_id", "gross_violation_count", "primary_blocker", "recommended_next_action"),
            ("space_id", "gross_violations", "blocker", "action"),
        ) if grade_b else "None.\n")
    )

    # 11. Spaces strategically dead
    grade_d = by_grade.get("D_VALID_BUT_STRATEGICALLY_WEAK", [])
    sections.append(
        "## 11. Spaces that appear strategically dead\n\n"
        + (_md_table(
            grade_d[:15],
            ("space_id", "relationship_count", "gross_violation_count"),
            ("space_id", "rels", "gross_violations"),
        ) if grade_d else "None.\n")
    )

    # 12. Invalid / audit-risk
    grade_e = by_grade.get("E_INVALID_OR_AUDIT_RISK", [])
    sections.append(
        "## 12. Spaces rejected as invalid / audit-risk\n\n"
        + (_md_table(
            grade_e[:15],
            ("space_id", "diagnostic_only_relationship_count", "suspicious_flag_count", "recommended_next_action"),
            ("space_id", "diagnostic_only", "suspicious", "action"),
        ) if grade_e else "None.\n")
    )

    # 13. Per-space optimisation findings
    if best_params:
        sections.append(
            "## 13. Per-space optimisation findings\n\n"
            + _md_table(
                best_params[:10],
                ("space_id", "robustness_score", "simulated_pnl", "slippage_bps", "min_edge", "reentry_policy"),
                ("space_id", "robustness", "pnl", "slippage_bps", "min_edge", "reentry"),
            )
        )
    else:
        sections.append("## 13. Per-space optimisation findings\n\nNo optimisation run linked.\n")

    # 14. Parameter sensitivity findings
    sensitive = [r for r in robust_summary if _float(r.get("slip_sensitivity_penalty")) > 0.5]
    sections.append(
        "## 14. Parameter sensitivity findings\n\n"
        + (_md_table(
            sensitive[:10],
            ("space_id", "slip_sensitivity_penalty", "classification"),
            ("space_id", "slippage_sensitivity", "class"),
        ) if sensitive else "No spaces showed strong parameter sensitivity.\n")
    )

    # 15. Main sources of simulated PnL
    pnl_by_strategy: dict[str, float] = defaultdict(float)
    for t in accepted_trades:
        family = t.get("strategy_family", "") or "unknown"
        pnl_by_strategy[family] += _float(t.get("net_edge_after_cost")) * _float(t.get("notional_usdc"))
    if pnl_by_strategy:
        sections.append(
            "## 15. Main sources of simulated PnL (by strategy family)\n\n"
            "| Strategy family | PnL |\n"
            "| --- | --- |\n"
            + "\n".join(
                f"| `{k}` | {v:.2f} |"
                for k, v in sorted(pnl_by_strategy.items(), key=lambda kv: -kv[1])
            )
            + "\n"
        )
    else:
        sections.append("## 15. Main sources of simulated PnL\n\nNo accepted trades observed.\n")

    # 16. Main blockers
    blocker_totals: Counter = Counter()
    for row in blockers:
        blocker_totals[row.get("blocker_category", "other")] += _int(row.get("count", 1))
    if blocker_totals:
        sections.append(
            "## 16. Main blockers\n\n"
            "| Blocker category | Count |\n"
            "| --- | --- |\n"
            + "\n".join(
                f"| `{k}` | {v} |" for k, v in blocker_totals.most_common(10)
            )
            + "\n"
        )
    else:
        sections.append("## 16. Main blockers\n\nNo blockers recorded.\n")

    # 17. Overfit warnings
    overfit_spaces = by_grade.get("F_OVERFIT_ONE_OFF", [])
    sections.append(
        "## 17. Overfit warnings\n\n"
        + (_md_table(
            overfit_spaces[:15],
            ("space_id", "accepted_trade_count", "dominant_relationship_share_of_pnl", "simulated_pnl"),
            ("space_id", "trades", "dominant_rel_share", "pnl"),
        ) if overfit_spaces else "No spaces flagged as overfit.\n")
    )

    # 18. Credibility
    sections.append(
        "## 18. Credibility status\n\n"
        f"* Sweep credibility: `{credibility_line}`\n"
        "* Optimisation credibility: `exploratory_only_not_credible` (always)\n"
        "* This refactor expands trade-surface analysis; it does not unlock live\n"
        "  trading or upgrade credibility to `credible_positive`.\n"
    )

    # 19. Recommended next work
    sections.append(
        "## 19. Recommended next work\n\n"
        "Priority order (highest first):\n\n"
        "1. **Grade C → fix infrastructure**: backfill missing price history, set\n"
        "   `known_total_candidates`, run alignment for the infrastructure-blocked\n"
        "   spaces.\n"
        "2. **Grade B → tune economic/replay**: rerun those spaces with looser\n"
        "   presets (`exploratory_trade_surface` or `gross_violation_scan`) and\n"
        "   feed the resulting leaderboard back into the optimiser.\n"
        "3. **Grade A → optimise only after economic gates pass**: require\n"
        "   `median_trade_return_pct >= 0.01`, `total_return_pct >= 0.005`, and\n"
        "   at least five independent violation windows before tuning further.\n"
        "4. **Grade F**: leave alone until more independent evidence appears.\n"
        "5. **Grade G**: structurally robust but economically tiny; bundle with\n"
        "   other promising spaces or skip.\n"
        "6. **Grade E**: repair taxonomy/templates; do not optimise.\n"
    )

    # 20. Structurally robust but economically tiny
    grade_g = by_grade.get("G_ECONOMICALLY_TINY_SIGNAL", [])
    sections.append(
        "## 20. Structurally robust but economically tiny — bundle or skip\n\n"
        "These spaces have positive simulated PnL and pass the structural checks, "
        "but fail at least one per-trade economic gate. Treat them as bundle "
        "ingredients at most, not standalone optimisation targets.\n\n"
        + (_md_table(
            grade_g[:20],
            ("space_id", "accepted_trade_count", "distinct_relationships_traded", "independent_violation_windows", "simulated_pnl", "median_trade_return_pct", "total_return_pct"),
            ("space_id", "trades", "distinct_rels", "windows", "pnl", "median_trade_return_pct", "total_return_pct"),
        ) if grade_g else "None.\n")
    )

    sections.append(
        "## 21. Expanded-universe comparison snapshot\n\n"
        "| Metric | Value |\n"
        "| --- | --- |\n"
        f"| Markets discovered | {comparison.get('markets_discovered', 'unknown')} |\n"
        f"| Spaces analysed | {comparison.get('spaces_analysed', len(leaderboard))} |\n"
        f"| Relationships generated | {comparison.get('relationships_generated', 'unknown')} |\n"
        f"| Distinct relationships traded | {comparison.get('distinct_relationships_traded', distinct_rels_total)} |\n"
        f"| Distinct spaces traded | {comparison.get('distinct_spaces_traded', 0)} |\n"
        f"| Total return on trade cost | {comparison.get('total_return_pct', total_return_pct):.4%} |\n"
        f"| Grade distribution | {comparison.get('grade_distribution', {})} |\n"
    )

    return "\n\n".join(sections) + "\n"


# ─── helpers ──────────────────────────────────────────────────────────────────


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


def _safe_read(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _comparison_snapshot(
    data_root: Path,
    sweep_run_id: str,
    leaderboard: list[dict[str, Any]],
) -> dict[str, Any]:
    stats_path = data_root / "raw" / "market_universe" / sweep_run_id / "discovery_stats.json"
    markets_discovered: int | str = "unknown"
    if stats_path.exists():
        try:
            stats = json.loads(stats_path.read_text(encoding="utf-8"))
            markets_discovered = int((stats.get("counts") or {}).get("markets", 0))
        except Exception:
            markets_discovered = "unknown"
    total_pnl = sum(_float(r.get("simulated_pnl")) for r in leaderboard)
    total_cost = sum(_float(r.get("total_trade_cost")) for r in leaderboard)
    grade_counts = Counter(r.get("space_grade", "UNGRADED") for r in leaderboard)
    return {
        "markets_discovered": markets_discovered,
        "spaces_analysed": len(leaderboard),
        "relationships_generated": "unknown",
        "distinct_relationships_traded": sum(_int(r.get("distinct_relationships_traded")) for r in leaderboard),
        "distinct_spaces_traded": sum(1 for r in leaderboard if _int(r.get("accepted_trade_count")) > 0),
        "total_return_pct": total_pnl / max(total_cost, 1e-9),
        "grade_distribution": dict(sorted(grade_counts.items())),
    }


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2


def _md_table(
    rows: list[dict[str, Any]],
    columns: tuple[str, ...],
    headers: tuple[str, ...],
) -> str:
    if not rows:
        return "_(none)_\n"
    header_row = "| " + " | ".join(headers) + " |"
    sep_row = "| " + " | ".join("---" for _ in headers) + " |"
    body_rows = []
    for r in rows:
        vals = []
        for c in columns:
            v = r.get(c, "")
            if isinstance(v, float):
                v = f"{v:.2f}"
            vals.append(str(v))
        body_rows.append("| " + " | ".join(vals) + " |")
    return "\n".join([header_row, sep_row, *body_rows]) + "\n"


def _extract_field(text: str, field_name: str) -> str | None:
    """Find a line in the form 'field_name: <value>' in markdown."""
    import re
    m = re.search(rf"{re.escape(field_name)}:\s*\**\s*([^\n*`]+)", text)
    if not m:
        return None
    return m.group(1).strip().strip("`").strip()
