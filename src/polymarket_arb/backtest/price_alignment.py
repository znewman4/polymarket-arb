"""Align two price-history series for backtest replay."""

from __future__ import annotations

from typing import Literal

from ..storage.base import PriceHistoryRow
from ..strategies.nesting_contradiction import AlignedPricePoint

# Default freshness threshold: prices older than this are labelled "stale".
# Prices within this window are labelled "exact" or "fresh".
_FRESHNESS_THRESHOLD_MS = 15 * 60 * 1000   # 15 min  → "exact"
_STALE_THRESHOLD_MS = 6 * 60 * 60 * 1000   # 6 hours → "stale"


def _quality(staleness_ms: int) -> str:
    """Map price staleness to an alignment quality label."""
    if staleness_ms <= _FRESHNESS_THRESHOLD_MS:
        return "exact"
    if staleness_ms <= _STALE_THRESHOLD_MS:
        return "fresh"
    return "stale"


def align_price_series(
    rows_a: list[PriceHistoryRow],
    rows_b: list[PriceHistoryRow],
    *,
    start_ts_ms: int | None = None,
    end_ts_ms: int | None = None,
    signal_interval_ms: int = 3600 * 1000,
    staleness_limit_ms: int = 6 * 3600 * 1000,
    alignment_mode: Literal[
        "forward_fill_max_age",
        "strict_exact",
        "nearest_within_window",
        "bucketed_snapshot",
    ] = "forward_fill_max_age",
) -> list[AlignedPricePoint]:
    """Produce aligned (ts, price_a, price_b) using the selected alignment mode.

    All modes enforce no-lookahead: only prices with ts_ms ≤ tick are ever used.

    Modes
    -----
    forward_fill_max_age (default / current behaviour):
        At each tick, use the most recent price ≤ tick for each series.
        Drop the tick if either price is older than ``staleness_limit_ms``.

    strict_exact:
        Same logic but staleness tolerance is set to 15 minutes.  Only
        tick-aligned (or near-tick) prices produce aligned points.  Useful
        for auditing; will produce far fewer points on sparse data.

    nearest_within_window:
        Alias for forward_fill_max_age.  Provided so presets can name the
        mode explicitly without changing behaviour; future versions may
        refine this to search ±window while preserving the no-future rule.

    bucketed_snapshot:
        Groups ticks into ``signal_interval_ms`` buckets and emits at most
        one point per bucket (the first qualifying tick in the bucket).
        Useful to reduce density when interval is small.

    Every returned AlignedPricePoint carries:
        staleness_a_ms   - tick minus price_a actual ts
        staleness_b_ms   - tick minus price_b actual ts
        alignment_quality - "exact" (<15 min), "fresh" (<6 h), "stale" (>=6 h)
    """
    if not rows_a or not rows_b:
        return []

    # For strict_exact, use a much tighter staleness window (15 min).
    effective_staleness = staleness_limit_ms
    if alignment_mode == "strict_exact":
        effective_staleness = _FRESHNESS_THRESHOLD_MS

    sorted_a = sorted(rows_a, key=lambda r: r.ts_ms)
    sorted_b = sorted(rows_b, key=lambda r: r.ts_ms)

    ts_start = start_ts_ms or min(sorted_a[0].ts_ms, sorted_b[0].ts_ms)
    ts_end = end_ts_ms or max(sorted_a[-1].ts_ms, sorted_b[-1].ts_ms)

    result: list[AlignedPricePoint] = []
    tick = ts_start

    ptr_a = 0
    ptr_b = 0
    last_a: PriceHistoryRow | None = None
    last_b: PriceHistoryRow | None = None

    # For bucketed_snapshot: track which bucket we last emitted.
    last_emitted_bucket: int = -1

    while tick <= ts_end:
        # Advance pointers to include all rows up to `tick` (no lookahead).
        while ptr_a < len(sorted_a) and sorted_a[ptr_a].ts_ms <= tick:
            last_a = sorted_a[ptr_a]
            ptr_a += 1
        while ptr_b < len(sorted_b) and sorted_b[ptr_b].ts_ms <= tick:
            last_b = sorted_b[ptr_b]
            ptr_b += 1

        if last_a is not None and last_b is not None:
            age_a = tick - last_a.ts_ms
            age_b = tick - last_b.ts_ms
            if age_a <= effective_staleness and age_b <= effective_staleness:
                quality = _quality(max(age_a, age_b))

                if alignment_mode == "bucketed_snapshot":
                    bucket = tick // signal_interval_ms
                    if bucket == last_emitted_bucket:
                        tick += signal_interval_ms
                        continue
                    last_emitted_bucket = bucket

                result.append(AlignedPricePoint(
                    ts_ms=tick,
                    price_a=last_a.price,
                    price_b=last_b.price,
                    price_a_ts_ms=last_a.ts_ms,
                    price_b_ts_ms=last_b.ts_ms,
                    staleness_a_ms=age_a,
                    staleness_b_ms=age_b,
                    alignment_quality=quality,
                ))

        tick += signal_interval_ms

    return result
