"""Order respects the configured RiskLimits.

Phase 0 implements stake-per-market, spread, and quote-age. Per-event /
total-unresolved enforcement requires position-state lookups and lands in
Phase 7.
"""

from __future__ import annotations

from decimal import Decimal

from ..models import CheckResult, CheckStatus, PreflightContext


class RiskLimitsCheck:
    name = "risk_limits"

    async def check(self, ctx: PreflightContext) -> CheckResult:
        if ctx.order is None:
            return CheckResult(self.name, CheckStatus.PASS,
                               detail={"reason": "no order"})
        order = ctx.order
        limits = ctx.limits
        stake = (order.price * order.size).quantize(Decimal("0.0001"))
        if stake > limits.max_stake_usdc_per_market:
            return CheckResult(self.name, CheckStatus.FAIL,
                               reason=f"stake {stake} > "
                                      f"max_stake_usdc_per_market {limits.max_stake_usdc_per_market}")
        if order.spread_pct is not None and order.spread_pct > limits.max_spread_pct:
            return CheckResult(self.name, CheckStatus.FAIL,
                               reason=f"spread_pct {order.spread_pct} > "
                                      f"max {limits.max_spread_pct}")
        if order.quote_age_ms is not None and order.quote_age_ms > limits.max_quote_age_ms:
            return CheckResult(self.name, CheckStatus.FAIL,
                               reason=f"quote_age_ms {order.quote_age_ms} > "
                                      f"max {limits.max_quote_age_ms}")
        return CheckResult(self.name, CheckStatus.PASS,
                           detail={"stake_usdc": str(stake)})
