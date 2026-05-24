"""Async Limitless Exchange client — pagination + raw-lake persistence.

Fetches binary markets from the public /markets/active endpoint (no auth
required). Raw JSON pages are written to data/raw/limitless/markets/... before
parsing, matching the same pattern used by GammaClient.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger

from ...http.client import AsyncHttpClient
from ...limitless.models import LimitlessMarketEntry
from ...storage.parquet.raw_writer import RawWriter
from ._endpoints import ACTIVE_MARKETS_PATH
from .parser import parse_limitless_market


def _now_ms() -> int:
    return int(time.time() * 1000)


class LimitlessClient:
    """Stateless wrapper over the Limitless public API.

    Holds a reference to AsyncHttpClient and a RawWriter so each call
    persists its raw page before parsing.
    """

    def __init__(
        self,
        *,
        limitless_host: str,
        http: AsyncHttpClient,
        raw_writer: RawWriter,
        page_size: int = 25,  # Limitless hard cap is 25
    ) -> None:
        self._host = limitless_host.rstrip("/")
        self._http = http
        self._raw = raw_writer
        self._page_size = page_size

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._http.get_json(f"{self._host}{path}", params=params or {})

    async def iter_raw_markets(
        self,
        *,
        trade_type: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield raw market dicts, filtering to marketType=single (binary only).

        trade_type: "amm" | "clob" | None (all)
        """
        page = 1  # Limitless pages are 1-indexed
        while True:
            params: dict[str, Any] = {"page": page, "limit": self._page_size}
            if trade_type and trade_type != "all":
                params["tradeType"] = trade_type

            payload = await self._get(ACTIVE_MARKETS_PATH, params=params)
            self._raw.write_json(
                source="limitless/markets",
                payload={"params": params, "items": payload},
            )

            if isinstance(payload, dict):
                items = payload.get("data", [])
            elif isinstance(payload, list):
                items = payload
            else:
                items = []

            if not items:
                logger.info("limitless /markets/active exhausted at page {}", page)
                break

            for item in items:
                if isinstance(item, dict) and item.get("marketType", "single") == "single":
                    yield item

            if len(items) < self._page_size:
                break
            page += 1

    async def fetch_all_markets(
        self,
        *,
        trade_type: str | None = None,
    ) -> list[LimitlessMarketEntry]:
        """Fetch all active binary markets, parse, and return as a list."""
        results: list[LimitlessMarketEntry] = []
        ts = _now_ms()
        _ = ts  # reserved for future ingested_ts_ms use
        async for raw in self.iter_raw_markets(trade_type=trade_type):
            entry = parse_limitless_market(raw)
            if entry is not None:
                results.append(entry)
        logger.info("limitless: fetched {} usable binary markets", len(results))
        return results
