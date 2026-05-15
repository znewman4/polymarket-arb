"""Quote staleness check. Phase 3 (WebSocket) wires the live timestamp lookup;
Phase 0 just enforces the configured ``max_quote_age_ms`` against the value
the caller sets on ``OrderIntent.quote_age_ms``. If quote_age is unknown when
an order is requested, this fails closed."""

from __future__ import annotations

from ..models import CheckResult, CheckStatus, PreflightContext


class OrderbookFreshnessCheck:
    name = "orderbook_freshness"

    async def check(self, ctx: PreflightContext) -> CheckResult:
        if ctx.order is None:
            return CheckResult(self.name, CheckStatus.PASS,
                               detail={"reason": "no order"})
        if ctx.order.quote_age_ms is None:
            return CheckResult(self.name, CheckStatus.FAIL,
                               reason="quote_age_ms unknown — refusing fail-safe")
        if ctx.order.quote_age_ms > ctx.limits.max_quote_age_ms:
            return CheckResult(self.name, CheckStatus.FAIL,
                               reason=f"quote_age_ms {ctx.order.quote_age_ms} > "
                                      f"limit {ctx.limits.max_quote_age_ms}")
        return CheckResult(self.name, CheckStatus.PASS,
                           detail={"quote_age_ms": ctx.order.quote_age_ms})
