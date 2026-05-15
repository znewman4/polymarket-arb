"""When the caller asks for live trading, paper-mode must NOT be active."""

from __future__ import annotations

from ..models import CheckResult, CheckStatus, PreflightContext


class PaperModeNotActiveCheck:
    name = "paper_mode_not_active"

    async def check(self, ctx: PreflightContext) -> CheckResult:
        if ctx.paper_mode:
            return CheckResult(self.name, CheckStatus.FAIL,
                               reason="paper_mode=true; live order requested")
        return CheckResult(self.name, CheckStatus.PASS)
