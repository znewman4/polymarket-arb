"""Live-trading data models.

``OrderResult`` is what ``OrderClient.place_order`` returns.  ``OrdersLogRow``
is the persistent audit record written to the ``orders_log`` parquet table on
every place_order attempt, regardless of outcome (paper, live, rejected).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class OrderResult:
    """Outcome of one ``OrderClient.place_order`` call.

    For paper-mode placements, ``submitted=False`` and the fill data comes from
    the simulator.  For live placements, ``submitted=True`` and the fill data
    comes from the network response.
    """

    intent_id: str
    submitted: bool  # False for paper-mode, kill-switch reject, or preflight reject
    status: str  # "paper_filled" | "paper_partial" | "paper_no_book"
                 # | "rejected_kill_switch" | "rejected_preflight"
                 # | "rejected_orders_disallowed" | "rejected_paper_mode_off"
                 # | "live_submitted" | "live_failed"
    paper_mode: bool
    token_id: str
    side: str
    requested_size: Decimal
    filled_size: Decimal
    avg_fill_price: Decimal | None
    notional_usdc: Decimal
    fees_usdc: Decimal
    reason: str
    ts_ms: int
    # Audit
    kill_switch_active: bool
    orders_allowed: bool
    preflight_passed: bool
    preflight_token_id: str | None = None
    http_status: int | None = None  # only set in live mode


@dataclass(frozen=True)
class OrdersLogRow:
    """One row of the ``orders_log`` parquet table.  Every place_order call
    produces exactly one row, even on rejection.  Designed to mirror the
    standardised trade row's key fields so the agent's behaviour can be
    cross-checked against the backtest later.
    """

    intent_id: str
    ts_ms: int
    strategy_id: str
    token_id: str
    market_id: str
    side: str
    requested_size: str  # decimal-as-string for parquet portability
    filled_size: str
    avg_fill_price: str | None
    notional_usdc: str
    fees_usdc: str
    status: str
    reason: str
    paper_mode: bool
    kill_switch_active: bool
    orders_allowed: bool
    preflight_passed: bool
    preflight_token_id: str | None
    http_status: int | None
    # Provenance — which standardised lane / hypothesis prompted this order.
    source_lane: str = ""
    source_relationship_id: str = ""
    source_hypothesis_id: str = ""
    schema_version: int = 1
    ingested_ts_ms: int = 0
    # Free-form metadata for human review later (e.g. evidence_summary).
    notes: str = ""
    detail_json: str = field(default_factory=lambda: "{}")


@dataclass(frozen=True)
class PositionRow:
    """One append-only position state record created from a filled paper order."""

    position_id: str
    strategy_id: str
    market_id: str
    token_id: str
    side: str
    open_ts_ms: int
    entry_price: str
    size: str
    notional_usdc: str
    source_relationship_id: str
    notes: str
    status: str
    close_ts_ms: int | None = None
    close_price: str | None = None
    realised_pnl: str | None = None
    schema_version: int = 1
