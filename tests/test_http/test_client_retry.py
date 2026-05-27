from __future__ import annotations

import pytest
import respx
from httpx import Response

from polymarket_arb.http.client import AsyncHttpClient, HttpError, TransientError


@pytest.mark.asyncio
async def test_transient_then_success(settings):
    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        route = router.get("https://gamma-api.example/markets").mock(
            side_effect=[
                Response(503),
                Response(200, json=[{"id": "m1"}]),
            ]
        )
        result = await http.get_json("https://gamma-api.example/markets")
        assert result == [{"id": "m1"}]
        assert route.call_count == 2


@pytest.mark.asyncio
async def test_non_transient_no_retry(settings):
    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        route = router.get("https://gamma-api.example/markets").mock(
            return_value=Response(404, text="market not found")
        )
        with pytest.raises(HttpError) as exc_info:
            await http.get_json("https://gamma-api.example/markets")
        assert exc_info.value.response is not None
        assert exc_info.value.response.text == "market not found"
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_retry_budget_exhausted(settings):
    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        route = router.get("https://gamma-api.example/markets").mock(
            return_value=Response(503)
        )
        with pytest.raises(TransientError):
            await http.get_json("https://gamma-api.example/markets")
        # max_retries=2 in test settings
        assert route.call_count == 2
