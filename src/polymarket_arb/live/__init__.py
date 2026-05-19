"""Live-trading scaffolding.

Phase 3 of the standardised-backtest plan.  Builds the full live order
infrastructure with ``paper_mode=True`` as the safe default.  When paper-mode
is on, ``OrderClient`` evaluates the strategy and writes ``orders_log`` rows
but NEVER opens a network socket to Polymarket.  Flipping to live trading
requires BOTH ``settings.paper_mode=False`` AND ``settings.orders_allowed=True``
AND a configured signing key.
"""

from .models import OrderResult, OrdersLogRow
from .order_client import OrderClient

__all__ = ["OrderClient", "OrderResult", "OrdersLogRow"]
