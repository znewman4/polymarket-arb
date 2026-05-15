"""Thin shim: a `raise_if_orders_disallowed()` helper that delegates to the
preflight gate.

This module exists so that any future order-placement code path imports a
*top-level compliance helper* rather than the risk subsystem internals — this
keeps the dependency graph one-way (compliance ← risk ← strategies).
"""

from __future__ import annotations

from ..settings import Settings


class OrdersForbidden(RuntimeError):
    """Raised when orders are not currently permitted."""


def raise_if_orders_disallowed(settings: Settings) -> None:
    """Cheap fail-fast check — full gate runs in `risk.preflight.PreflightGate`.

    This is the *minimum* check; passing this does NOT mean an order may be
    placed. It only means the global flag isn't off.
    """

    if not settings.orders_allowed:
        raise OrdersForbidden(
            "orders_allowed=false. Phase 0-9 are research-only. "
            "Flipping this flag requires a signed git tag plus the full "
            "PreflightGate to issue a token."
        )
