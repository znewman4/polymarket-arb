"""Infer market resolutions from price-history convergence + closed flag."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from ..storage.base import MarketRow, PriceHistoryRow


@dataclass(frozen=True)
class InferredResolution:
    market_id: str
    token_id_yes: str
    token_id_no: str
    resolution_ts_ms: int
    yes_outcome: Literal["yes", "no", "unresolved"]
    confidence: float
    inference_method: Literal["price_convergence", "closed_flag", "missing"]


def _last_n_prices(rows: list[PriceHistoryRow], n_ms: int, cutoff_ts_ms: int) -> list[Decimal]:
    """Prices for the last n_ms milliseconds before cutoff."""
    cutoff = cutoff_ts_ms
    start = cutoff - n_ms
    return [r.price for r in rows if start <= r.ts_ms <= cutoff]


def infer_resolutions(
    markets: list[MarketRow],
    prices_by_token: dict[str, list[PriceHistoryRow]],
    *,
    epsilon: float = 0.05,
    lookback_ms: int = 24 * 3600 * 1000,
) -> dict[str, InferredResolution]:
    """Infer resolved outcome for each market.

    Rules (in priority order):
    1. If closed and we have price history for YES token converging to ε of 0 or 1
       in the final `lookback_ms` of data → resolved with that outcome.
    2. If closed but prices are ambiguous → unresolved.
    3. If open → unresolved.
    """
    result: dict[str, InferredResolution] = {}

    for market in markets:
        if len(market.outcomes) != 2 or len(market.clob_token_ids) != 2:
            continue

        token_yes_id = market.clob_token_ids[0]
        token_no_id = market.clob_token_ids[1]
        rows_yes = prices_by_token.get(token_yes_id, [])

        if not rows_yes:
            result[market.id] = InferredResolution(
                market_id=market.id,
                token_id_yes=token_yes_id,
                token_id_no=token_no_id,
                resolution_ts_ms=market.resolved_at_ms or 0,
                yes_outcome="unresolved",
                confidence=0.0,
                inference_method="missing",
            )
            continue

        sorted_rows = sorted(rows_yes, key=lambda r: r.ts_ms)
        last_ts = sorted_rows[-1].ts_ms
        recent_prices = _last_n_prices(sorted_rows, lookback_ms, last_ts)

        if not recent_prices:
            recent_prices = [sorted_rows[-1].price]

        avg_recent = sum(recent_prices) / Decimal(len(recent_prices))

        if not market.closed:
            result[market.id] = InferredResolution(
                market_id=market.id,
                token_id_yes=token_yes_id,
                token_id_no=token_no_id,
                resolution_ts_ms=0,
                yes_outcome="unresolved",
                confidence=0.0,
                inference_method="missing",
            )
            continue

        # Market is closed
        resolution_ts = market.resolved_at_ms or market.closed_at_ms or last_ts

        if avg_recent >= Decimal(str(1.0 - epsilon)):
            result[market.id] = InferredResolution(
                market_id=market.id,
                token_id_yes=token_yes_id,
                token_id_no=token_no_id,
                resolution_ts_ms=resolution_ts,
                yes_outcome="yes",
                confidence=float(avg_recent),
                inference_method="price_convergence",
            )
        elif avg_recent <= Decimal(str(epsilon)):
            result[market.id] = InferredResolution(
                market_id=market.id,
                token_id_yes=token_yes_id,
                token_id_no=token_no_id,
                resolution_ts_ms=resolution_ts,
                yes_outcome="no",
                confidence=float(Decimal("1") - avg_recent),
                inference_method="price_convergence",
            )
        else:
            result[market.id] = InferredResolution(
                market_id=market.id,
                token_id_yes=token_yes_id,
                token_id_no=token_no_id,
                resolution_ts_ms=resolution_ts,
                yes_outcome="unresolved",
                confidence=0.5,
                inference_method="closed_flag",
            )

    return result
