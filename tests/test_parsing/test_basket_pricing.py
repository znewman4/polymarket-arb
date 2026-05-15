from __future__ import annotations

from decimal import Decimal

import pytest

from polymarket_arb.parsing.basket_pricing import (
    InsufficientDepth,
    executable_buy_cost,
    executable_sell_proceeds,
)
from polymarket_arb.storage.base import OrderbookLevel


def test_executable_buy_cost_walks_asks_low_to_high():
    asks = [OrderbookLevel(Decimal("0.53"), Decimal("10")), OrderbookLevel(Decimal("0.52"), Decimal("5"))]
    assert executable_buy_cost(asks, Decimal("7")) == Decimal("3.66")


def test_executable_sell_proceeds_walks_bids_high_to_low():
    bids = [OrderbookLevel(Decimal("0.47"), Decimal("10")), OrderbookLevel(Decimal("0.48"), Decimal("5"))]
    assert executable_sell_proceeds(bids, Decimal("7")) == Decimal("3.34")


def test_insufficient_depth_raises():
    with pytest.raises(InsufficientDepth):
        executable_buy_cost([OrderbookLevel(Decimal("0.52"), Decimal("1"))], Decimal("2"))
