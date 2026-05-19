"""Tests for the entry/resolution causality gate.

The gate drops legs whose entry timestamp is at or after the inferred
resolution timestamp.  These trades have access to the market outcome at
entry time and would be look-ahead leakage if reported as alpha.
"""

from __future__ import annotations

from dataclasses import dataclass

from polymarket_arb.backtest.standardised.adapters import _first_convergence_ts_ms
from polymarket_arb.backtest.standardised.contract import StandardisedTradeRow
from polymarket_arb.backtest.standardised.orchestrator import _apply_causality_gate


@dataclass
class _Tick:
    ts_ms: int
    price: float


def _row(**overrides) -> StandardisedTradeRow:
    base = {
        "trade_id": "t",
        "trade_group_id": "g",
        "run_id": "r1",
        "subrun_id": "s",
        "source_lane": "rulebook_aggressive_deterministic",
        "source_agent": "deterministic_template_aggressive",
        "reason": "test",
        "entry_ts_ms": 1000,
        "resolution_ts_ms": 2000,
        "stake_usdc": 50.0,
        "notional_usdc": 50.0,
        "entry_cost_usdc": 50.0,
    }
    base.update(overrides)
    return StandardisedTradeRow(**base)


def test_entry_before_resolution_is_kept() -> None:
    kept, summary = _apply_causality_gate([_row(entry_ts_ms=1000, resolution_ts_ms=2000)])
    assert len(kept) == 1
    assert summary["suppressed"] == 0
    assert summary["per_lane_suppressed"] == {}


def test_entry_after_resolution_is_dropped() -> None:
    trade = _row(entry_ts_ms=3000, resolution_ts_ms=2000)
    kept, summary = _apply_causality_gate([trade])
    assert kept == []
    assert summary["suppressed"] == 1
    assert summary["per_lane_suppressed"] == {"rulebook_aggressive_deterministic": 1}


def test_entry_equal_to_resolution_is_dropped() -> None:
    """Equality is a violation: entering at the exact resolution tick still
    means we had the outcome's price at entry time."""
    trade = _row(entry_ts_ms=2000, resolution_ts_ms=2000)
    kept, summary = _apply_causality_gate([trade])
    assert kept == []
    assert summary["suppressed"] == 1


def test_missing_resolution_ts_is_kept() -> None:
    """A trade on an unresolved market has no resolution_ts; we never block these."""
    trade = _row(entry_ts_ms=1000, resolution_ts_ms=None)
    kept, _summary = _apply_causality_gate([trade])
    assert len(kept) == 1
    assert _summary["suppressed"] == 0


def test_missing_entry_ts_is_kept() -> None:
    trade = _row(entry_ts_ms=None, resolution_ts_ms=2000)
    kept, _ = _apply_causality_gate([trade])
    assert len(kept) == 1


def test_zero_resolution_ts_is_kept() -> None:
    """resolution_ts_ms=0 means the inference fell through to the missing/closed-flag
    branch — treat as unknown, not as 'resolved at epoch'."""
    trade = _row(entry_ts_ms=1000, resolution_ts_ms=0)
    kept, _ = _apply_causality_gate([trade])
    assert len(kept) == 1


def test_first_convergence_yes_picks_earliest_converged_tick() -> None:
    """Price rises into the converged zone (>=0.98) at tick #3 and stays there.
    The inferred resolution_ts must be tick #3, not the last tick."""
    rows = [
        _Tick(ts_ms=100, price=0.20),
        _Tick(ts_ms=200, price=0.50),
        _Tick(ts_ms=300, price=0.99),  # first converged
        _Tick(ts_ms=400, price=0.995),
        _Tick(ts_ms=500, price=0.999),
    ]
    ts = _first_convergence_ts_ms(rows, outcome="yes", epsilon=0.02)
    assert ts == 300


def test_first_convergence_no_picks_earliest_converged_tick() -> None:
    rows = [
        _Tick(ts_ms=100, price=0.80),
        _Tick(ts_ms=200, price=0.50),
        _Tick(ts_ms=300, price=0.01),  # first converged below epsilon=0.02
        _Tick(ts_ms=400, price=0.005),
    ]
    ts = _first_convergence_ts_ms(rows, outcome="no", epsilon=0.02)
    assert ts == 300


def test_first_convergence_handles_bounce_after_initial_convergence() -> None:
    """If the price briefly leaves the converged zone before settling, the
    resolution_ts should be the tick AFTER the last non-converged sample."""
    rows = [
        _Tick(ts_ms=100, price=0.99),  # converged early
        _Tick(ts_ms=200, price=0.90),  # bounce out
        _Tick(ts_ms=300, price=0.95),  # still below 0.98
        _Tick(ts_ms=400, price=0.99),  # back in
        _Tick(ts_ms=500, price=0.999),
    ]
    # Walking backward, the last NON-converged tick is at ts=300 (price 0.95).
    # The convergence-from-then-on started at ts=400.
    ts = _first_convergence_ts_ms(rows, outcome="yes", epsilon=0.02)
    assert ts == 400


def test_first_convergence_handles_always_converged() -> None:
    rows = [
        _Tick(ts_ms=100, price=0.99),
        _Tick(ts_ms=200, price=0.995),
    ]
    ts = _first_convergence_ts_ms(rows, outcome="yes", epsilon=0.02)
    assert ts == 100


def test_per_lane_breakdown() -> None:
    # Each trade is in a distinct group so per-leg counts == per-group counts.
    trades = [
        _row(trade_id="a", trade_group_id="ga",
             source_lane="rulebook_aggressive_deterministic",
             entry_ts_ms=3000, resolution_ts_ms=2000),
        _row(trade_id="b", trade_group_id="gb",
             source_lane="rulebook_aggressive_deterministic",
             entry_ts_ms=4000, resolution_ts_ms=2000),
        _row(trade_id="c", trade_group_id="gc",
             source_lane="diagnostic_ultra_loose",
             entry_ts_ms=3000, resolution_ts_ms=2000),
        _row(trade_id="d", trade_group_id="gd",
             source_lane="rulebook_baseline_deterministic",
             entry_ts_ms=500, resolution_ts_ms=2000),  # legitimate
    ]
    kept, summary = _apply_causality_gate(trades)
    assert {t.trade_id for t in kept} == {"d"}
    assert summary["per_lane_suppressed"] == {
        "rulebook_aggressive_deterministic": 2,
        "diagnostic_ultra_loose": 1,
    }
    assert summary["total_legs_before"] == 4
    assert summary["total_legs_after"] == 1
    assert summary["groups_suppressed"] == 3


def test_group_level_drop_removes_both_legs() -> None:
    """If any leg of a paired trade fails causality, both legs of the group
    are dropped — preserves the paired-trade invariant."""
    trades = [
        _row(trade_id="a", trade_group_id="pair1", leg="a",
             entry_ts_ms=500, resolution_ts_ms=2000),   # legitimate (early)
        _row(trade_id="b", trade_group_id="pair1", leg="b",
             entry_ts_ms=3000, resolution_ts_ms=2000),  # post-resolution
        _row(trade_id="c", trade_group_id="pair2", leg="a",
             entry_ts_ms=400, resolution_ts_ms=2000),
        _row(trade_id="d", trade_group_id="pair2", leg="b",
             entry_ts_ms=600, resolution_ts_ms=2000),
    ]
    kept, summary = _apply_causality_gate(trades)
    # pair1: both legs dropped (one fails).  pair2: both kept.
    assert {t.trade_id for t in kept} == {"c", "d"}
    assert summary["suppressed"] == 2
    assert summary["groups_suppressed"] == 1
