"""Tests for the Polymarket CLOB signing wrapper."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from py_clob_client.order_builder.constants import SELL

from polymarket_arb.live.signing import (
    SigningNotConfigured,
    build_clob_client,
    post_order,
    sign_and_build_order,
)


def test_build_clob_client_constructs_with_dummy_credentials() -> None:
    """build_clob_client should construct without error given dummy creds.

    The dummy hex key is a valid 32-byte secp256k1 scalar, so the underlying
    eth-account loader accepts it; no network call is made at construction.
    """
    client = build_clob_client(
        private_key_hex=(
            "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
        ),
        api_key="dummy-api-key",
        api_secret="dummy-secret",
        api_passphrase="dummy-passphrase",
    )
    assert client is not None


def test_sign_and_build_order_requires_client() -> None:
    with pytest.raises(SigningNotConfigured):
        sign_and_build_order(
            None,
            token_id="tok",
            price=Decimal("0.5"),
            size=Decimal("10"),
            side="buy",
        )


def test_sign_and_build_order_passes_through_create_order_result() -> None:
    fake_signed = {"signature": "0xdead", "salt": "1"}
    client = MagicMock()
    client.create_order.return_value = fake_signed
    result = sign_and_build_order(
        client,
        token_id="tok-a",
        price=Decimal("0.5234"),
        size=Decimal("12.5"),
        side="buy",
    )
    assert result is fake_signed
    client.create_order.assert_called_once()
    (order_args,), _ = client.create_order.call_args
    assert order_args.token_id == "tok-a"
    assert order_args.price == 0.5234
    assert order_args.size == 12.5


def test_sign_and_build_order_sell_uses_sell_constant() -> None:
    fake_signed = {"signature": "0xbeef", "salt": "2"}
    client = MagicMock()
    client.create_order.return_value = fake_signed

    result = sign_and_build_order(
        client,
        token_id="tok",
        price=Decimal("0.50"),
        size=Decimal("10"),
        side="sell",
    )

    assert result is fake_signed
    (order_args,), _ = client.create_order.call_args
    assert order_args.side == SELL


def test_sign_and_build_order_invalid_side_raises() -> None:
    client = MagicMock()
    with pytest.raises(ValueError, match="side must be 'buy' or 'sell'"):
        sign_and_build_order(
            client,
            token_id="tok",
            price=Decimal("0.5"),
            size=Decimal("10"),
            side="short",
        )


def test_post_order_returns_response_dict() -> None:
    client = MagicMock()
    client.post_order.return_value = {"orderID": "abc123", "success": True}
    signed = {"signature": "0xfeed"}
    resp = post_order(client, signed)
    assert resp == {"orderID": "abc123", "success": True}
    client.post_order.assert_called_once_with(signed)


def test_post_order_wraps_non_dict_response() -> None:
    client = MagicMock()
    client.post_order.return_value = "raw-text"
    resp = post_order(client, {"sig": "x"})
    assert resp == {"raw": "raw-text"}
