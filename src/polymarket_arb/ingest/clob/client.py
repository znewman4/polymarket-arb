"""Read-only public Polymarket CLOB REST client."""

from __future__ import annotations

from typing import Any

from ...http.client import AsyncHttpClient
from ...storage.parquet.raw_writer import RawWriter
from ._endpoints import (
    batch_prices_history_url,
    book_url,
    books_url,
    midpoint_url,
    price_url,
    prices_history_url,
    spread_url,
    trades_url,
)


class ClobClient:
    def __init__(
        self,
        *,
        clob_host: str,
        http: AsyncHttpClient,
        raw_writer: RawWriter | None = None,
    ) -> None:
        self._host = clob_host.rstrip("/")
        self._http = http
        self._raw_writer = raw_writer

    async def fetch_book(self, token_id: str) -> dict[str, Any]:
        payload = await self._http.get_json(book_url(self._host, token_id))
        self._write_raw("book", {"token_id": token_id, "payload": payload})
        return payload

    async def fetch_books(self, token_ids: list[str]) -> Any:
        body = [{"token_id": token_id} for token_id in token_ids]
        payload = await self._http.post_json(books_url(self._host), json=body)
        self._write_raw("books", {"token_ids": token_ids, "payload": payload})
        return payload

    async def fetch_price(self, token_id: str, side: str) -> Any:
        payload = await self._http.get_json(price_url(self._host, token_id, side.lower()))
        self._write_raw("price", {"token_id": token_id, "side": side, "payload": payload})
        return payload

    async def fetch_midpoint(self, token_id: str) -> Any:
        payload = await self._http.get_json(midpoint_url(self._host, token_id))
        self._write_raw("midpoint", {"token_id": token_id, "payload": payload})
        return payload

    async def fetch_spread(self, token_id: str) -> Any:
        payload = await self._http.get_json(spread_url(self._host, token_id))
        self._write_raw("spread", {"token_id": token_id, "payload": payload})
        return payload

    async def fetch_prices_history(
        self,
        token_id: str,
        *,
        interval: str = "1d",
        start_ts_s: int | None = None,
        end_ts_s: int | None = None,
        fidelity_minutes: int | None = None,
    ) -> dict[str, Any]:
        url = prices_history_url(
            self._host,
            token_id,
            interval=interval,
            start_ts_s=start_ts_s,
            end_ts_s=end_ts_s,
            fidelity_minutes=fidelity_minutes,
        )
        payload = await self._http.get_json(url)
        self._write_raw("prices-history", {"token_id": token_id, "interval": interval, "payload": payload})
        return payload

    async def fetch_batch_prices_history(
        self,
        token_ids: list[str],
        *,
        interval: str = "1d",
        start_ts_s: int | None = None,
        end_ts_s: int | None = None,
        fidelity_minutes: int | None = None,
    ) -> list[dict[str, Any]]:
        """POST /batch-prices-history for up to 20 token IDs at once.

        Returns a list of per-token payloads in the same order as ``token_ids``.
        Raises ``httpx.HTTPStatusError`` on 4xx/5xx — callers should fall back
        to per-token GET /prices-history.
        """
        body: dict[str, Any] = {"markets": token_ids, "interval": interval}
        if start_ts_s is not None:
            body["start_ts"] = start_ts_s
        if end_ts_s is not None:
            body["end_ts"] = end_ts_s
        if fidelity_minutes is not None:
            body["fidelity"] = fidelity_minutes
        payload = await self._http.post_json(batch_prices_history_url(self._host), json=body)
        self._write_raw(
            "batch-prices-history",
            {"token_ids": token_ids, "interval": interval, "payload": payload},
        )
        return _normalise_batch_prices_history(payload, token_ids)

    async def fetch_trades(self, token_id: str) -> Any:
        """Fetch public trade history for a token.

        Best-effort: returns None if endpoint is unavailable (401/403/404).
        """
        import httpx
        try:
            payload = await self._http.get_json(trades_url(self._host, token_id))
            self._write_raw("trades", {"token_id": token_id, "payload": payload})
            return payload
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403, 404):
                return None
            raise

    def _write_raw(self, kind: str, payload: Any) -> None:
        if self._raw_writer is not None:
            self._raw_writer.write_json(f"clob/{kind}", payload)


def _normalise_batch_prices_history(payload: Any, token_ids: list[str]) -> list[dict[str, Any]]:
    """Normalize old and current batch history response shapes.

    Current CLOB docs return ``{"history": {"token": [points...]}}``. Older
    tests and clients used a list of per-token ``{"history": [...]}`` payloads.
    Keep both shapes so callers can parse one token at a time.
    """
    if isinstance(payload, list):
        return [p if isinstance(p, dict) else {"history": []} for p in payload]
    if not isinstance(payload, dict):
        return []
    history = payload.get("history")
    if isinstance(history, dict):
        return [
            {"history": points if isinstance(points, list) else []}
            for token_id in token_ids
            for points in [history.get(token_id)]
        ]
    if isinstance(history, list) and len(token_ids) == 1:
        return [{"history": history}]
    return []
