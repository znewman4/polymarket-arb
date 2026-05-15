"""Pure depth-walk pricing helpers for CLOB books."""

from __future__ import annotations

from decimal import Decimal

from ..storage.base import OrderbookLevel


class InsufficientDepth(ValueError):
    """Raised when requested size exceeds available orderbook depth."""


def executable_buy_cost(asks: list[OrderbookLevel], size_shares: Decimal) -> Decimal:
    return _walk(sorted(asks, key=lambda x: x.price), size_shares)


def executable_sell_proceeds(bids: list[OrderbookLevel], size_shares: Decimal) -> Decimal:
    return _walk(sorted(bids, key=lambda x: x.price, reverse=True), size_shares)


def _walk(levels: list[OrderbookLevel], size_shares: Decimal) -> Decimal:
    remaining = size_shares
    total = Decimal("0")
    for level in levels:
        take = min(remaining, level.size)
        total += take * level.price
        remaining -= take
        if remaining <= 0:
            return total
    raise InsufficientDepth(f"requested {size_shares} shares, short by {remaining}")
