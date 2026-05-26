"""Domain models for Limitless x Polymarket cross-market arbitrage."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LimitlessMarketEntry:
    slug: str
    title: str
    yes_price: float   # 0.0-1.0
    address: str       # EVM contract address, required for live order placement


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
