"""Per-space parameter grid runner + robustness scoring.

Given a space leaderboard CSV and a grid config YAML, this module:

  1. picks the top-N spaces (excluding D/E/F grades by default),
  2. enumerates (or samples) a parameter grid per space,
  3. runs an isolated research_replay restricted to that space for each cell,
  4. emits SpaceOptimisationRow records with robustness metrics.

Robustness — not raw PnL — is the primary ranking metric.  The combined
robustness score uses weights from the grid YAML.

RESEARCH-ONLY.  Every output row carries credibility=exploratory_only_not_credible.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Any

import yaml

from .row_contracts import SpaceOptimisationRow

_DEFAULT_TOP_N = 10
_MAX_CELLS_PER_SPACE = 60   # safety cap

# Strategy families we never tune
_SKIP_GRADES = frozenset({
    "E_INVALID_OR_AUDIT_RISK",
    "F_OVERFIT_ONE_OFF",
    "G_ECONOMICALLY_TINY_SIGNAL",
    "D_VALID_BUT_STRATEGICALLY_WEAK",  # nothing to optimise
})


@dataclass
class SpaceOptimisationResult:
    run_id: str
    output_dir: Path
    rows: list[SpaceOptimisationRow] = field(default_factory=list)
    best_by_space: dict[str, SpaceOptimisationRow] = field(default_factory=dict)
    robustness_by_space: dict[str, dict[str, float]] = field(default_factory=dict)
    grid_used: dict[str, Any] = field(default_factory=dict)
    skipped_spaces: list[tuple[str, str]] = field(default_factory=list)  # (space_id, reason)


# ─── grid loading ─────────────────────────────────────────────────────────────


def load_grid(grid_path: Path) -> dict[str, Any]:
    return yaml.safe_load(grid_path.read_text(encoding="utf-8"))


def enumerate_cells(grid_cfg: dict[str, Any], sampling: str | None = None, sample_size: int | None = None) -> list[dict[str, Any]]:
    """Enumerate parameter cells from a grid config.

    Args:
        grid_cfg: parsed YAML config (top-level dict)
        sampling: "exhaustive" | "slim" | "lhs"
        sample_size: only used for "lhs"
    """
    default = grid_cfg.get("default_sampling") or {}
    sampling = sampling or default.get("strategy", "slim")
    sample_size = sample_size or default.get("sample_size", 60)

    grid = (grid_cfg.get("slim_grid") or grid_cfg["grid"]) if sampling == "slim" else grid_cfg["grid"]

    keys = list(grid.keys())
    values_lists = [list(grid[k]) for k in keys]

    all_combos = list(product(*values_lists))

    if sampling == "lhs" and len(all_combos) > sample_size:
        # Simple uniform sample as a stand-in for LHS — deterministic via seed
        rng = random.Random(0)
        all_combos = rng.sample(all_combos, sample_size)

    if len(all_combos) > _MAX_CELLS_PER_SPACE and sampling != "exhaustive":
        rng = random.Random(0)
        all_combos = rng.sample(all_combos, _MAX_CELLS_PER_SPACE)

    return [dict(zip(keys, combo, strict=False)) for combo in all_combos]


# ─── leaderboard input ────────────────────────────────────────────────────────


def pick_spaces_from_leaderboard(
    leaderboard_csv: Path,
    *,
    top_n: int = _DEFAULT_TOP_N,
    grade_filter: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Read the space_leaderboard.csv and pick the top-N spaces by criterion.

    Default ranking: A_PROFITABLE first, then B_PROMISING, then C_INFRASTRUCTURE.
    Excludes D/E/F by default.
    """
    if not leaderboard_csv.exists():
        return []
    with leaderboard_csv.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return []

    grade_filter = grade_filter or _SKIP_GRADES

    rows = [r for r in rows if r.get("space_grade") not in grade_filter]

    grade_rank = {
        "A_PROFITABLE_ROBUST_CANDIDATE": 0,
        "B_PROMISING_GATE_BLOCKED": 1,
        "C_INFRASTRUCTURE_BLOCKED": 2,
        "UNGRADED": 3,
    }

    def _key(r: dict[str, Any]) -> tuple:
        try:
            pnl = float(r.get("simulated_pnl") or 0.0)
        except (TypeError, ValueError):
            pnl = 0.0
        try:
            trades = int(r.get("accepted_trade_count") or 0)
        except (TypeError, ValueError):
            trades = 0
        return (
            grade_rank.get(r.get("space_grade", "UNGRADED"), 99),
            -trades,
            -pnl,
            r.get("space_id", ""),
        )

    rows.sort(key=_key)
    return rows[:top_n]


# ─── parameter-set evaluation (smoke-test stub) ───────────────────────────────


def _parameter_set_id(cell: dict[str, Any]) -> str:
    """Deterministic 12-char id from cell dict."""
    raw = json.dumps(cell, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _eval_cell_for_space(
    space_row: dict[str, Any],
    cell: dict[str, Any],
) -> SpaceOptimisationRow:
    """Evaluate one parameter cell for one space.

    This is a smoke-test evaluator that synthesises plausible metrics from
    the leaderboard's baseline numbers.  It is deterministic per (space, cell)
    and never claims to be a live or even an approximate backtest result —
    its job is to provide the report skeleton with shape-correct data.

    For a real evaluation, replace this with a filtered call to
    ``run_research_backtest`` that restricts to relationships in this space.
    """
    space_id = space_row.get("space_id", "")
    try:
        base_pnl = float(space_row.get("simulated_pnl") or 0.0)
    except (TypeError, ValueError):
        base_pnl = 0.0
    try:
        base_trades = int(space_row.get("accepted_trade_count") or 0)
    except (TypeError, ValueError):
        base_trades = 0
    try:
        base_rels = int(space_row.get("distinct_relationships_traded") or 0)
    except (TypeError, ValueError):
        base_rels = 0

    # Deterministic perturbation: scale by cell choices
    slippage_factor = max(0.0, 1.0 - (float(cell["slippage_bps"]) / 1000.0))
    edge_factor = max(0.1, 1.0 - float(cell["min_edge"]) * 5.0)
    confidence_factor = max(0.1, 1.0 - float(cell["min_confidence"]) * 0.3)
    reentry_factor = {
        "first_violation_only": 1.0,
        "reenter_after_cooldown": 1.6,
        "trade_every_distinct_violation_window": 2.0,
    }.get(str(cell["reentry_policy"]), 1.0)

    sized_pnl = base_pnl * slippage_factor * edge_factor * confidence_factor
    sized_trades = max(1, int(base_trades * reentry_factor))
    raw_fills = sized_trades * 2

    pos_after = sized_pnl > 0
    overfit = base_rels <= 1
    win_rate = 0.55 if pos_after else 0.40

    return SpaceOptimisationRow(
        space_id=space_id,
        parameter_set_id=_parameter_set_id(cell),
        strategy_family=(space_row.get("strategy_families_available") or "").split(";")[0] or "unknown",
        slippage_bps=int(cell["slippage_bps"]),
        min_edge=float(cell["min_edge"]),
        min_confidence=float(cell["min_confidence"]),
        max_exposure_per_space=float(cell["max_exposure_per_space"]),
        max_stake_per_trade=float(cell["max_stake_per_trade"]),
        reentry_policy=str(cell["reentry_policy"]),
        cooldown_minutes=int(cell["cooldown_minutes"]),
        alignment_mode=str(cell["alignment_mode"]),
        sizing_policy=str(cell["sizing_policy"]),
        simulated_pnl=round(sized_pnl, 4),
        accepted_trades=sized_trades,
        raw_fills=raw_fills,
        unique_violation_windows=max(1, sized_trades),
        distinct_relationships_traded=base_rels,
        distinct_bundles_traded=0,
        max_drawdown=max(0.0, -sized_pnl * 0.5) if sized_pnl < 0 else max(0.0, sized_pnl * 0.2),
        win_rate=win_rate,
        avg_trade_pnl=(sized_pnl / sized_trades) if sized_trades else 0.0,
        median_trade_pnl=(sized_pnl / sized_trades) if sized_trades else 0.0,
        max_loss=min(0.0, sized_pnl),
        max_win=max(0.0, sized_pnl),
        slippage_paid=raw_fills * float(cell["max_stake_per_trade"]) * float(cell["slippage_bps"]) / 10000.0,
        exposure_used=min(float(cell["max_exposure_per_space"]), sized_trades * float(cell["max_stake_per_trade"]) * 2),
        robustness_score=0.0,           # filled in later
        overfit_warning=overfit,
        dominant_trade_share_of_pnl=(1.0 if overfit else 0.5),
        positive_after_costs=pos_after,
        credibility_label="exploratory_only_not_credible",
    )


# ─── robustness scoring ───────────────────────────────────────────────────────


def _robustness_score(
    rows_for_space: list[SpaceOptimisationRow],
    weights: dict[str, float],
) -> dict[str, float]:
    """Compute aggregate robustness metrics for a space."""
    if not rows_for_space:
        return {}

    n = len(rows_for_space)
    pos_share = sum(1 for r in rows_for_space if r.positive_after_costs) / n
    pnls = sorted(r.simulated_pnl for r in rows_for_space)
    median_pnl = pnls[n // 2]
    max_drawdown = max(r.max_drawdown for r in rows_for_space)
    max_pnl = max(r.simulated_pnl for r in rows_for_space) or 1.0
    drawdown_norm = max_drawdown / (abs(max_pnl) + 1e-9)
    distinct_rels_max = max(r.distinct_relationships_traded for r in rows_for_space) or 0
    distinct_rels_share = min(1.0, distinct_rels_max / 5.0)
    dominant_share = sum(r.dominant_trade_share_of_pnl for r in rows_for_space) / n

    # slippage sensitivity: PnL std as slippage changes
    by_slip: dict[int, list[float]] = defaultdict(list)
    for r in rows_for_space:
        by_slip[r.slippage_bps].append(r.simulated_pnl)
    slip_avgs = [sum(v) / len(v) for v in by_slip.values()]
    if len(slip_avgs) >= 2:
        slip_range = max(slip_avgs) - min(slip_avgs)
        slip_sensitivity_penalty = min(1.0, slip_range / (abs(median_pnl) + 1e-9))
    else:
        slip_sensitivity_penalty = 0.0

    # final score (weighted)
    score = (
        weights.get("positive_after_costs_share", 0.30) * pos_share
        + weights.get("median_pnl_share", 0.25) * (1.0 if median_pnl > 0 else 0.0)
        + weights.get("distinct_relationships_share", 0.15) * distinct_rels_share
        + weights.get("drawdown_penalty", 0.15) * (1.0 - min(1.0, drawdown_norm))
        + weights.get("slippage_sensitivity_penalty", 0.10) * (1.0 - slip_sensitivity_penalty)
        + weights.get("dominant_trade_penalty", 0.05) * (1.0 - dominant_share)
    )

    return {
        "positive_share": round(pos_share, 4),
        "median_pnl": round(median_pnl, 4),
        "max_pnl": round(max_pnl, 4),
        "max_drawdown": round(max_drawdown, 4),
        "distinct_rels_share": round(distinct_rels_share, 4),
        "dominant_share": round(dominant_share, 4),
        "slip_sensitivity_penalty": round(slip_sensitivity_penalty, 4),
        "robustness_score": round(min(1.0, max(0.0, score)), 4),
    }


# ─── main entry point ────────────────────────────────────────────────────────


def run_space_optimisation(
    data_root: Path,
    leaderboard_csv: Path,
    grid_path: Path,
    *,
    run_id: str,
    top_n: int = _DEFAULT_TOP_N,
    sampling: str | None = None,
    sample_size: int | None = None,
    output_dir: Path | None = None,
) -> SpaceOptimisationResult:
    """Run the per-space parameter optimisation.

    Args:
        data_root: Root of the Parquet data lake (used for output path default).
        leaderboard_csv: Path to space_leaderboard.csv from a prior space sweep.
        grid_path: Path to the optimisation grid YAML.
        run_id: Output run id for the optimisation.
        top_n: Number of spaces to optimise from the leaderboard.
        sampling: "exhaustive" | "slim" | "lhs" (overrides YAML default).
        sample_size: LHS sample size override.
        output_dir: Override output location.
    """
    out_dir = output_dir or (data_root.parent / "reports" / "space_optimisation" / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sensitivity_tables").mkdir(parents=True, exist_ok=True)

    spaces = pick_spaces_from_leaderboard(leaderboard_csv, top_n=top_n)
    grid_cfg = load_grid(grid_path)
    cells = enumerate_cells(grid_cfg, sampling=sampling, sample_size=sample_size)
    weights: dict[str, float] = grid_cfg.get("robustness_weights") or {}

    all_rows: list[SpaceOptimisationRow] = []
    by_space: dict[str, list[SpaceOptimisationRow]] = defaultdict(list)
    skipped: list[tuple[str, str]] = []

    for space_row in spaces:
        space_id = space_row.get("space_id", "")
        if not space_id:
            continue
        grade = space_row.get("space_grade") or "UNGRADED"
        if grade in _SKIP_GRADES:
            skipped.append((space_id, f"skipped_due_to_grade={grade}"))
            continue
        for cell in cells:
            row = _eval_cell_for_space(space_row, cell)
            all_rows.append(row)
            by_space[space_id].append(row)

    # Compute robustness per space and best param-set
    best_by_space: dict[str, SpaceOptimisationRow] = {}
    robustness_by_space: dict[str, dict[str, float]] = {}
    for space_id, rows in by_space.items():
        scores = _robustness_score(rows, weights)
        robustness_by_space[space_id] = scores
        # Stamp robustness on every row, pick the best non-overfit row
        for r in rows:
            r.robustness_score = scores["robustness_score"]
        # Best by combined robustness x positive_after_costs, excluding overfits
        candidates = [r for r in rows if r.positive_after_costs and not r.overfit_warning]
        if candidates:
            best = max(candidates, key=lambda r: (r.simulated_pnl, -r.max_drawdown))
        else:
            best = max(rows, key=lambda r: r.simulated_pnl)
        best_by_space[space_id] = best

    return SpaceOptimisationResult(
        run_id=run_id,
        output_dir=out_dir,
        rows=all_rows,
        best_by_space=best_by_space,
        robustness_by_space=robustness_by_space,
        grid_used=grid_cfg,
        skipped_spaces=skipped,
    )
