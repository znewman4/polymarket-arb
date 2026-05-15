from __future__ import annotations

from decimal import Decimal

from polymarket_arb.backtest.execution_sim import (
    simulate_buy_from_orderbook,
    simulate_sell_from_orderbook,
)
from polymarket_arb.storage.base import OrderbookLevel, OrderbookSnapshot


def _book() -> OrderbookSnapshot:
    return OrderbookSnapshot(
        token_id="tok",
        condition_id="0xc",
        market_slug=None,
        timestamp_ms=1,
        bids=[OrderbookLevel(Decimal("0.49"), Decimal("5")), OrderbookLevel(Decimal("0.48"), Decimal("5"))],
        asks=[OrderbookLevel(Decimal("0.51"), Decimal("5")), OrderbookLevel(Decimal("0.52"), Decimal("5"))],
        book_hash=None,
        source="rest",
        schema_version=1,
        ingested_ts_ms=1,
    )


def test_buy_exact_and_fee():
    filled, notional, fees, partial, reason = simulate_buy_from_orderbook(
        _book(), Decimal("5"), fee_bps=Decimal("10")
    )
    assert filled == Decimal("5")
    assert notional == Decimal("2.55")
    assert fees == Decimal("0.00255")
    assert partial is False
    assert reason == "filled"


def test_partial_and_limit():
    filled, _, _, partial, reason = simulate_buy_from_orderbook(
        _book(), Decimal("8"), limit_price=Decimal("0.51")
    )
    assert filled == Decimal("5")
    assert partial is True
    assert reason == "partial_insufficient_depth"


def test_sell_path():
    filled, notional, _, partial, reason = simulate_sell_from_orderbook(_book(), Decimal("6"))
    assert filled == Decimal("6")
    assert notional == Decimal("2.93")
    assert partial is False
    assert reason == "filled"
