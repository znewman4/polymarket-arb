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
from ._endpoints import (
    EVENTS_PATH,
    MARKETS_PATH,
    RELATED_TAGS_PATH,
    SEARCH_PATH,
    SERIES_PATH,
    SINGLE_MARKET_PATH,
    SPORTS_PATH,
    TAGS_PATH,
    TEAMS_PATH,
)
from .parser import parse_event, parse_market


def _now_ms() -> int:
    return int(time.time() * 1000)


def _items_from_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "items", "results", "markets", "events", "tags", "series", "sports", "teams"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


class GammaClient:
    """Stateless wrapper over Gamma. Holds a reference to ``AsyncHttpClient``
    and a ``RawWriter`` so each call persists its raw page."""

    def __init__(
        self,
        *,
        gamma_host: str,
        http: AsyncHttpClient,
        raw_writer: RawWriter,
        page_size: int = 100,
    ) -> None:
        self._host = gamma_host.rstrip("/")
        self._http = http
        self._raw = raw_writer
        self._page_size = page_size

    @property
    def data_root(self):
        return self._raw._root

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._http.get_json(f"{self._host}{path}", params=params or {})

    async def _iter_paginated_raw(
        self,
        path: str,
        *,
        source: str,
        params: dict[str, Any] | None = None,
        limit: int | None = None,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        offset = 0
        page_idx = 0
        page_limit = limit or self._page_size
        base_params = {k: v for k, v in (params or {}).items() if v is not None}
        while True:
            if max_pages is not None and page_idx >= max_pages:
                break
            req_params = {"limit": page_limit, "offset": offset, **base_params}
            payload = await self._get(path, params=req_params)
            self._raw.write_json(
                source=f"gamma/{source}",
                payload={"params": req_params, "items": payload},
            )
            items = _items_from_payload(payload)
            if not items:
                break
            for item in items:
                if isinstance(item, dict):
                    yield item
                else:
                    yield {"value": item}
            page_idx += 1
            if len(items) < page_limit:
                break
            offset += page_limit

    async def _fetch_raw_list(
        self,
        path: str,
        *,
        source: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        payload = await self._get(path, params=params)
        self._raw.write_json(
            source=f"gamma/{source}",
            payload={"params": params or {}, "items": payload},
        )
        out: list[dict[str, Any]] = []
        for item in _items_from_payload(payload):
            out.append(item if isinstance(item, dict) else {"value": item})
        return out

    async def iter_raw_markets(
        self,
        *,
        active: bool | None = True,
        closed: bool | None = False,
        archived: bool | None = False,
        limit: int | None = None,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        params: dict[str, Any] = {}
        if active is not None:
            params["active"] = "true" if active else "false"
        if closed is not None:
            params["closed"] = "true" if closed else "false"
        if archived is not None:
            params["archived"] = "true" if archived else "false"
        async for row in self._iter_paginated_raw(
            MARKETS_PATH,
            source="markets",
            params=params,
            limit=limit,
            max_pages=max_pages,
        ):
            yield row

    async def iter_raw_events(
        self,
        *,
        active: bool | None = True,
        closed: bool | None = None,
        archived: bool | None = False,
        limit: int | None = None,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        params: dict[str, Any] = {}
        if active is not None:
            params["active"] = "true" if active else "false"
        if closed is not None:
            params["closed"] = "true" if closed else "false"
        if archived is not None:
            params["archived"] = "true" if archived else "false"
        async for row in self._iter_paginated_raw(
            EVENTS_PATH,
            source="events",
            params=params,
            limit=limit,
            max_pages=max_pages,
        ):
            yield row

    async def iter_search(
        self,
        query: str,
        *,
        limit: int = 100,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for row in self._iter_paginated_raw(
            SEARCH_PATH,
            source="public_search",
            params={"query": query},
            limit=limit,
            max_pages=max_pages,
        ):
            yield row

    async def iter_tags(
        self,
        *,
        limit: int = 100,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for row in self._iter_paginated_raw(
            TAGS_PATH,
            source="tags",
            limit=limit,
            max_pages=max_pages,
        ):
            yield row

    async def iter_related_tags(self, tag_id: str | int) -> list[dict[str, Any]]:
        path = RELATED_TAGS_PATH.format(id=tag_id)
        return await self._fetch_raw_list(path, source="related_tags", params={})

    async def iter_series(
        self,
        *,
        limit: int = 100,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for row in self._iter_paginated_raw(
            SERIES_PATH,
            source="series",
            limit=limit,
            max_pages=max_pages,
        ):
            yield row

    async def iter_sports(self) -> list[dict[str, Any]]:
        return await self._fetch_raw_list(SPORTS_PATH, source="sports", params={})

    async def iter_teams(
        self,
        *,
        sport_id: str | int | None = None,
        limit: int = 100,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for row in self._iter_paginated_raw(
            TEAMS_PATH,
            source="teams",
            params={"sport_id": sport_id},
            limit=limit,
            max_pages=max_pages,
        ):
            yield row

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
