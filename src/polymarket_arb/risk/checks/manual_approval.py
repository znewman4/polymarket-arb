"""One-shot manual approval token. Phase 10 only.

A file ``data/.live_approved_<order_id>`` must exist. The check consumes
(deletes) the file on success so each token is single-use.
"""

from __future__ import annotations

from pathlib import Path

from ..models import CheckResult, CheckStatus, PreflightContext


class ManualApprovalCheck:
    name = "manual_approval"

    def __init__(self, data_root: Path | None = None) -> None:
        self._data_root = data_root

    async def check(self, ctx: PreflightContext) -> CheckResult:
        # Without an order, manual approval is N/A.
        if ctx.order is None or self._data_root is None:
            return CheckResult(self.name, CheckStatus.PASS,
                               detail={"reason": "no order or no data_root"})
        token_path = self._data_root / f".live_approved_{ctx.order.id}"
        if not token_path.exists():
            return CheckResult(self.name, CheckStatus.FAIL,
                               reason=f"no manual approval token at {token_path}")
        try:
            token_path.unlink()
        except OSError as exc:
            return CheckResult(self.name, CheckStatus.FAIL,
                               reason=f"could not consume token: {exc}")
        return CheckResult(self.name, CheckStatus.PASS)
