"""Tests for Phase B price alignment modes and staleness labelling."""

from __future__ import annotations

from decimal import Decimal

import pytest

from polymarket_arb.backtest.price_alignment import align_price_series
from polymarket_arb.storage.base import PriceHistoryRow
from polymarket_arb.strategies.nesting_contradiction import AlignedPricePoint

_TS = 1_700_000_000_000
_HOUR = 3_600_000  # ms
_MIN = 60_000      # ms


def _row(token_id: str, ts_ms: int, price: float = 0.5) -> PriceHistoryRow:
    return PriceHistoryRow(
        market_id="mkt",
        condition_id=None,
        token_id=token_id,
        outcome=None,
        ts_ms=ts_ms,
        price=Decimal(str(price)),
        source="clob",
        fidelity="1h",
        interval="1h",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _dense(token_id: str, start: int, n: int, price: float = 0.5) -> list[PriceHistoryRow]:
    return [_row(token_id, start + i * _HOUR, price) for i in range(n)]


# ── AlignedPricePoint backward compatibility ──────────────────────────────────


def test_aligned_price_point_new_fields_have_defaults() -> None:
    pt = AlignedPricePoint(
        ts_ms=_TS,
        price_a=Decimal("0.5"),
        price_b=Decimal("0.3"),
        price_a_ts_ms=_TS - 100,
        price_b_ts_ms=_TS - 100,
    )
    assert pt.staleness_a_ms == 0
    assert pt.staleness_b_ms == 0
    assert pt.alignment_quality == "fresh"


def test_aligned_price_point_positional_five_args_still_works() -> None:
    pt = AlignedPricePoint(_TS, Decimal("0.7"), Decimal("0.6"), _TS, _TS)
    assert pt.ts_ms == _TS
    assert pt.price_a == Decimal("0.7")
    assert pt.staleness_a_ms == 0  # default


# ── staleness_a_ms / staleness_b_ms ──────────────────────────────────────────


def test_staleness_fields_populated() -> None:
    rows_a = [_row("a", _TS)]           # only at start
    rows_b = [_row("b", _TS)]
    pts = align_price_series(
        rows_a, rows_b,
        signal_interval_ms=_HOUR,
        staleness_limit_ms=3 * _HOUR,
    )
    assert len(pts) >= 1
    # At tick = _TS, staleness = 0
    tick0 = next(p for p in pts if p.ts_ms == _TS)
    assert tick0.staleness_a_ms == 0
    assert tick0.staleness_b_ms == 0


def test_staleness_increases_over_time() -> None:
    rows_a = [_row("a", _TS)]
    rows_b = [_row("b", _TS)]
    pts = align_price_series(
        rows_a, rows_b,
        start_ts_ms=_TS,
        end_ts_ms=_TS + 2 * _HOUR,
        signal_interval_ms=_HOUR,
        staleness_limit_ms=3 * _HOUR,
    )
    # Second tick at _TS + 1h should have staleness = 1h
    tick1 = next(p for p in pts if p.ts_ms == _TS + _HOUR)
    assert tick1.staleness_a_ms == _HOUR
    assert tick1.staleness_b_ms == _HOUR


def test_no_lookahead_all_modes() -> None:
    """price_a_ts_ms and price_b_ts_ms must always be ≤ tick.ts_ms."""
    rows_a = [_row("a", _TS), _row("a", _TS + 2 * _HOUR, 0.9)]
    rows_b = [_row("b", _TS + i * _HOUR) for i in range(3)]
    for mode in ("forward_fill_max_age", "strict_exact", "nearest_within_window", "bucketed_snapshot"):
        pts = align_price_series(
            rows_a, rows_b,
            signal_interval_ms=_HOUR,
            staleness_limit_ms=4 * _HOUR,
            alignment_mode=mode,  # type: ignore[arg-type]
        )
        for p in pts:
            assert p.price_a_ts_ms <= p.ts_ms, f"Lookahead in mode {mode!r} at ts={p.ts_ms}"
            assert p.price_b_ts_ms <= p.ts_ms, f"Lookahead in mode {mode!r} at ts={p.ts_ms}"


# ── alignment_quality labelling ───────────────────────────────────────────────


def test_quality_exact_when_fresh() -> None:
    rows_a = [_row("a", _TS + i * _HOUR) for i in range(3)]
    rows_b = [_row("b", _TS + i * _HOUR) for i in range(3)]
    pts = align_price_series(rows_a, rows_b, signal_interval_ms=_HOUR)
    # At each tick the price is exactly from that tick → staleness = 0 → "exact"
    for p in pts:
        assert p.alignment_quality == "exact"


def test_quality_stale_when_old() -> None:
    # A only has data at _TS, B also at _TS
    rows_a = [_row("a", _TS)]
    rows_b = [_row("b", _TS)]
    # go 8 hours out — staleness > 6h threshold
    pts = align_price_series(
        rows_a, rows_b,
        start_ts_ms=_TS,
        end_ts_ms=_TS + 8 * _HOUR,
        signal_interval_ms=_HOUR,
        staleness_limit_ms=10 * _HOUR,
    )
    stale_pts = [p for p in pts if p.staleness_a_ms > 6 * _HOUR]
    assert stale_pts, "Expected some stale-labelled points"
    for p in stale_pts:
        assert p.alignment_quality == "stale"


def test_quality_fresh_intermediate() -> None:
    # Prices at _TS, ticks at _TS through _TS + 4h with 6h staleness limit
    rows_a = [_row("a", _TS)]
    rows_b = [_row("b", _TS)]
    pts = align_price_series(
        rows_a, rows_b,
        start_ts_ms=_TS,
        end_ts_ms=_TS + 4 * _HOUR,
        signal_interval_ms=_HOUR,
        staleness_limit_ms=6 * _HOUR,
    )
    # 1h-4h staleness → "fresh" (between 15 min and 6h)
    for p in pts:
        if 0 < p.staleness_a_ms <= 6 * _HOUR:
            if p.staleness_a_ms <= 15 * _MIN:
                assert p.alignment_quality == "exact"
            elif p.staleness_a_ms <= 6 * _HOUR:
                assert p.alignment_quality in ("exact", "fresh")


# ── alignment_mode: forward_fill_max_age (default) ───────────────────────────


def test_default_mode_unchanged_behaviour() -> None:
    """Default mode must produce same results as before (backward compat)."""
    rows_a = [_row("a", _TS + i * _HOUR, 0.5) for i in range(5)]
    rows_b = [_row("b", _TS + i * _HOUR, 0.3) for i in range(5)]
    pts_default = align_price_series(rows_a, rows_b, signal_interval_ms=_HOUR)
    pts_explicit = align_price_series(
        rows_a, rows_b,
        signal_interval_ms=_HOUR,
        alignment_mode="forward_fill_max_age",
    )
    assert len(pts_default) == len(pts_explicit)
    for a, b in zip(pts_default, pts_explicit):
        assert a.price_a == b.price_a
        assert a.price_b == b.price_b
        assert a.ts_ms == b.ts_ms


# ── alignment_mode: strict_exact ─────────────────────────────────────────────


def test_strict_exact_produces_fewer_points() -> None:
    """strict_exact only accepts prices within 15 min → many fewer points on sparse data."""
    rows_a = [_row("a", _TS)]           # one price at start
    rows_b = [_row("b", _TS)]           # one price at start
    pts_strict = align_price_series(
        rows_a, rows_b,
        start_ts_ms=_TS,
        end_ts_ms=_TS + 5 * _HOUR,
        signal_interval_ms=_HOUR,
        staleness_limit_ms=6 * _HOUR,
        alignment_mode="strict_exact",
    )
    pts_fill = align_price_series(
        rows_a, rows_b,
        start_ts_ms=_TS,
        end_ts_ms=_TS + 5 * _HOUR,
        signal_interval_ms=_HOUR,
        staleness_limit_ms=6 * _HOUR,
        alignment_mode="forward_fill_max_age",
    )
    assert len(pts_strict) < len(pts_fill), (
        "strict_exact should produce fewer points on sparse data"
    )


def test_strict_exact_accepts_fresh_prices() -> None:
    """strict_exact accepts prices that are very close to the tick."""
    rows_a = [_row("a", _TS + i * _HOUR) for i in range(5)]
    rows_b = [_row("b", _TS + i * _HOUR) for i in range(5)]
    pts = align_price_series(
        rows_a, rows_b,
        signal_interval_ms=_HOUR,
        alignment_mode="strict_exact",
    )
    assert len(pts) == 5


# ── alignment_mode: larger staleness = more ticks ────────────────────────────


def test_larger_staleness_produces_more_ticks() -> None:
    """Exploratory preset's 24h staleness produces more ticks than strict 6h."""
    rows_a = [_row("a", _TS)]
    rows_b = [_row("b", _TS)]
    pts_strict = align_price_series(
        rows_a, rows_b,
        start_ts_ms=_TS,
        end_ts_ms=_TS + 20 * _HOUR,
        signal_interval_ms=_HOUR,
        staleness_limit_ms=6 * _HOUR,
    )
    pts_exploratory = align_price_series(
        rows_a, rows_b,
        start_ts_ms=_TS,
        end_ts_ms=_TS + 20 * _HOUR,
        signal_interval_ms=_HOUR,
        staleness_limit_ms=24 * _HOUR,
    )
    assert len(pts_exploratory) > len(pts_strict)


# ── bucketed_snapshot ─────────────────────────────────────────────────────────


def test_bucketed_snapshot_emits_one_per_bucket() -> None:
    rows_a = [_row("a", _TS + i * _HOUR) for i in range(8)]
    rows_b = [_row("b", _TS + i * _HOUR) for i in range(8)]
    pts = align_price_series(
        rows_a, rows_b,
        signal_interval_ms=_HOUR,
        alignment_mode="bucketed_snapshot",
    )
    # Each bucket = 1 hour, so at most one point per bucket
    ts_set = {p.ts_ms for p in pts}
    assert len(ts_set) == len(pts)  # all unique timestamps


# ── existing tests still pass with new fields ─────────────────────────────────


def test_existing_tests_unaffected_by_new_fields() -> None:
    rows_a = [_row("a", _TS + i * _HOUR, 0.5) for i in range(5)]
    rows_b = [_row("b", _TS + i * _HOUR, 0.3) for i in range(5)]
    pts = align_price_series(rows_a, rows_b, signal_interval_ms=_HOUR)
    assert len(pts) == 5
    assert all(p.price_a == Decimal("0.5") for p in pts)
    assert all(p.price_b == Decimal("0.3") for p in pts)
    # New fields present
    assert all(hasattr(p, "staleness_a_ms") for p in pts)
    assert all(hasattr(p, "alignment_quality") for p in pts)
