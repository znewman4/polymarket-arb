"""Refuse to proceed while the kill switch is active."""

from __future__ import annotations

from pathlib import Path

from ...monitoring import kill_switch
from ..models import CheckResult, CheckStatus, PreflightContext


class KillSwitchOffCheck:
    name = "kill_switch_off"

    def __init__(self, killswitch_path: Path) -> None:
        self._path = killswitch_path

    async def check(self, ctx: PreflightContext) -> CheckResult:
        if kill_switch.is_active(self._path):
            return CheckResult(self.name, CheckStatus.FAIL,
                               reason=f"kill switch active (file or signal): {self._path}")
        return CheckResult(self.name, CheckStatus.PASS)
