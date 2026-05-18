"""Universe discovery tests with mocked public Gamma endpoints."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from polymarket_arb.http.client import AsyncHttpClient
from polymarket_arb.ingest.gamma.client import GammaClient
from polymarket_arb.ingest.market_universe_discovery import run_universe_discovery
from polymarket_arb.storage.parquet.raw_writer import RawWriter


@pytest.mark.asyncio
async def test_gamma_client_iter_tags_paginates(settings, tmp_data_root):
    settings.gamma_host = "https://gamma.example"
    async with AsyncHttpClient(settings.http) as http, respx.mock(assert_all_called=False) as router:
        offsets: list[int] = []

        def _tags(request):
            offsets.append(int(request.url.params["offset"]))
            if offsets[-1] == 0:
                return Response(200, json=[{"id": "t1", "label": "Sports"}])
            return Response(200, json=[{"id": "t2", "label": "Politics"}])

        router.get("https://gamma.example/tags").mock(side_effect=_tags)
        client = GammaClient(
            gamma_host=settings.gamma_host,
            http=http,
            raw_writer=RawWriter(tmp_data_root),
        )
        rows = [r async for r in client.iter_tags(limit=1, max_pages=2)]

    assert [r["id"] for r in rows] == ["t1", "t2"]
    assert offsets == [0, 1]


@pytest.mark.asyncio
async def test_universe_discovery_writes_manifest_and_stats(settings, tmp_data_root):
    settings.gamma_host = "https://gamma.example"
    async with AsyncHttpClient(settings.http) as http, respx.mock(assert_all_called=False) as router:
        router.get("https://gamma.example/markets").mock(
            return_value=Response(200, json=[{"id": "m1", "question": "Will A win the cup?"}])
        )
        router.get("https://gamma.example/events").mock(
            return_value=Response(200, json=[{"id": "e1", "title": "Cup", "markets": [{"id": "m2", "question": "Will B win the cup?"}]}])
        )
        router.get("https://gamma.example/tags").mock(
            return_value=Response(200, json=[{"id": "t1", "label": "Sports"}])
        )
        router.get("https://gamma.example/tags/t1/related-tags").mock(
            return_value=Response(200, json=[{"id": "t2", "label": "Football"}])
        )
        router.get("https://gamma.example/series").mock(
            return_value=Response(200, json=[{"id": "ser1", "title": "Championships"}])
        )
        router.get("https://gamma.example/sports").mock(
            return_value=Response(200, json=[{"id": "s1", "name": "Soccer"}])
        )
        router.get("https://gamma.example/teams").mock(
            return_value=Response(200, json=[{"id": "team1", "name": "A FC"}])
        )
        client = GammaClient(
            gamma_host=settings.gamma_host,
            http=http,
            raw_writer=RawWriter(tmp_data_root),
        )
        result = await run_universe_discovery(
            client,
            run_id="disc",
            active=False,
            closed=False,
            max_pages=1,
        )

    assert result.manifest_path.exists()
    assert result.stats_path.exists()
    stats = json.loads(result.stats_path.read_text(encoding="utf-8"))
    for source, count in stats["counts"].items():
        path = result.output_dir / f"{source}.jsonl"
        line_count = len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])
        assert count == line_count
    assert result.counts["markets"] == 2
    assert result.counts["events"] == 1
    assert result.counts["related_tags"] == 1
