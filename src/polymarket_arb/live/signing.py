"""Polymarket CLOB API L2 order signing (EIP-712).

**THIS IS A STUB.**  The signing path is invoked only when the OrderClient is
asked to place a LIVE order (``paper_mode=False`` AND ``orders_allowed=True``
AND a private key is configured).  In paper mode the OrderClient never calls
into this module — the strategy is exercised end-to-end without any wallet,
signing key, or network contact.

When we're ready to flip to live, this module will gain the EIP-712 typed-data
hashing + secp256k1 signing required by the Polymarket CLOB API.  Until then
the stub raises so any code path that accidentally invokes signing in paper
mode fails loud.
"""

from __future__ import annotations


class SigningNotConfigured(RuntimeError):
    """Raised when a live order tries to sign without a configured key."""


class SigningStubError(NotImplementedError):
    """Raised when the stub is invoked — signals that live trading is not yet
    implemented and the OrderClient should not have routed here."""


def sign_order_intent(intent, private_key_hex: str | None) -> dict:
    """Stub for EIP-712 signing.  Always raises in the current phase.

    Args:
        intent: An ``OrderIntent`` from ``polymarket_arb.risk.models``.
        private_key_hex: The signer's private key.  If None / empty the
            function raises ``SigningNotConfigured`` instead of the stub error
            so the caller's safety check fires before any network attempt.
    """
    if not private_key_hex:
        raise SigningNotConfigured(
            "POLYMARKET_PRIVATE_KEY is unset; cannot sign a live order.  "
            "Flip back to paper_mode=True or configure the key.",
        )
    raise SigningStubError(
        "Live signing is not implemented in this phase.  The OrderClient must "
        "remain in paper_mode=True.  See plan Phase 3 — signing is intentionally "
        "deferred until paper-mode behaviour is verified end-to-end on the VPS.",
    )
