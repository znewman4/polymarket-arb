"""Parse CLOB orderbook payloads into storage DTOs."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from ...storage.base import BestQuote, OrderbookLevel, OrderbookSnapshot


class ClobParseError(ValueError):
    """Raised for malformed CLOB payloads."""


def parse_orderbook(payload: dict[str, Any], *, token_id_hint: str | None = None) -> OrderbookSnapshot:
    if not isinstance(payload, dict):
        raise ClobParseError("orderbook payload must be a dict")
    token_id = str(payload.get("asset_id") or payload.get("token_id") or token_id_hint or "")
    if not token_id:
        raise ClobParseError("orderbook missing token id")
    bids = [_level(x) for x in payload.get("bids") or []]
    asks = [_level(x) for x in payload.get("asks") or []]
    return OrderbookSnapshot(
        token_id=token_id,
        condition_id=_optional_str(payload.get("market") or payload.get("condition_id")),
        market_slug=_optional_str(payload.get("market_slug")),
        timestamp_ms=_timestamp_ms(payload.get("timestamp")),
        bids=bids,
        asks=asks,
        book_hash=_optional_str(payload.get("hash")),
        source="rest",
        schema_version=1,
        ingested_ts_ms=_now_ms(),
    )


def best_quote_from_book(book: OrderbookSnapshot) -> BestQuote:
    best_bid = max(book.bids, key=lambda x: x.price, default=None)
    best_ask = min(book.asks, key=lambda x: x.price, default=None)
    bid_price = best_bid.price if best_bid else None
    ask_price = best_ask.price if best_ask else None
    midpoint = None
    spread = None
    if bid_price is not None and ask_price is not None:
        midpoint = (bid_price + ask_price) / Decimal("2")
        spread = ask_price - bid_price
    return BestQuote(
        token_id=book.token_id,
        timestamp_ms=book.timestamp_ms,
        best_bid=bid_price,
        best_bid_size=best_bid.size if best_bid else None,
        best_ask=ask_price,
        best_ask_size=best_ask.size if best_ask else None,
        midpoint=midpoint,
        spread=spread,
        schema_version=1,
        ingested_ts_ms=_now_ms(),
    )


def _level(payload: Any) -> OrderbookLevel:
    if not isinstance(payload, dict):
        raise ClobParseError("orderbook level must be a dict")
    try:
        return OrderbookLevel(
            price=Decimal(str(payload["price"])),
            size=Decimal(str(payload["size"])),
        )
    except (KeyError, InvalidOperation) as exc:
        raise ClobParseError(f"malformed orderbook level: {payload!r}") from exc


def _timestamp_ms(value: Any) -> int:
    if value is None:
        return _now_ms()
    if isinstance(value, int | float):
        return int(value if value > 10_000_000_000 else value * 1000)
    text = str(value)
    if text.isdigit():
        return _timestamp_ms(int(text))
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _now_ms()
    return int(dt.timestamp() * 1000)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)
