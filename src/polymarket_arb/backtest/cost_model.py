"""Cost estimation for simulated trades."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

ExecutionModel = Literal["price_history_only", "spread_model", "recorded_depth", "mark_only"]

_EXECUTION_MODEL_CONFIDENCE = {
    "price_history_only": 0.4,
    "spread_model": 0.7,
    "recorded_depth": 1.0,
    "mark_only": 0.1,
}


@dataclass(frozen=True)
class CostEstimate:
    fee_usdc: Decimal
    slippage_usdc: Decimal
    fill_price: Decimal
    execution_model_confidence: float


def estimate_costs(
    *,
    notional_usdc: Decimal,
    mid_price: Decimal,
    execution_model: ExecutionModel,
    fee_bps: Decimal,
    builder_fee_bps: Decimal = Decimal("0"),
    slippage_bps: Decimal,
) -> CostEstimate:
    """Estimate fill_price and costs for a buy order.

    Always represents a 'buy' (we never short on Polymarket).
    Price is pushed UP by slippage for buys.
    """
    total_fee_bps = fee_bps + builder_fee_bps
    fee_usdc = notional_usdc * total_fee_bps / Decimal("10000")

    if execution_model == "mark_only":
        return CostEstimate(
            fee_usdc=Decimal("0"),
            slippage_usdc=Decimal("0"),
            fill_price=mid_price,
            execution_model_confidence=_EXECUTION_MODEL_CONFIDENCE["mark_only"],
        )

    slippage_usdc = notional_usdc * slippage_bps / Decimal("10000")
    # For a buy: we fill at mid_price * (1 + slippage_bps/10000)
    slippage_fraction = slippage_bps / Decimal("10000")
    fill_price = mid_price * (Decimal("1") + slippage_fraction)
    fill_price = min(fill_price, Decimal("1"))  # price cannot exceed 1

    confidence = _EXECUTION_MODEL_CONFIDENCE.get(execution_model, 0.4)
    return CostEstimate(
        fee_usdc=fee_usdc,
        slippage_usdc=slippage_usdc,
        fill_price=fill_price,
        execution_model_confidence=confidence,
    )
