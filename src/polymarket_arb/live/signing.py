"""Polymarket CLOB API V2 order signing using py-clob-client-v2."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from loguru import logger
from py_clob_client_v2 import (
    ApiCreds,
    ClobClient,
    OrderArgs,
    OrderType,
    PartialCreateOrderOptions,
    Side,
)


class SigningNotConfigured(RuntimeError):
    """Raised when live order attempted without credentials."""


def build_clob_client(
    *,
    private_key_hex: str,
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    funder: str = "",
    chain_id: int = 137,
    host: str = "https://clob.polymarket.com",
) -> ClobClient:
    """Build an authenticated ClobClient for the Polymarket CLOB V2 API."""
    creds = ApiCreds(
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
    ) if api_key else None
    return ClobClient(
        host=host,
        chain_id=chain_id,
        key=private_key_hex,
        creds=creds,
        signature_type=1,
        funder=funder or None,
    )


def create_and_post_order(
    client: ClobClient,
    *,
    token_id: str,
    price: Decimal,
    size: Decimal,
    side: str,
    tick_size: str = "0.01",
    neg_risk: bool = False,
) -> dict[str, Any]:
    """Create and post a GTC limit order to the Polymarket CLOB V2.

    Returns the API response dict.

    Raises:
        SigningNotConfigured: if client is None.
        ValueError: if side is not 'buy' or 'sell'.
    """
    if client is None:
        raise SigningNotConfigured("ClobClient not initialised.")
    if side.lower() not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

    order_side = Side.BUY if side.lower() == "buy" else Side.SELL
    order_args = OrderArgs(
        token_id=token_id,
        price=float(round(price, 4)),
        size=float(round(size, 2)),
        side=order_side,
    )
    options = PartialCreateOrderOptions(
        tick_size=tick_size,
        neg_risk=neg_risk,
    )
    resp = client.create_and_post_order(
        order_args=order_args,
        options=options,
        order_type=OrderType.GTC,
    )
    logger.debug(
        "v2 order posted: token_id={} price={} size={} side={}",
        token_id, price, size, side,
    )
    return resp if isinstance(resp, dict) else {"raw": resp}
