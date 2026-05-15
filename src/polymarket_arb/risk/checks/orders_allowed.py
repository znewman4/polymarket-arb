"""``orders_allowed`` flag must be true."""

from __future__ import annotations

from ...settings import Settings
from ..models import CheckResult, CheckStatus, PreflightContext


class OrdersAllowedFlagCheck:
    name = "orders_allowed_flag"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def check(self, ctx: PreflightContext) -> CheckResult:
        if not self._settings.orders_allowed:
            return CheckResult(self.name, CheckStatus.FAIL,
                               reason="orders_allowed=false (research-only mode)")
        return CheckResult(self.name, CheckStatus.PASS)
