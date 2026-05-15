"""Configuration models for the nesting/contradiction strategy backtest."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RelationshipBacktestConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    """Configuration for one simulated nesting/contradiction strategy run."""

    run_id: str = ""
    strategy_name: str = "nesting_contradiction"
    starting_cash_usdc: Decimal = Decimal("10000")
    base_currency: str = "USDC"

    # Universe / relationship filters
    start_ts_ms: int | None = None
    end_ts_ms: int | None = None
    min_relationship_confidence: float = Field(default=0.65, ge=0.0, le=1.0)
    min_coverage_score: float = Field(default=0.60, ge=0.0, le=1.0)
    allow_manual_review_relationships: bool = False
    relationship_types: list[str] = [
        "nested_a_implies_b",
        "nested_b_implies_a",
        "contradiction",
        "inverse",
        "mutually_exclusive",
        "same_entity_exclusive",
        "mutually_exclusive_category",
        "inverse_temporal_order",
    ]

    # Signal thresholds
    min_gross_edge: float = Field(default=0.02, ge=0.0)
    min_net_edge: float = Field(default=0.01, ge=0.0)
    quote_staleness_limit_ms: int = 6 * 60 * 60 * 1000

    # Sizing
    max_stake_per_trade_usdc: Decimal = Decimal("250")
    max_stake_per_market_usdc: Decimal = Decimal("500")
    max_concurrent_positions: int = 20
    allow_negative_cash: bool = False

    # Costs (basis points)
    fee_bps: Decimal = Decimal("0")
    builder_fee_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("50")

    # Execution model
    execution_model: Literal[
        "price_history_only", "spread_model", "recorded_depth", "mark_only"
    ] = "price_history_only"
    allow_price_history_only: bool = True
    allow_unsupported_shorting: bool = False  # always False, safety field only

    # Replay cadence
    mtm_interval_ms: int = 6 * 60 * 60 * 1000
    signal_interval_ms: int = 60 * 60 * 1000


class CategoryBundleBacktestConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    """Configuration for conservative N-way category bundle scans/backtests."""

    run_id: str = ""
    strategy_name: str = "category_bundle"
    starting_cash_usdc: Decimal = Decimal("10000")
    base_currency: str = "USDC"

    metadata_path: str = "configs/outcome_spaces/outcome_spaces_v1.yaml"
    start_ts_ms: int | None = None
    end_ts_ms: int | None = None

    min_net_edge: Decimal = Decimal("0.01")
    quote_staleness_limit_ms: int = 6 * 60 * 60 * 1000
    signal_interval_ms: int = 60 * 60 * 1000

    max_stake_per_bundle_usdc: Decimal = Decimal("1000")
    allow_uncertain_bundles: bool = False

    fee_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("50")


class ContextAwareBacktestConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    """Configuration for context-aware relationship replay."""

    run_id: str = ""
    strategy_name: str = "context_aware"
    lane: Literal[
        "strict_context_valid",
        "reviewed_context_valid",
        "exploratory_context_unreviewed",
        "exploratory_context_auto_approved",
        "all_context_research",
    ] = "strict_context_valid"
    starting_cash_usdc: Decimal = Decimal("10000")
    base_currency: str = "USDC"
    context_time_mode: Literal["ex_post_research", "historical_replay_safe"] = "ex_post_research"
    relationship_universe: Literal[
        "accepted_only",
        "all_with_context_decisions",
        "reviewed_lanes",
    ] = "reviewed_lanes"

    start_ts_ms: int | None = None
    end_ts_ms: int | None = None
    min_relationship_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    min_context_rule_confidence: float = Field(default=0.65, ge=0.0, le=1.0)
    min_coverage_score: float = Field(default=0.60, ge=0.0, le=1.0)
    min_gross_edge: float = Field(default=0.02, ge=0.0)
    min_net_edge: float = Field(default=0.01, ge=0.0)
    quote_staleness_limit_ms: int = 6 * 60 * 60 * 1000
    signal_interval_ms: int = 60 * 60 * 1000

    max_stake_per_trade_usdc: Decimal = Decimal("250")
    max_stake_per_market_usdc: Decimal = Decimal("500")
    max_concurrent_positions: int = 20
    allow_negative_cash: bool = False

    fee_bps: Decimal = Decimal("0")
    builder_fee_bps: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("50")
    execution_model: Literal[
        "price_history_only", "spread_model", "recorded_depth", "mark_only"
    ] = "price_history_only"
    allow_unsupported_shorting: bool = False
    holdout_pct: float = Field(default=0.30, ge=0.0, le=1.0)
    # When True, also include exploratory_context_auto_approved decisions.
    # Results are capped at inconclusive; never headline credible.
    include_auto_approved: bool = False

    # Pair economic viability filter for sum-based strategy types.
    # 0.0 = disabled (no behaviour change, backwards-compatible default).
    # For mutually_exclusive_category and similar types, skip pairs where
    # P(A)+P(B) never meets min_combined_prob OR neither P(A) nor P(B)
    # ever meets min_single_prob across the full price history.
    min_combined_prob_for_pairwise: float = Field(default=0.0, ge=0.0, le=1.0)
    min_single_prob_for_pairwise: float = Field(default=0.0, ge=0.0, le=1.0)


class DiagnosticBacktestConfig(ContextAwareBacktestConfig):
    """Diagnostic bypass variant of the context-aware backtest.

    RESEARCH-ONLY / DIAGNOSTIC-ONLY. All runs are labelled
    ``diagnostic_only_not_credible`` and can never produce ``credible_positive``.
    These bypass flags are for probing data-pipeline correctness, not for
    generating credible strategy results.
    """

    strategy_name: str = "diagnostic_context_aware"

    # Bypass flags — each flag relaxes one production guardrail.
    bypass_review_lane_checks: bool = False
    bypass_min_confidence: bool = False
    bypass_coverage_threshold: bool = False
    include_all_validation_statuses: bool = False
    allow_unresolved_markets: bool = True

    @property
    def effective_min_confidence(self) -> float:
        return 0.0 if self.bypass_min_confidence else self.min_relationship_confidence

    @property
    def effective_min_coverage_score(self) -> float:
        return 0.0 if self.bypass_coverage_threshold else self.min_coverage_score
