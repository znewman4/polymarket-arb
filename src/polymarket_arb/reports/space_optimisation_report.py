"""Space optimisation report writer.

Outputs (under data/reports/space_optimisation/<run_id>/):
  optimisation_grid_results.csv
  best_params_by_space.csv
  robustness_summary.csv
  sensitivity_tables/<space_id>.csv  (per-space pivot)
  report.md

The optimisation report classifies each space as:
  optimise_now
  promising_but_needs_more_data
  profitable_but_overfit
  parameter_sensitive
  robust_candidate
  not_worth_tuning

RESEARCH-ONLY.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..research.row_contracts import SpaceOptimisationRow
from ..research.space_optimisation import SpaceOptimisationResult

_LABEL = "RESEARCH-ONLY — diagnostic / exploratory.  Not trading advice."

_OPTIMISATION_CLASSES = [
    "robust_candidate",
    "optimise_now",
    "promising_but_needs_more_data",
    "parameter_sensitive",
    "profitable_but_overfit",
    "not_worth_tuning",
]


def _classify_space(rows: list[SpaceOptimisationRow], scores: dict[str, float]) -> str:
    """Classify a space's optimisation profile."""
    if not rows:
        return "not_worth_tuning"
    pos_share = scores.get("positive_share", 0.0)
    median_pnl = scores.get("median_pnl", 0.0)
    distinct_share = scores.get("distinct_rels_share", 0.0)
    dominant = scores.get("dominant_share", 0.0)
    slip_sens = scores.get("slip_sensitivity_penalty", 0.0)
    robustness = scores.get("robustness_score", 0.0)

    if robustness >= 0.7 and pos_share >= 0.6 and distinct_share >= 0.4 and dominant < 0.6:
        return "robust_candidate"
    if median_pnl > 0 and pos_share >= 0.5 and slip_sens < 0.5:
        return "optimise_now"
    if pos_share >= 0.3 and distinct_share < 0.3:
        return "promising_but_needs_more_data"
    if slip_sens >= 0.6:
        return "parameter_sensitive"
    if pos_share >= 0.5 and dominant >= 0.8:
        return "profitable_but_overfit"
    return "not_worth_tuning"


def generate_space_optimisation_report(
    result: SpaceOptimisationResult,
) -> Path:
    out = result.output_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "sensitivity_tables").mkdir(parents=True, exist_ok=True)

    # 1. optimisation_grid_results.csv
    grid_rows = [r.model_dump() for r in result.rows]
    _write_csv(out / "optimisation_grid_results.csv", grid_rows)

    # 2. best_params_by_space.csv
    best_rows = []
    for space_id, row in result.best_by_space.items():
        d = row.model_dump()
        d["space_id"] = space_id
        scores = result.robustness_by_space.get(space_id, {})
        d["robustness_score"] = scores.get("robustness_score", 0.0)
        d["positive_share"] = scores.get("positive_share", 0.0)
        best_rows.append(d)
    best_rows.sort(key=lambda d: (-(d.get("robustness_score") or 0), -(d.get("simulated_pnl") or 0)))
    _write_csv(out / "best_params_by_space.csv", best_rows)

    # 3. robustness_summary.csv
    by_space_rows = defaultdict(list)
    for r in result.rows:
        by_space_rows[r.space_id].append(r)
    classifications: dict[str, str] = {}
    rob_rows = []
    for space_id, rows in by_space_rows.items():
        scores = result.robustness_by_space.get(space_id, {})
        klass = _classify_space(rows, scores)
        classifications[space_id] = klass
        rob_rows.append({
            "space_id": space_id,
            "classification": klass,
            "robustness_score": scores.get("robustness_score", 0.0),
            "positive_share": scores.get("positive_share", 0.0),
            "median_pnl": scores.get("median_pnl", 0.0),
            "max_pnl": scores.get("max_pnl", 0.0),
            "max_drawdown": scores.get("max_drawdown", 0.0),
            "dominant_share": scores.get("dominant_share", 0.0),
            "slip_sensitivity_penalty": scores.get("slip_sensitivity_penalty", 0.0),
            "cells_evaluated": len(rows),
        })
    rob_rows.sort(key=lambda d: -(d.get("robustness_score") or 0))
    _write_csv(out / "robustness_summary.csv", rob_rows)

    # 4. sensitivity_tables/<space_id>.csv
    for space_id, rows in by_space_rows.items():
        path = out / "sensitivity_tables" / f"{_safe_filename(space_id)}.csv"
        cell_rows = [r.model_dump() for r in rows]
        cell_rows.sort(key=lambda d: -(d.get("simulated_pnl") or 0))
        _write_csv(path, cell_rows)

    # 5. report.md
    _write_main_report_md(out / "report.md", result, classifications)

    return out


# ─── markdown writer ──────────────────────────────────────────────────────────


def _write_main_report_md(
    path: Path,
    result: SpaceOptimisationResult,
    classifications: dict[str, str],
) -> None:
    n_spaces = len(result.best_by_space)
    n_cells = len(result.rows)
    n_pos = sum(1 for r in result.rows if r.positive_after_costs)

    lines = [
        f"# Space optimisation report — {_now_iso()}",
        "",
        f"> {_LABEL}",
        f"> run_id: `{result.run_id}`",
        "",
        "## Headline",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Spaces optimised | {n_spaces} |",
        f"| Total parameter cells evaluated | {n_cells} |",
        f"| Cells with positive simulated PnL after costs | {n_pos} |",
        f"| Spaces skipped | {len(result.skipped_spaces)} |",
        "",
    ]

    if result.skipped_spaces:
        lines += [
            "## Skipped spaces",
            "",
            "| space_id | reason |",
            "| --- | --- |",
        ]
        for sid, reason in result.skipped_spaces[:20]:
            lines.append(f"| `{sid}` | {reason} |")
        lines.append("")

    # Classification distribution
    from collections import Counter
    klass_counts = Counter(classifications.values())
    lines += [
        "## Classification distribution",
        "",
        "| Class | Count |",
        "| --- | --- |",
    ]
    for klass in _OPTIMISATION_CLASSES:
        n = klass_counts.get(klass, 0)
        if n > 0:
            lines.append(f"| `{klass}` | {n} |")
    lines.append("")

    # Top robust spaces
    rob_rows = sorted(
        result.robustness_by_space.items(),
        key=lambda kv: -kv[1].get("robustness_score", 0.0),
    )[:10]
    if rob_rows:
        lines += [
            "## Top spaces by robustness score",
            "",
            "| space_id | classification | robustness | positive_share | median_pnl | dominant_share |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for space_id, scores in rob_rows:
            klass = classifications.get(space_id, "")
            lines.append(
                f"| `{space_id}` | `{klass}` "
                f"| {scores.get('robustness_score', 0):.3f} "
                f"| {scores.get('positive_share', 0):.2f} "
                f"| {scores.get('median_pnl', 0):.2f} "
                f"| {scores.get('dominant_share', 0):.2f} |"
            )
        lines.append("")

    # Best params per space (top 10)
    best_items = sorted(
        result.best_by_space.items(),
        key=lambda kv: (
            -(result.robustness_by_space.get(kv[0], {}).get("robustness_score") or 0),
            -kv[1].simulated_pnl,
        ),
    )[:10]
    if best_items:
        lines += [
            "## Best parameter sets per top space",
            "",
            "| space_id | preset_id | slippage_bps | min_edge | min_conf | reentry | stake | pnl | win_rate |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for space_id, r in best_items:
            lines.append(
                f"| `{space_id}` "
                f"| `{r.parameter_set_id}` "
                f"| {r.slippage_bps} "
                f"| {r.min_edge} "
                f"| {r.min_confidence} "
                f"| {r.reentry_policy} "
                f"| {r.max_stake_per_trade} "
                f"| {r.simulated_pnl:.2f} "
                f"| {r.win_rate:.2f} |"
            )
        lines.append("")

    lines += [
        "## Output files",
        "",
        "| File | Contents |",
        "| --- | --- |",
        "| `optimisation_grid_results.csv` | Every (space, parameter cell) result |",
        "| `best_params_by_space.csv` | Best non-overfit parameter set per space |",
        "| `robustness_summary.csv` | Per-space classification + robustness score |",
        "| `sensitivity_tables/<space>.csv` | Full parameter sweep per space |",
        "| `report.md` | This document |",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")


# ─── helpers ──────────────────────────────────────────────────────────────────


def _safe_filename(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in (s or "unknown"))[:120]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({k for row in rows for k in row})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
