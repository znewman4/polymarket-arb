"""Tests for the per-space optimisation pipeline."""

from __future__ import annotations

import csv
from pathlib import Path

from polymarket_arb.research.space_optimisation import (
    enumerate_cells,
    load_grid,
    pick_spaces_from_leaderboard,
    run_space_optimisation,
)

_GRID_PATH = Path(__file__).resolve().parents[2] / "configs" / "research_presets" / "space_optimisation_grid_v1.yaml"


def _write_leaderboard(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({k for r in rows for k in r})
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_grid_yaml_loads():
    grid = load_grid(_GRID_PATH)
    assert "grid" in grid
    assert "slim_grid" in grid
    assert "robustness_weights" in grid


def test_enumerate_cells_slim_grid_finite():
    grid = load_grid(_GRID_PATH)
    cells = enumerate_cells(grid, sampling="slim")
    # slim grid has 2*2*2*2*2*2*2*1*1 = 128 — capped to MAX_CELLS_PER_SPACE
    assert 0 < len(cells) <= 60


def test_enumerate_cells_keys_match_grid():
    grid = load_grid(_GRID_PATH)
    cells = enumerate_cells(grid, sampling="slim")
    assert cells
    cell = cells[0]
    for key in (
        "slippage_bps", "min_edge", "min_confidence",
        "max_exposure_per_space", "max_stake_per_trade",
        "reentry_policy", "cooldown_minutes", "alignment_mode", "sizing_policy",
    ):
        assert key in cell


def test_pick_spaces_excludes_grade_e_and_f(tmp_path):
    leaderboard = tmp_path / "lb.csv"
    _write_leaderboard(leaderboard, [
        {"space_id": "good_a", "space_grade": "A_PROFITABLE_ROBUST_CANDIDATE", "accepted_trade_count": "10", "simulated_pnl": "100"},
        {"space_id": "bad_e", "space_grade": "E_INVALID_OR_AUDIT_RISK", "accepted_trade_count": "0", "simulated_pnl": "0"},
        {"space_id": "bad_f", "space_grade": "F_OVERFIT_ONE_OFF", "accepted_trade_count": "1", "simulated_pnl": "50"},
        {"space_id": "weak_d", "space_grade": "D_VALID_BUT_STRATEGICALLY_WEAK", "accepted_trade_count": "0", "simulated_pnl": "0"},
        {"space_id": "blocked_b", "space_grade": "B_PROMISING_GATE_BLOCKED", "accepted_trade_count": "0", "simulated_pnl": "0"},
    ])
    picked = pick_spaces_from_leaderboard(leaderboard, top_n=10)
    ids = {r["space_id"] for r in picked}
    assert ids == {"good_a", "blocked_b"}


def test_pick_spaces_prefers_grade_a(tmp_path):
    leaderboard = tmp_path / "lb.csv"
    _write_leaderboard(leaderboard, [
        {"space_id": "b", "space_grade": "B_PROMISING_GATE_BLOCKED", "accepted_trade_count": "5", "simulated_pnl": "10"},
        {"space_id": "a", "space_grade": "A_PROFITABLE_ROBUST_CANDIDATE", "accepted_trade_count": "1", "simulated_pnl": "1"},
    ])
    picked = pick_spaces_from_leaderboard(leaderboard, top_n=2)
    assert picked[0]["space_id"] == "a"


def test_run_optimisation_smoke(tmp_path, tmp_data_root):
    leaderboard = tmp_data_root / "lb.csv"
    _write_leaderboard(leaderboard, [
        {
            "space_id": "space_x",
            "space_grade": "A_PROFITABLE_ROBUST_CANDIDATE",
            "accepted_trade_count": "5",
            "simulated_pnl": "20",
            "distinct_relationships_traded": "3",
            "strategy_families_available": "nesting",
        },
    ])
    result = run_space_optimisation(
        data_root=tmp_data_root,
        leaderboard_csv=leaderboard,
        grid_path=_GRID_PATH,
        run_id="opt_test_001",
        top_n=1,
        sampling="slim",
    )
    assert len(result.best_by_space) == 1
    assert "space_x" in result.best_by_space
    assert len(result.rows) > 0
    # robustness score is in [0, 1]
    for r in result.rows:
        assert 0.0 <= r.robustness_score <= 1.0
    # All rows credibility-labelled
    for r in result.rows:
        assert r.credibility_label == "exploratory_only_not_credible"
