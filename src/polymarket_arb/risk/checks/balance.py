"""Sufficient USDC balance for the proposed order. Phase 7 wires this to
the position-state view; Phase 0 trusts ``OrderIntent.available_balance_usdc``
if provided and otherwise treats balance as unknown→fail-safe."""

from __future__ import annotations

from decimal import Decimal

from ..models import CheckResult, CheckStatus, PreflightContext


class BalanceAvailableCheck:
    name = "balance_available"

    async def check(self, ctx: PreflightContext) -> CheckResult:
        if ctx.order is None:
            return CheckResult(self.name, CheckStatus.PASS,
                               detail={"reason": "no order"})
        if ctx.order.available_balance_usdc is None:
            return CheckResult(self.name, CheckStatus.FAIL,
                               reason="available_balance_usdc unknown — refusing fail-safe")
        stake = (ctx.order.price * ctx.order.size).quantize(Decimal("0.0001"))
        if ctx.order.available_balance_usdc < stake:
            return CheckResult(self.name, CheckStatus.FAIL,
                               reason=f"balance {ctx.order.available_balance_usdc} < "
                                      f"stake {stake}")
        return CheckResult(self.name, CheckStatus.PASS,
                           detail={"stake_usdc": str(stake),
                                   "balance_usdc": str(ctx.order.available_balance_usdc)})
