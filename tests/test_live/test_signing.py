"""Tests for the Polymarket signer microservice wrapper."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
import respx

from polymarket_arb.live.signing import SigningNotConfigured, post_order_via_signer

SIGNER_URL = "http://poly-signer:7777"


@respx.mock
def test_post_order_via_signer_success() -> None:
    """Successful order returns the signer response dict."""
    respx.post(f"{SIGNER_URL}/order").mock(
        return_value=httpx.Response(200, json={"order_id": "abc123", "status": "live"})
    )

    resp = post_order_via_signer(
        SIGNER_URL,
        token_id="tok123",
        price=Decimal("0.45"),
        size=Decimal("5.0"),
        side="buy",
    )

    assert resp["order_id"] == "abc123"


@respx.mock
def test_post_order_via_signer_400_raises() -> None:
    """Signer 400 response raises RuntimeError with error message."""
    respx.post(f"{SIGNER_URL}/order").mock(
        return_value=httpx.Response(400, json={"error": "maker address not allowed"})
    )

    with pytest.raises(RuntimeError, match="maker address not allowed"):
        post_order_via_signer(
            SIGNER_URL,
            token_id="tok123",
            price=Decimal("0.45"),
            size=Decimal("5.0"),
            side="buy",
        )


def test_post_order_via_signer_no_url_raises() -> None:
    """Empty signer_url raises SigningNotConfigured."""
    with pytest.raises(SigningNotConfigured):
        post_order_via_signer(
            "",
            token_id="tok123",
            price=Decimal("0.45"),
            size=Decimal("5.0"),
            side="buy",
        )


@respx.mock
def test_post_order_via_signer_payload_format() -> None:
    """Verify the payload sent to the signer has correct field names and types."""
    route = respx.post(f"{SIGNER_URL}/order").mock(
        return_value=httpx.Response(200, json={"order_id": "xyz"})
    )

    post_order_via_signer(
        SIGNER_URL,
        token_id="tok456",
        price=Decimal("0.6312"),
        size=Decimal("5.5"),
        side="sell",
        tick_size="0.01",
        neg_risk=True,
    )

    sent = route.calls[0].request
    body = json.loads(sent.content)
    assert body["token_id"] == "tok456"
    assert body["price"] == 0.6312
    assert body["size"] == 5.5
    assert body["side"] == "sell"
    assert body["tick_size"] == "0.01"
    assert body["neg_risk"] is True


@respx.mock
def test_post_order_via_signer_network_error_propagates() -> None:
    """Network failure raises httpx.HTTPError."""
    respx.post(f"{SIGNER_URL}/order").mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(httpx.HTTPError):
        post_order_via_signer(
            SIGNER_URL,
            token_id="tok123",
            price=Decimal("0.45"),
            size=Decimal("5.0"),
            side="buy",
        )
