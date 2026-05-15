"""Mocked GammaClient pagination + raw-lake persistence tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from httpx import Response

from polymarket_arb.http.client import AsyncHttpClient
from polymarket_arb.ingest.gamma.client import GammaClient
from polymarket_arb.storage.parquet.raw_writer import RawWriter


def _fixture(name: str) -> list[dict]:
    p = Path(__file__).resolve().parents[1] / "fixtures" / "gamma" / name
    return json.loads(p.read_text())


@pytest.mark.asyncio
async def test_iter_markets_paginates_until_short_page(settings, tmp_data_root):
    page1 = _fixture("markets_synthetic.json")  # 3 valid markets
    settings.gamma_host = "https://gamma.example"

    async with AsyncHttpClient(settings.http) as http, respx.mock(
        assert_all_called=False
    ) as router:
        # First call: full page (3 items, page_size=3) → expect a second call.
        # Second call: empty list → loop exits.
        offsets_seen: list[int] = []

        def _handler(request):
            offsets_seen.append(int(request.url.params["offset"]))
            if offsets_seen[-1] == 0:
                return Response(200, json=page1)
            return Response(200, json=[])

        router.get("https://gamma.example/markets").mock(side_effect=_handler)

        rw = RawWriter(tmp_data_root)
        client = GammaClient(gamma_host="https://gamma.example", http=http,
                             raw_writer=rw, page_size=3)
        rows = [r async for r in client.iter_markets()]

    assert len(rows) == 3
    assert offsets_seen == [0, 3]  # second page requested then bailed on empty
    # Raw lake has both pages dumped
    raw_files = list((tmp_data_root / "raw" / "gamma" / "markets").rglob("*.json"))
    assert len(raw_files) == 2


@pytest.mark.asyncio
async def test_iter_markets_drops_invalid_records(settings, tmp_data_root):
    invalid = _fixture("markets_invalid.json")  # 4 invalid markets
    settings.gamma_host = "https://gamma.example"

    async with AsyncHttpClient(settings.http) as http, respx.mock(
        assert_all_called=False
    ) as router:
        offsets: list[int] = []

        def _h(request):
            offsets.append(int(request.url.params["offset"]))
            if offsets[-1] == 0:
                return Response(200, json=invalid)
            return Response(200, json=[])

        router.get("https://gamma.example/markets").mock(side_effect=_h)

        rw = RawWriter(tmp_data_root)
        client = GammaClient(gamma_host="https://gamma.example", http=http,
                             raw_writer=rw, page_size=4)
        rows = [r async for r in client.iter_markets()]
    assert rows == []  # all four invalid rows dropped
    raw_files = list((tmp_data_root / "raw" / "gamma" / "markets").rglob("*.json"))
    assert len(raw_files) >= 1  # raw page persisted regardless


@pytest.mark.asyncio
async def test_iter_markets_max_pages_caps_iteration(settings, tmp_data_root):
    page = _fixture("markets_synthetic.json")
    settings.gamma_host = "https://gamma.example"

    async with AsyncHttpClient(settings.http) as http, respx.mock(
        assert_all_called=False
    ) as router:
        # Always return a full page so the loop is bounded only by max_pages.
        router.get("https://gamma.example/markets").mock(
            return_value=Response(200, json=page)
        )
        rw = RawWriter(tmp_data_root)
        client = GammaClient(gamma_host="https://gamma.example", http=http,
                             raw_writer=rw, page_size=3)
        rows = [r async for r in client.iter_markets(max_pages=2)]
    assert len(rows) == 6  # 2 pages x 3 valid rows


@pytest.mark.asyncio
async def test_fetch_market_single(settings, tmp_data_root):
    [target] = [m for m in _fixture("markets_synthetic.json")
                if m["slug"] == "synthetic-stringified"]
    settings.gamma_host = "https://gamma.example"

    async with AsyncHttpClient(settings.http) as http, respx.mock() as router:
        router.get("https://gamma.example/markets/1001").mock(
            return_value=Response(200, json=target)
        )
        rw = RawWriter(tmp_data_root)
        client = GammaClient(gamma_host="https://gamma.example", http=http,
                             raw_writer=rw)
        row = await client.fetch_market("1001")

    assert row is not None
    assert row.id == "1001"
    assert row.slug == "synthetic-stringified"
