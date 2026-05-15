from __future__ import annotations

import respx
from httpx import Response

from polymarket_arb.http.client import AsyncHttpClient
from polymarket_arb.ingest.clob.client import ClobClient
from polymarket_arb.storage.parquet.raw_writer import RawWriter


async def test_fetch_book_writes_raw(settings, tmp_data_root):
    settings.clob_host = "https://clob.example"
    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        router.get("https://clob.example/book").mock(
            return_value=Response(200, json={"asset_id": "tok", "bids": [], "asks": []})
        )
        client = ClobClient(
            clob_host=settings.clob_host,
            http=http,
            raw_writer=RawWriter(tmp_data_root),
        )
        payload = await client.fetch_book("tok")
    assert payload["asset_id"] == "tok"
    assert list((tmp_data_root / "raw" / "clob" / "book").rglob("*.json"))


async def test_fetch_batch_prices_history_uses_current_body_shape(settings, tmp_data_root):
    settings.clob_host = "https://clob.example"
    token_id = "123456789"

    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        route = router.post("https://clob.example/batch-prices-history").mock(
            return_value=Response(
                200,
                json={"history": {token_id: [{"t": 1700000000, "p": "0.42"}]}},
            )
        )
        client = ClobClient(
            clob_host=settings.clob_host,
            http=http,
            raw_writer=RawWriter(tmp_data_root),
        )
        payloads = await client.fetch_batch_prices_history(
            [token_id],
            interval="max",
            fidelity_minutes=720,
        )

    assert route.calls.last.request.content == (
        b'{"markets":["123456789"],"interval":"max","fidelity":720}'
    )
    assert payloads == [{"history": [{"t": 1700000000, "p": "0.42"}]}]
