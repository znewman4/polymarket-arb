"""CLOB public endpoint URL helpers."""

from __future__ import annotations

from urllib.parse import urlencode


def book_url(base: str, token_id: str) -> str:
    return f"{base.rstrip('/')}/book?{urlencode({'token_id': token_id})}"


def books_url(base: str) -> str:
    return f"{base.rstrip('/')}/books"


def price_url(base: str, token_id: str, side: str) -> str:
    return f"{base.rstrip('/')}/price?{urlencode({'token_id': token_id, 'side': side})}"


def midpoint_url(base: str, token_id: str) -> str:
    return f"{base.rstrip('/')}/midpoint?{urlencode({'token_id': token_id})}"


def spread_url(base: str, token_id: str) -> str:
    return f"{base.rstrip('/')}/spread?{urlencode({'token_id': token_id})}"


def tick_size_url(base: str, token_id: str) -> str:
    return f"{base.rstrip('/')}/tick-size?{urlencode({'token_id': token_id})}"


def last_trade_price_url(base: str, token_id: str) -> str:
    return f"{base.rstrip('/')}/last-trade-price?{urlencode({'token_id': token_id})}"


def prices_history_url(
    base: str,
    market_token_id: str,
    *,
    interval: str = "1d",
    start_ts_s: int | None = None,
    end_ts_s: int | None = None,
    fidelity_minutes: int | None = None,
) -> str:
    params: dict = {"market": market_token_id, "interval": interval}
    if start_ts_s is not None:
        params["startTs"] = start_ts_s
    if end_ts_s is not None:
        params["endTs"] = end_ts_s
    if fidelity_minutes is not None:
        params["fidelity"] = fidelity_minutes
    return f"{base.rstrip('/')}/prices-history?{urlencode(params)}"


def batch_prices_history_url(base: str) -> str:
    return f"{base.rstrip('/')}/batch-prices-history"


def trades_url(base: str, token_id: str) -> str:
    return f"{base.rstrip('/')}/trades?{urlencode({'market': token_id})}"
