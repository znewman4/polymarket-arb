"""Async Gamma client — pagination + raw-lake persistence.

The client returns parsed ``MarketRow`` / ``EventRow`` objects; the raw
JSON page is also written verbatim to ``data/raw/gamma/...`` *before*
parsing so we can re-derive normalised tables if a Pydantic schema changes.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger

from ...http.client import AsyncHttpClient
from ...storage.base import EventRow, MarketRow
from ...storage.parquet.raw_writer import RawWriter
from ._endpoints import EVENTS_PATH, MARKETS_PATH, SINGLE_MARKET_PATH
from .parser import parse_event, parse_market


def _now_ms() -> int:
    return int(time.time() * 1000)


class GammaClient:
    """Stateless wrapper over Gamma. Holds a reference to ``AsyncHttpClient``
    and a ``RawWriter`` so each call persists its raw page."""

    def __init__(
        self,
        *,
        gamma_host: str,
        http: AsyncHttpClient,
        raw_writer: RawWriter,
        page_size: int = 500,
    ) -> None:
        self._host = gamma_host.rstrip("/")
        self._http = http
        self._raw = raw_writer
        self._page_size = page_size

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._http.get_json(f"{self._host}{path}", params=params or {})

    async def iter_markets(
        self,
        *,
        active: bool | None = True,
        closed: bool | None = False,
        archived: bool | None = False,
        max_pages: int | None = None,
    ) -> AsyncIterator[MarketRow]:
        """Yield parsed markets across as many pages as needed (or up to
        ``max_pages``). Drops invalid records (logged) but persists every
        page raw."""

        offset = 0
        page_idx = 0
        while True:
            if max_pages is not None and page_idx >= max_pages:
                break
            params: dict[str, Any] = {"limit": self._page_size, "offset": offset}
            if active is not None:
                params["active"] = "true" if active else "false"
            if closed is not None:
                params["closed"] = "true" if closed else "false"
            if archived is not None:
                params["archived"] = "true" if archived else "false"

            payload = await self._get(MARKETS_PATH, params=params)
            self._raw.write_json(
                source="gamma/markets",
                payload={"params": params, "items": payload},
            )

            if not isinstance(payload, list) or not payload:
                logger.info("gamma /markets exhausted", offset=offset, returned=0)
                break

            ts = _now_ms()
            yielded = 0
            for raw in payload:
                row = parse_market(raw, ingested_ts_ms=ts)
                if row is not None:
                    yielded += 1
                    yield row
            logger.debug("gamma /markets page", offset=offset,
                         received=len(payload), yielded=yielded)

            if len(payload) < self._page_size:
                break
            offset += self._page_size
            page_idx += 1

    async def iter_events(
        self,
        *,
        active: bool | None = True,
        closed: bool | None = None,
        archived: bool | None = False,
        max_pages: int | None = None,
    ) -> AsyncIterator[EventRow]:
        offset = 0
        page_idx = 0
        while True:
            if max_pages is not None and page_idx >= max_pages:
                break
            params: dict[str, Any] = {"limit": self._page_size, "offset": offset}
            if active is not None:
                params["active"] = "true" if active else "false"
            if closed is not None:
                params["closed"] = "true" if closed else "false"
            if archived is not None:
                params["archived"] = "true" if archived else "false"

            payload = await self._get(EVENTS_PATH, params=params)
            self._raw.write_json(
                source="gamma/events",
                payload={"params": params, "items": payload},
            )

            if not isinstance(payload, list) or not payload:
                break

            ts = _now_ms()
            for raw in payload:
                row = parse_event(raw, ingested_ts_ms=ts)
                if row is not None:
                    yield row

            if len(payload) < self._page_size:
                break
            offset += self._page_size
            page_idx += 1

    async def fetch_market(self, market_id: str) -> MarketRow | None:
        """Single-market fetch (used by ``gamma show-market``)."""

        path = SINGLE_MARKET_PATH.format(market_id=market_id)
        payload = await self._get(path)
        if not isinstance(payload, dict):
            return None
        self._raw.write_json(source=f"gamma/markets/{market_id}", payload=payload)
        return parse_market(payload, ingested_ts_ms=_now_ms())
