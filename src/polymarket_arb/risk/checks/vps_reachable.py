"""VPS reachability — Phase 0 localhost variant.

When the gate runs *on* the VPS, this check is trivially Pass (we're already
running). When triggered from a remote control plane (Phase 7+) the check
will hit the VPS health endpoint with a recent-heartbeat assertion.
"""

from __future__ import annotations

import socket

from ..models import CheckResult, CheckStatus, PreflightContext


class VPSReachableCheck:
    name = "vps_reachable"

    async def check(self, ctx: PreflightContext) -> CheckResult:
        try:
            # We resolve our own hostname; if even this fails the box is broken.
            socket.gethostbyname(socket.gethostname())
        except OSError as exc:
            return CheckResult(self.name, CheckStatus.FAIL, reason=f"localhost unreachable: {exc}")
        return CheckResult(self.name, CheckStatus.PASS)
