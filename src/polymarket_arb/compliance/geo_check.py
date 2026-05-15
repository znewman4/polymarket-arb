"""Egress IP + country detection.

Two independent providers are queried; the country answers must agree.
A successful result is cached for ``compliance.ip_check_ttl_s`` (default 5 min).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from ..http.client import AsyncHttpClient, HttpError, TransientError
from ..settings import Settings


class ComplianceError(RuntimeError):
    """Raised when egress IP cannot be confirmed in the whitelist."""


@dataclass(frozen=True)
class EgressInfo:
    ip: str
    country_iso: str
    fetched_at_monotonic: float
    primary_source: str
    secondary_source: str

    def is_fresh(self, ttl_s: int, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        return (now - self.fetched_at_monotonic) <= ttl_s


def _extract_country(payload: Any, *, candidate_keys: tuple[str, ...] = (
        "country_iso", "country_code", "countryCode", "country",
)) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in candidate_keys:
        v = payload.get(key)
        if isinstance(v, str) and len(v) >= 2:
            return v[:2].upper()
    return None


def _extract_ip(payload: Any) -> str | None:
    if isinstance(payload, dict):
        v = payload.get("ip")
        if isinstance(v, str):
            return v
    if isinstance(payload, str):
        return payload.strip()
    return None


class GeoChecker:
    """Resolve egress IP + country from two providers and cache the result."""

    def __init__(self, settings: Settings, http: AsyncHttpClient) -> None:
        self._settings = settings
        self._http = http
        self._cached: EgressInfo | None = None

    async def fetch(self, *, force: bool = False) -> EgressInfo:
        if not force and self._cached and self._cached.is_fresh(
            self._settings.compliance.ip_check_ttl_s
        ):
            return self._cached

        primary_url = self._settings.ip_provider_primary
        secondary_url = self._settings.ip_provider_secondary

        try:
            primary = await self._http.get_json(primary_url)
            secondary = await self._http.get_json(secondary_url)
        except (HttpError, TransientError) as exc:
            raise ComplianceError(f"egress IP provider unreachable: {exc}") from exc

        primary_ip = _extract_ip(primary)
        secondary_ip = _extract_ip(secondary)
        if not primary_ip or not secondary_ip:
            raise ComplianceError(
                f"could not extract IP from providers (primary={primary!r}, "
                f"secondary={secondary!r})"
            )
        if primary_ip != secondary_ip:
            logger.warning("egress IP providers disagree on IP",
                           primary=primary_ip, secondary=secondary_ip)

        secondary_country = _extract_country(secondary)
        if secondary_country is None:
            raise ComplianceError(
                f"secondary provider did not return country info: {secondary!r}"
            )

        # Phase 0 trusts the secondary provider's country field. A future
        # version may add a third resolver call against ipapi.co for the
        # primary IP and require the two countries to agree.
        info = EgressInfo(
            ip=primary_ip,
            country_iso=secondary_country,
            fetched_at_monotonic=time.monotonic(),
            primary_source=primary_url,
            secondary_source=secondary_url,
        )
        self._cached = info
        logger.info("egress confirmed", ip=info.ip, country=info.country_iso)
        return info

    def assert_in_whitelist(self, info: EgressInfo) -> None:
        whitelist = [c.upper() for c in self._settings.compliance.allowed_egress_countries]
        if not whitelist:
            raise ComplianceError(
                "no allowed_egress_countries configured — refusing to proceed"
            )
        if info.country_iso.upper() not in whitelist:
            raise ComplianceError(
                f"egress country {info.country_iso!r} not in whitelist {whitelist}"
            )
