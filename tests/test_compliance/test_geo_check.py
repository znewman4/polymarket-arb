from __future__ import annotations

import pytest
import respx
from httpx import Response

from polymarket_arb.compliance.geo_check import ComplianceError, GeoChecker
from polymarket_arb.http.client import AsyncHttpClient


@pytest.mark.asyncio
async def test_pass_through_whitelist(settings):
    async with AsyncHttpClient(settings.http) as http, respx.mock(assert_all_called=True) as router:
        router.get("https://ip.example/primary").mock(
            return_value=Response(200, json={"ip": "203.0.113.42"})
        )
        router.get("https://ip.example/secondary").mock(
            return_value=Response(200, json={"ip": "203.0.113.42", "country_iso": "DE"})
        )
        gc = GeoChecker(settings, http)
        info = await gc.fetch()
        assert info.ip == "203.0.113.42"
        assert info.country_iso == "DE"
        gc.assert_in_whitelist(info)


@pytest.mark.asyncio
async def test_fail_outside_whitelist(settings):
    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        router.get("https://ip.example/primary").mock(
            return_value=Response(200, json={"ip": "203.0.113.42"})
        )
        router.get("https://ip.example/secondary").mock(
            return_value=Response(200, json={"ip": "203.0.113.42", "country_iso": "GB"})
        )
        gc = GeoChecker(settings, http)
        info = await gc.fetch()
        with pytest.raises(ComplianceError):
            gc.assert_in_whitelist(info)


@pytest.mark.asyncio
async def test_provider_unreachable(settings):
    # Primary fails persistently → secondary is never reached.
    async with AsyncHttpClient(settings.http) as http, \
            respx.mock(assert_all_called=False) as router:
        router.get("https://ip.example/primary").mock(return_value=Response(500))
        router.get("https://ip.example/secondary").mock(
            return_value=Response(200, json={"ip": "x", "country_iso": "DE"})
        )
        gc = GeoChecker(settings, http)
        with pytest.raises(ComplianceError):
            await gc.fetch()


@pytest.mark.asyncio
async def test_cache_within_ttl(settings):
    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        primary = router.get("https://ip.example/primary").mock(
            return_value=Response(200, json={"ip": "203.0.113.99"})
        )
        secondary = router.get("https://ip.example/secondary").mock(
            return_value=Response(200, json={"ip": "203.0.113.99", "country_iso": "NL"})
        )
        gc = GeoChecker(settings, http)
        await gc.fetch()
        await gc.fetch()
        assert primary.call_count == 1
        assert secondary.call_count == 1
