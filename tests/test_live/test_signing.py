"""Tests for the Polymarket CLOB V2 signing wrapper."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from polymarket_arb.live.signing import (
    SigningNotConfigured,
    build_clob_client,
    create_and_post_order,
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


def test_create_and_post_order_requires_client() -> None:
    with pytest.raises(SigningNotConfigured):
        create_and_post_order(
            None,
            token_id="tok",
            price=Decimal("0.5"),
            size=Decimal("10"),
            side="buy",
            neg_risk=False,
        )


def test_create_and_post_order_passes_through_response() -> None:
    fake_response = {"orderID": "abc123", "success": True}
    client = MagicMock()
    client.create_and_post_order.return_value = fake_response

    result = create_and_post_order(
        client,
        token_id="tok-a",
        price=Decimal("0.5234"),
        size=Decimal("12.5"),
        side="buy",
        tick_size="0.01",
        neg_risk=False,
    )

    assert result is fake_response
    client.create_and_post_order.assert_called_once()
    _, kwargs = client.create_and_post_order.call_args
    assert kwargs["order_args"].token_id == "tok-a"
    assert kwargs["order_args"].price == 0.5234
    assert kwargs["order_args"].size == 12.5
    assert kwargs["order_args"].side.name == "BUY"
    assert kwargs["options"].tick_size == "0.01"
    assert kwargs["options"].neg_risk is False
    assert kwargs["order_type"] == "GTC"


def test_create_and_post_order_sell_uses_sell_side() -> None:
    fake_response = {"orderID": "sell-1"}
    client = MagicMock()
    client.create_and_post_order.return_value = fake_response

    result = create_and_post_order(
        client,
        token_id="tok",
        price=Decimal("0.50"),
        size=Decimal("10"),
        side="sell",
        neg_risk=False,
    )

    assert result is fake_response
    _, kwargs = client.create_and_post_order.call_args
    assert kwargs["order_args"].side.name == "SELL"


def test_create_and_post_order_neg_risk_sets_option() -> None:
    fake_response = {"orderID": "neg-1"}
    client = MagicMock()
    client.create_and_post_order.return_value = fake_response

    result = create_and_post_order(
        client,
        token_id="tok",
        price=Decimal("0.50"),
        size=Decimal("10"),
        side="buy",
        tick_size="0.001",
        neg_risk=True,
    )

    assert result is fake_response
    _, kwargs = client.create_and_post_order.call_args
    assert kwargs["options"].tick_size == "0.001"
    assert kwargs["options"].neg_risk is True


def test_create_and_post_order_invalid_side_raises() -> None:
    client = MagicMock()
    with pytest.raises(ValueError, match="side must be 'buy' or 'sell'"):
        create_and_post_order(
            client,
            token_id="tok",
            price=Decimal("0.5"),
            size=Decimal("10"),
            side="short",
            neg_risk=False,
        )


def test_create_and_post_order_wraps_non_dict_response() -> None:
    client = MagicMock()
    client.create_and_post_order.return_value = "raw-text"
    resp = create_and_post_order(
        client,
        token_id="tok",
        price=Decimal("0.5"),
        size=Decimal("10"),
        side="buy",
    )
    assert resp == {"raw": "raw-text"}
