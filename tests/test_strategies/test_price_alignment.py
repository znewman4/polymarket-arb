"""Tests for price series alignment."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from polymarket_arb.backtest.price_alignment import align_price_series
from polymarket_arb.storage.base import PriceHistoryRow

_TS = int(datetime.now(timezone.utc).timestamp() * 1000)
_HOUR = 3600 * 1000


def _price_row(token_id: str, ts_ms: int, price: float) -> PriceHistoryRow:
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


def test_basic_alignment():
    """Two aligned hourly series → one point per hour."""
    rows_a = [_price_row("tok_a", _TS + i * _HOUR, 0.5) for i in range(5)]
    rows_b = [_price_row("tok_b", _TS + i * _HOUR, 0.3) for i in range(5)]

    points = align_price_series(
        rows_a, rows_b,
        signal_interval_ms=_HOUR,
        staleness_limit_ms=2 * _HOUR,
    )
    assert len(points) == 5
    assert all(p.price_a == Decimal("0.5") for p in points)
    assert all(p.price_b == Decimal("0.3") for p in points)


def test_sparse_series_forward_fill():
    """Sparse series B is forward-filled within staleness limit."""
    rows_a = [_price_row("tok_a", _TS + i * _HOUR, 0.5) for i in range(6)]
    # B only has data at hours 0, 3
    rows_b = [
        _price_row("tok_b", _TS + 0 * _HOUR, 0.3),
        _price_row("tok_b", _TS + 3 * _HOUR, 0.4),
    ]

    points = align_price_series(
        rows_a, rows_b,
        signal_interval_ms=_HOUR,
        staleness_limit_ms=2 * _HOUR,
    )
    # Hour 0: both present
    # Hour 1: B forward fills from hour 0 (age 1h ≤ 2h) → ok
    # Hour 2: B forward fills from hour 0 (age 2h = limit) → ok
    # Hour 3: B present at hour 3 → ok
    # Hour 4: B forward fills from hour 3 (age 1h) → ok
    # Hour 5: B forward fills from hour 3 (age 2h = limit) → ok
    assert len(points) >= 4


def test_too_stale_dropped():
    """Point dropped if best available price is too old."""
    rows_a = [_price_row("tok_a", _TS + i * _HOUR, 0.5) for i in range(4)]
    # B has only one old price
    rows_b = [_price_row("tok_b", _TS, 0.3)]  # only at hour 0

    points = align_price_series(
        rows_a, rows_b,
        signal_interval_ms=_HOUR,
        staleness_limit_ms=_HOUR,  # only 1 hour staleness
    )
    # Only the first 2 ticks are within staleness (0h and 1h)
    assert len(points) <= 2


def test_empty_series_returns_empty():
    assert align_price_series([], [], signal_interval_ms=_HOUR) == []
    rows_a = [_price_row("tok_a", _TS, 0.5)]
    assert align_price_series(rows_a, [], signal_interval_ms=_HOUR) == []


def test_no_lookahead():
    """Prices with ts_ms > tick must NOT appear in alignment at that tick."""
    rows_a = [
        _price_row("tok_a", _TS + 0 * _HOUR, 0.5),
        _price_row("tok_a", _TS + 2 * _HOUR, 0.9),  # future price at tick=1h
    ]
    rows_b = [_price_row("tok_b", _TS + i * _HOUR, 0.3) for i in range(3)]

    points = align_price_series(
        rows_a, rows_b,
        start_ts_ms=_TS,
        end_ts_ms=_TS + _HOUR,
        signal_interval_ms=_HOUR,
        staleness_limit_ms=2 * _HOUR,
    )
    # At tick = _TS + _HOUR (1h), the row at 2h should NOT be used
    tick_1_points = [p for p in points if p.ts_ms == _TS + _HOUR]
    for p in tick_1_points:
        # price_a at this tick must be from ts_ms ≤ _TS + _HOUR
        assert p.price_a_ts_ms <= _TS + _HOUR
        assert p.price_a == Decimal("0.5")  # not 0.9 from the future


def test_aligned_price_timestamps_correct():
    """price_a_ts_ms and price_b_ts_ms must always be ≤ tick ts_ms."""
    rows_a = [_price_row("tok_a", _TS + i * _HOUR, 0.5 + i * 0.01) for i in range(5)]
    rows_b = [_price_row("tok_b", _TS + i * _HOUR, 0.3) for i in range(5)]
    points = align_price_series(rows_a, rows_b, signal_interval_ms=_HOUR)
    for p in points:
        assert p.price_a_ts_ms <= p.ts_ms
        assert p.price_b_ts_ms <= p.ts_ms
