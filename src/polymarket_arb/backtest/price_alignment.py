"""Align two price-history series for backtest replay."""

from __future__ import annotations

from ..storage.base import PriceHistoryRow
from ..strategies.nesting_contradiction import AlignedPricePoint


def align_price_series(
    rows_a: list[PriceHistoryRow],
    rows_b: list[PriceHistoryRow],
    *,
    start_ts_ms: int | None = None,
    end_ts_ms: int | None = None,
    signal_interval_ms: int = 3600 * 1000,
    staleness_limit_ms: int = 6 * 3600 * 1000,
) -> list[AlignedPricePoint]:
    """Produce aligned (ts, price_a, price_b) using forward-fill within staleness.

    At each tick of signal_interval_ms, finds the most recent price ≤ tick for each
    series. Drops ticks where either price is missing or too stale.
    No lookahead: only prices with ts_ms ≤ tick are considered.
    """
    if not rows_a or not rows_b:
        return []

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

    while tick <= ts_end:
        # Advance pointers to include all rows up to `tick`
        while ptr_a < len(sorted_a) and sorted_a[ptr_a].ts_ms <= tick:
            last_a = sorted_a[ptr_a]
            ptr_a += 1
        while ptr_b < len(sorted_b) and sorted_b[ptr_b].ts_ms <= tick:
            last_b = sorted_b[ptr_b]
            ptr_b += 1

        if last_a is not None and last_b is not None:
            age_a = tick - last_a.ts_ms
            age_b = tick - last_b.ts_ms
            if age_a <= staleness_limit_ms and age_b <= staleness_limit_ms:
                result.append(AlignedPricePoint(
                    ts_ms=tick,
                    price_a=last_a.price,
                    price_b=last_b.price,
                    price_a_ts_ms=last_a.ts_ms,
                    price_b_ts_ms=last_b.ts_ms,
                ))

        tick += signal_interval_ms

    return result
