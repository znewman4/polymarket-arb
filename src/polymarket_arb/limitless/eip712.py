"""EIP-712 order signing for the Limitless Exchange CLOB.

Adapted from the dr-manhattan open-source library (MIT licence):
  https://github.com/guzus/dr-manhattan/blob/main/dr_manhattan/exchanges/limitless.py

The Limitless CLOB requires EIP-712 signed orders embedded in the POST /orders
body.  This module exposes a single public function, build_signed_order(), that
constructs the full ``order`` dict (amounts, salt, signature) ready to be placed
directly inside the API payload.

Chain: Base mainnet (chain_id=8453).
"""

from __future__ import annotations

import time
from typing import Any

_CHAIN_ID = 8453  # Base mainnet
_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_PRICE_TICK = 0.001
_SCALE = 1_000_000  # 6 decimal places (USDC)

# EIP-712 type definitions for the Limitless Order struct
_EIP712_TYPES: dict[str, Any] = {
    "EIP712Domain": [
        {"name": "name",              "type": "string"},
        {"name": "version",           "type": "string"},
        {"name": "chainId",           "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "Order": [
        {"name": "salt",          "type": "uint256"},
        {"name": "maker",         "type": "address"},
        {"name": "signer",        "type": "address"},
        {"name": "taker",         "type": "address"},
        {"name": "tokenId",       "type": "uint256"},
        {"name": "makerAmount",   "type": "uint256"},
        {"name": "takerAmount",   "type": "uint256"},
        {"name": "expiration",    "type": "uint256"},
        {"name": "nonce",         "type": "uint256"},
        {"name": "feeRateBps",    "type": "uint256"},
        {"name": "side",          "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
    ],
}


def build_signed_order(
    *,
    token_id: str,
    price: float,
    size_usdc: float,
    side: str,           # "BUY" or "SELL"
    order_type: str,     # "GTC" or "FOK"
    exchange_address: str,
    private_key: str,
    wallet_address: str,
    chain_id: int = _CHAIN_ID,
    fee_rate_bps: int = 300,
) -> dict[str, Any]:
    """Build and EIP-712 sign a Limitless CLOB order.

    Args:
        token_id:         YES or NO token ID (numeric string from market.token_id_*).
        price:            Price per share as a decimal 0.0-1.0.
        size_usdc:        Order size in USDC.
        side:             "BUY" or "SELL".
        order_type:       "GTC" or "FOK".
        exchange_address: Venue exchange contract from market.address (venue.exchange).
        private_key:      Hex private key for EIP-712 signing.
        wallet_address:   Checksummed wallet address (maker/signer).
        chain_id:         EVM chain ID; defaults to 8453 (Base mainnet).
        fee_rate_bps:     Taker fee in basis points; defaults to 300 (3%).

    Returns:
        The ``order`` dict to embed in POST /orders → {"order": <this>, ...}.
    """
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    # Salt: JS-safe integer derived from current time (mirrors SDK pattern)
    ts_ms = int(time.time() * 1000)
    nano_offset = int((time.perf_counter() * 1_000_000) % 1_000_000)
    one_day_ms = 1_000 * 60 * 60 * 24
    salt = ts_ms * 1_000 + nano_offset + one_day_ms

    # Scale amounts to 6 decimals and align to price tick
    shares = int(size_usdc * _SCALE)
    price_int = int(price * _SCALE)
    tick_int = int(_PRICE_TICK * _SCALE)
    shares_step = _SCALE // tick_int
    if shares % shares_step != 0:
        shares = (shares // shares_step) * shares_step

    numerator = shares * price_int * _SCALE
    denominator = _SCALE * _SCALE
    side_int = 0 if side.upper() == "BUY" else 1

    if side_int == 0:  # BUY: makerAmount = USDC (round up), takerAmount = shares
        collateral = (numerator + denominator - 1) // denominator
        maker_amount = collateral
        taker_amount = shares
    else:              # SELL: makerAmount = shares, takerAmount = USDC (round down)
        collateral = numerator // denominator
        maker_amount = shares
        taker_amount = collateral

    # EIP-712 message — all fields as Python int for correct ABI encoding
    eip712_message: dict[str, Any] = {
        "salt":          salt,
        "maker":         wallet_address,
        "signer":        wallet_address,
        "taker":         _ZERO_ADDRESS,
        "tokenId":       int(token_id),
        "makerAmount":   maker_amount,
        "takerAmount":   taker_amount,
        "expiration":    0,
        "nonce":         0,
        "feeRateBps":    fee_rate_bps,
        "side":          side_int,
        "signatureType": 0,  # EOA
    }

    domain: dict[str, Any] = {
        "name":              "Limitless CTF Exchange",
        "version":           "1",
        "chainId":           chain_id,
        "verifyingContract": exchange_address,
    }

    encoded = encode_typed_data(full_message={
        "types":       _EIP712_TYPES,
        "primaryType": "Order",
        "domain":      domain,
        "message":     eip712_message,
    })
    account = Account.from_key(private_key)
    signed = account.sign_message(encoded)
    sig_hex: str = signed.signature.hex()
    if not sig_hex.startswith("0x"):
        sig_hex = "0x" + sig_hex

    # API order payload — tokenId as string, expiration as string (API requirement)
    order: dict[str, Any] = {
        "salt":          salt,
        "maker":         wallet_address,
        "signer":        wallet_address,
        "taker":         _ZERO_ADDRESS,
        "tokenId":       token_id,   # string for API
        "makerAmount":   maker_amount,
        "takerAmount":   taker_amount,
        "expiration":    "0",        # string for API
        "nonce":         0,
        "feeRateBps":    fee_rate_bps,
        "side":          side_int,
        "signatureType": 0,
        "signature":     sig_hex,
    }

    # GTC orders include the price field
    if order_type.upper() == "GTC":
        order["price"] = round(price, 3)

    return order
