"""Tests for the Limitless EIP-712 order signing module."""

from __future__ import annotations

import pytest

from polymarket_arb.limitless.eip712 import build_signed_order

# Deterministic test private key (32-byte secp256k1 scalar, safe for tests)
_PRIVATE_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
_WALLET = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"  # derived from above key
_EXCHANGE = "0x05c748E2f4DcDe0ec9Fa8DDc40DE6b867f923fa5"
_TOKEN_ID = "97121182863313410873736562183878506062385048242276778296840510840301710926049"


def _order(**overrides):
    kwargs = dict(
        token_id=_TOKEN_ID,
        price=0.6,
        size_usdc=10.0,
        side="BUY",
        order_type="GTC",
        exchange_address=_EXCHANGE,
        private_key=_PRIVATE_KEY,
        wallet_address=_WALLET,
    )
    kwargs.update(overrides)
    return build_signed_order(**kwargs)


# ─── output shape ─────────────────────────────────────────────────────────────


def test_returns_dict():
    assert isinstance(_order(), dict)


def test_required_keys_present():
    order = _order()
    required = {
        "salt", "maker", "signer", "taker",
        "tokenId", "makerAmount", "takerAmount",
        "expiration", "nonce", "feeRateBps",
        "side", "signatureType", "signature",
    }
    assert required.issubset(order.keys())


def test_gtc_order_includes_price():
    order = _order(order_type="GTC")
    assert "price" in order
    assert abs(order["price"] - 0.6) < 0.001


def test_fok_order_excludes_price():
    order = _order(order_type="FOK")
    assert "price" not in order


# ─── field values ─────────────────────────────────────────────────────────────


def test_maker_and_signer_equal_wallet():
    order = _order()
    assert order["maker"] == _WALLET
    assert order["signer"] == _WALLET


def test_taker_is_zero_address():
    assert _order()["taker"] == "0x0000000000000000000000000000000000000000"


def test_token_id_is_string():
    assert isinstance(_order()["tokenId"], str)
    assert _order()["tokenId"] == _TOKEN_ID


def test_expiration_is_string_zero():
    assert _order()["expiration"] == "0"


def test_nonce_is_zero():
    assert _order()["nonce"] == 0


def test_signature_type_is_zero():
    assert _order()["signatureType"] == 0


def test_fee_rate_bps_default():
    assert _order()["feeRateBps"] == 300


def test_fee_rate_bps_custom():
    assert _order(fee_rate_bps=100)["feeRateBps"] == 100


def test_side_buy_is_zero():
    assert _order(side="BUY")["side"] == 0


def test_side_sell_is_one():
    assert _order(side="SELL")["side"] == 1


def test_side_case_insensitive():
    assert _order(side="buy")["side"] == 0
    assert _order(side="sell")["side"] == 1


# ─── signature ────────────────────────────────────────────────────────────────


def test_signature_is_hex_string():
    sig = _order()["signature"]
    assert isinstance(sig, str)
    assert sig.startswith("0x")


def test_signature_is_65_bytes():
    sig = _order()["signature"]
    # 65 bytes = 130 hex chars + "0x" prefix
    assert len(sig) == 132


def test_same_inputs_different_salt_each_call():
    # Salt includes nanosecond timestamp so each call produces a different value
    o1 = _order()
    o2 = _order()
    # Signatures will differ because salt differs
    # (Very rarely both happen in the same nanosecond; acceptable flakiness)
    assert o1["salt"] != o2["salt"] or o1["signature"] != o2["signature"]


# ─── BUY amount arithmetic ────────────────────────────────────────────────────


def test_buy_maker_amount_is_usdc_collateral():
    # BUY size_usdc=100 at price=0.5: size is treated as share count (100 shares).
    # Collateral (USDC paid) = 100 shares * 0.5 = 50 USDC = 50_000_000 scaled.
    order = _order(price=0.5, size_usdc=100.0)
    assert order["makerAmount"] == pytest.approx(50_000_000, rel=0.01)


def test_buy_taker_amount_is_shares():
    # taker receives 100 shares = 100_000_000 scaled
    order = _order(price=0.5, size_usdc=100.0)
    assert order["takerAmount"] == pytest.approx(100_000_000, rel=0.01)


def test_sell_maker_amount_is_shares():
    # SELL size_usdc=100 → maker gives 100 shares = 100_000_000 scaled
    order = _order(side="SELL", price=0.5, size_usdc=100.0)
    assert order["makerAmount"] == pytest.approx(100_000_000, rel=0.01)


def test_sell_taker_amount_is_usdc():
    # taker receives 50 USDC = 50_000_000 scaled
    order = _order(side="SELL", price=0.5, size_usdc=100.0)
    assert order["takerAmount"] == pytest.approx(50_000_000, rel=0.01)


# ─── salt ─────────────────────────────────────────────────────────────────────


def test_salt_is_positive_integer():
    assert isinstance(_order()["salt"], int)
    assert _order()["salt"] > 0


def test_salt_within_js_safe_integer():
    # JS Number.MAX_SAFE_INTEGER = 2**53 - 1 = 9_007_199_254_740_991
    assert _order()["salt"] < 9_007_199_254_740_991
