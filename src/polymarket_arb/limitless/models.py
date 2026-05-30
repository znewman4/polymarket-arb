"""Domain models for Limitless x Polymarket cross-market arbitrage."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LimitlessMarketEntry:
    slug: str
    title: str
    yes_price: float   # 0.0-1.0
    address: str       # EVM contract address, required for live order placement
    token_id_yes: str = ""  # CLOB token ID for YES outcome (numeric string from tokens.yes)
    token_id_no: str = ""   # CLOB token ID for NO outcome (numeric string from tokens.no)


@dataclass(frozen=True)
class PolyMarketEntry:
    condition_id: str
    token_id_yes: str   # CLOB token ID for YES outcome
    token_id_no: str    # CLOB token ID for NO outcome
    question: str
    yes_price: float    # 0.0-1.0


@dataclass(frozen=True)
class ArbMatch:
    limitless: LimitlessMarketEntry
    poly: PolyMarketEntry
    similarity: float
    arb_gap: float   # = 1.0 - (lim_yes + poly_yes); positive = opportunity
    status: str      # Set by compute_arb(), never match_markets(): ARB_OPPORTUNITY | OVER_ROUND | EFFICIENT


@dataclass(frozen=True)
class LimitlessOrderResult:
    status: str        # paper_filled | rejected_kill_switch | live_submitted | failed
    order_id: str | None
    side: str          # YES | NO
    price: float
    size_usdc: float
    market_slug: str
    error: str | None


@dataclass(frozen=True)
class LimitlessArbPosition:
    """An open Limitless x Polymarket arb position reconstructed from orders_log.

    Used by the convergence-based early-exit scanner to monitor open positions
    and decide whether current market prices have converged enough since entry
    to lock in a profit before resolution.
    """

    position_id: str
    limitless_slug: str
    poly_condition_id: str
    poly_token_id_no: str
    lim_entry_price: float
    poly_yes_entry: float
    arb_gap: float
    stake_usdc: float
    open_ts_ms: int
