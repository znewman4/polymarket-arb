"""Polymarket CLOB API L2 order signing using py-clob-client."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from loguru import logger
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs
from py_clob_client.order_builder.constants import BUY, SELL


class SigningNotConfigured(RuntimeError):
    """Raised when live order attempted without credentials."""


def build_clob_client(
    *,
    private_key_hex: str,
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    chain_id: int = 137,
    host: str = "https://clob.polymarket.com",
) -> ClobClient:
    """Build an authenticated ClobClient for the Polymarket CLOB API."""
    creds = ApiCreds(
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
    )
    return ClobClient(
        host=host,
        key=private_key_hex,
        chain_id=chain_id,
        creds=creds,
        signature_type=0,
    )


def sign_and_build_order(
    client: ClobClient,
    *,
    token_id: str,
    price: Decimal,
    size: Decimal,
    side: str,
    neg_risk: bool = False,
) -> Any:
    """Sign an order intent and return the signed order payload.

    Raises:
        SigningNotConfigured: if client is None.
        ValueError: if side is not "buy" or "sell".
    """
    if client is None:
        raise SigningNotConfigured("ClobClient not initialised.")
    if side.lower() not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")

    order_kwargs = {
        "token_id": token_id,
        "price": float(round(price, 4)),
        "size": float(round(size, 2)),
        "side": BUY if side.lower() == "buy" else SELL,
        "neg_risk": neg_risk,
    }
    try:
        order_args = OrderArgs(**order_kwargs)
    except TypeError as exc:
        if "neg_risk" not in str(exc):
            raise
        order_kwargs.pop("neg_risk")
        order_args = OrderArgs(**order_kwargs)
        order_args.neg_risk = neg_risk
    signed = client.create_order(order_args)
    logger.debug(
        "signed order: token_id={} price={} size={}",
        token_id, price, size,
    )
    return signed


def post_order(client: ClobClient, signed_order: Any) -> dict[str, Any]:
    """Submit a signed order to the CLOB. Returns the API response dict."""
    resp = client.post_order(signed_order)
    return resp if isinstance(resp, dict) else {"raw": resp}
