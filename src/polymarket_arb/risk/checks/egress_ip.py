"""Egress IP must be in the configured country whitelist."""

from __future__ import annotations

from ...compliance.geo_check import ComplianceError, GeoChecker
from ...settings import Settings
from ..models import CheckResult, CheckStatus, PreflightContext


class EgressIPWhitelistCheck:
    name = "egress_ip_whitelist"

    def __init__(self, geo_checker: GeoChecker, settings: Settings) -> None:
        self._geo = geo_checker
        self._settings = settings

    async def check(self, ctx: PreflightContext) -> CheckResult:
        try:
            info = await self._geo.fetch()
            self._geo.assert_in_whitelist(info)
        except ComplianceError as exc:
            return CheckResult(self.name, CheckStatus.FAIL, reason=str(exc))
        return CheckResult(
            self.name, CheckStatus.PASS,
            detail={"ip": info.ip, "country": info.country_iso},
        )
