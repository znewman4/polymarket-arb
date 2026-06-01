"""Polymarket CLOB API V2 order signing using py-clob-client-v2."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
from loguru import logger
from py_clob_client_v2 import (
    ApiCreds,
    ClobClient,
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
    signature_type: int = 1,
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
        signature_type=signature_type,
        funder=funder or None,
    )


def post_order_via_signer(
    signer_url: str,
    *,
    token_id: str,
    price: Decimal,
    size: Decimal,
    side: str,
    tick_size: str = "0.01",
    neg_risk: bool = False,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Post an order via the Node.js Polymarket signer microservice.

    The signer service handles EIP-712 signing for Magic/proxy wallet accounts,
    which is broken in py-clob-client-v2 for non-EOA accounts.

    Args:
        signer_url: Base URL of the signer service, e.g. "http://poly-signer:7777"
        token_id: Polymarket CLOB token ID for the outcome to buy/sell
        price: Limit price (0-1)
        size: Order size in shares
        side: "buy" or "sell"
        tick_size: Market tick size from CLOB API, default "0.01"
        neg_risk: Whether the market is a neg-risk market
        timeout: HTTP timeout in seconds

    Returns:
        API response dict from the signer service

    Raises:
        SigningNotConfigured: if signer_url is empty
        httpx.HTTPError: on network failure
        RuntimeError: if the signer returns an error response
    """
    if not signer_url:
        raise SigningNotConfigured("polymarket_signer_url is not configured")

    payload = {
        "token_id": token_id,
        "price": float(round(price, 4)),
        "size": float(round(size, 2)),
        "side": side.lower(),
        "tick_size": tick_size,
        "neg_risk": neg_risk,
    }
    try:
        response = httpx.post(
            f"{signer_url.rstrip('/')}/order",
            json=payload,
            timeout=timeout,
        )
        result = response.json()
        if response.status_code >= 400:
            raise RuntimeError(
                f"signer returned {response.status_code}: {result.get('error', result)}"
            )
        logger.debug(
            "signer order posted: token_id={} price={} size={} side={}",
            token_id, price, size, side,
        )
        return result
    except httpx.HTTPError as exc:
        logger.error("signer HTTP error for token {}: {}", token_id, exc)
        raise
