"""Preflight gate. The only producer of PreflightToken.

Every check writes a row to ``risk_snapshots`` (regardless of pass/fail) so
the audit trail is complete: any "near-trade" event leaves a record of every
gate that allowed it through.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from typing import Protocol

from loguru import logger

from ..storage.base import RiskSnapshot, RiskSnapshotsRepository
from .models import (
    CheckResult,
    CheckStatus,
    PreflightContext,
    PreflightFailure,
    PreflightReport,
    PreflightToken,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


class PreflightCheck(Protocol):
    """All checks implement this Protocol."""

    name: str

    async def check(self, ctx: PreflightContext) -> CheckResult: ...


class PreflightGate:
    """Run an ordered list of checks; emit a token if every check passes.

    The gate writes one ``RiskSnapshot`` per evaluation, win or lose. This is
    the only audit log that matters when reviewing whether a trade should
    have happened.
    """

    def __init__(
        self,
        checks: Iterable[PreflightCheck],
        risk_repo: RiskSnapshotsRepository,
    ) -> None:
        self._checks = list(checks)
        self._repo = risk_repo

    async def evaluate(self, ctx: PreflightContext) -> PreflightReport:
        results: list[CheckResult] = []
        overall: CheckStatus = CheckStatus.PASS
        for chk in self._checks:
            try:
                res = await chk.check(ctx)
            except Exception as exc:  # belt + braces: a check that raises is a fail
                res = CheckResult(
                    name=chk.name, status=CheckStatus.FAIL,
                    reason=f"check raised: {exc!r}",
                )
            results.append(res)
            if not res.passed:
                overall = CheckStatus.FAIL
                # Continue running remaining checks so the audit row is complete.

        order_id = ctx.order.id if ctx.order else None
        report = PreflightReport(
            overall=overall, checks=results,
            strategy_id=ctx.strategy_id, order_id=order_id,
        )
        self._record(report)
        return report

    def _record(self, report: PreflightReport) -> None:
        snap = RiskSnapshot(
            event_id=uuid.uuid4().hex,
            event_ts_ms=_now_ms(),
            strategy_id=report.strategy_id,
            order_id=report.order_id,
            overall=report.overall.value,
            checks=[
                {"name": r.name, "status": r.status.value, "reason": r.reason,
                 "detail": r.detail}
                for r in report.checks
            ],
            schema_version=1,
            ingested_ts_ms=_now_ms(),
        )
        try:
            self._repo.append(snap)
        except Exception as exc:  # never let audit-write failure mask the result
            logger.error("failed to write risk snapshot", error=str(exc))

    async def assert_can_trade(self, ctx: PreflightContext) -> PreflightToken:
        """Run the gate, raise on failure, otherwise return a token."""

        report = await self.evaluate(ctx)
        if not report.passed:
            raise PreflightFailure(report)
        return PreflightToken._new(
            order_id=ctx.order.id if ctx.order else None,
            report=report,
            now_ms=_now_ms(),
        )
