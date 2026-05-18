"""Typed row contracts for the space-centric research pipeline.

These models exist to PREVENT the failure modes of earlier reports, where
diagnostic-only rows (e.g. ``same_topic_no_trade``) leaked into trade and PnL
totals.  Every reporting/aggregation step must use these typed rows and the
validation helpers below.

Hard rules
----------
1. ``DiagnosticOnlyRelationshipRow`` instances NEVER appear in any aggregation
   that counts gross opportunities, accepted trades, or PnL.
2. ``StrategyEligibleRelationshipRow.strategy_family`` is required and non-empty.
3. ``AcceptedSimulatedTradeRow`` requires ``relationship_id``, ``space_id`` (or
   ``space_attribution_status``), and ``strategy_family`` populated.
4. ``space_id`` is selected via the precedence:
     outcome_space_id  >  context_space_id  >  bundle_space_id  >  synthetic
   When falling back, ``space_attribution_status`` records why.

RESEARCH-ONLY — no live trading.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Subtypes that look like relationships but are NEVER strategy-eligible.
# These rows can appear in audits but must be filtered before any trade/PnL aggregation.
DIAGNOSTIC_ONLY_SUBTYPES: frozenset[str] = frozenset({
    "same_topic_no_trade",
    "same_reference_clock_only",
    "mixed_party_nomination",
    "mixed_stage",
    "mixed_subtype",
    "nomination_to_general_dependency",     # blocked by design
    "first_round_to_general_dependency",    # blocked by design
})

# Subtypes that ARE valid strategy candidates.
STRATEGY_ELIGIBLE_SUBTYPES: frozenset[str] = frozenset({
    # Sports / ranking
    "championship_implies_conference",
    "championship_implies_top_n",
    "exact_finish_implies_top_n",
    "top_n_implies_broader_top_n",
    "exact_positions_mutually_exclusive",
    "winner_mutually_exclusive_with_exact_finish",
    # Mutual exclusion bundles
    "single_winner_competition_mutual_exclusion",
    "sports_title_winners_mutually_exclusive",
    "primary_candidates_mutually_exclusive",
    "candidate_winner_same_election",
    "first_round_winner",
    # Threshold / date
    "threshold_higher_implies_lower",
    "threshold_lower_implies_higher",
    "date_earlier_implies_later",
})

# Space attribution status values used when outcome/context/bundle space is missing
SpaceAttributionStatus = Literal[
    "outcome_space",        # primary outcome_space_id present
    "context_space",        # falls back to context_space_id
    "bundle_space",         # bundle scanner space id
    "synthetic_or_missing",  # no attribution available
]


def is_diagnostic_only_subtype(relationship_subtype: str | None) -> bool:
    """Return True if a subtype is diagnostic-only and must NOT enter trade totals."""
    if not relationship_subtype:
        return True
    return relationship_subtype in DIAGNOSTIC_ONLY_SUBTYPES


def space_id_for(
    outcome_space_id: str | None = None,
    context_space_id: str | None = None,
    bundle_space_id: str | None = None,
    fallback: str | None = None,
) -> tuple[str, SpaceAttributionStatus]:
    """Return (space_id, attribution_status) using the precedence rule.

    Precedence: outcome_space > context_space > bundle_space > synthetic.
    """
    if outcome_space_id:
        return outcome_space_id, "outcome_space"
    if context_space_id:
        return context_space_id, "context_space"
    if bundle_space_id:
        return bundle_space_id, "bundle_space"
    return (fallback or "unattributed"), "synthetic_or_missing"


# ─── Relationship-level rows ──────────────────────────────────────────────────


class RelationshipAuditRow(BaseModel):
    """Basic audit info for ANY relationship — includes diagnostic-only ones.

    Aggregations that operate on this row type must explicitly handle/exclude
    diagnostic-only subtypes; otherwise they will leak into totals.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    relationship_id: str
    relationship_type: str = ""
    relationship_subtype: str = ""
    relationship_family: str = ""
    validation_status: str = "unknown"
    final_confidence: float = 0.0
    space_id: str = "unattributed"
    space_attribution_status: SpaceAttributionStatus = "synthetic_or_missing"
    strategy_eligible: bool = False
    diagnostic_only: bool = True

    @field_validator("relationship_id")
    @classmethod
    def _rid_required(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("relationship_id is required")
        return v


class StrategyEligibleRelationshipRow(BaseModel):
    """A relationship that IS allowed to enter strategy totals.

    Construction requires ``strategy_family`` to be non-empty and the subtype
    to NOT be diagnostic-only.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    relationship_id: str
    relationship_type: str
    relationship_subtype: str
    relationship_family: str
    strategy_family: str   # required, non-empty
    space_id: str
    space_attribution_status: SpaceAttributionStatus
    template_id: str = ""
    final_confidence: float = 0.0
    validation_status: str = "accepted"

    @field_validator("strategy_family")
    @classmethod
    def _family_required(cls, v: str) -> str:
        if not v or not v.strip() or v.strip().lower() in ("none", "unknown"):
            raise ValueError("strategy_family must be a non-empty, non-'none' value")
        return v

    @field_validator("relationship_subtype")
    @classmethod
    def _not_diagnostic_only(cls, v: str) -> str:
        if v in DIAGNOSTIC_ONLY_SUBTYPES:
            raise ValueError(
                f"relationship_subtype={v!r} is diagnostic-only and cannot be "
                "a StrategyEligibleRelationshipRow"
            )
        return v


class DiagnosticOnlyRelationshipRow(BaseModel):
    """A relationship that exists for audit but NEVER enters trade/PnL totals.

    Examples: same_topic_no_trade, mixed_party_nomination, etc.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    relationship_id: str
    relationship_subtype: str
    reason: str = ""
    space_id: str = "unattributed"

    @field_validator("relationship_subtype")
    @classmethod
    def _must_be_diagnostic_only(cls, v: str) -> str:
        if v and v not in DIAGNOSTIC_ONLY_SUBTYPES:
            raise ValueError(
                f"relationship_subtype={v!r} is not in DIAGNOSTIC_ONLY_SUBTYPES — "
                "use StrategyEligibleRelationshipRow instead"
            )
        return v


# ─── Opportunity / trade rows ─────────────────────────────────────────────────


class GrossOpportunityRow(BaseModel):
    """A tick-level violation signal BEFORE costs.

    Diagnostic-only relationships are NEVER allowed here.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    relationship_id: str
    space_id: str
    space_attribution_status: SpaceAttributionStatus
    strategy_family: str
    signal_ts_ms: int
    gross_edge: float
    relationship_subtype: str = ""

    @field_validator("relationship_subtype")
    @classmethod
    def _not_diagnostic(cls, v: str) -> str:
        if v in DIAGNOSTIC_ONLY_SUBTYPES:
            raise ValueError(
                f"GrossOpportunityRow rejects diagnostic-only subtype {v!r}"
            )
        return v


class NetOpportunityRow(BaseModel):
    """A gross opportunity that survives configured costs."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    relationship_id: str
    space_id: str
    strategy_family: str
    signal_ts_ms: int
    gross_edge: float
    net_edge_after_cost: float


class ViolationWindowRow(BaseModel):
    """A contiguous run of violations for one relationship."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    relationship_id: str
    space_id: str
    window_id: str
    window_start_ts_ms: int
    window_end_ts_ms: int
    tick_count: int


class AcceptedSimulatedTradeRow(BaseModel):
    """An executed simulated trade with full attribution.

    Required: relationship_id, space_id, strategy_family.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    trade_id: str
    candidate_id: str = ""
    trade_group_id: str = ""
    relationship_id: str
    relationship_type: str = ""
    relationship_subtype: str = ""
    relationship_family: str = ""
    space_id: str
    space_attribution_status: SpaceAttributionStatus
    strategy_family: str
    template_id: str = ""
    leg: str = ""
    token_id: str = ""
    fill_ts_ms: int = 0
    fill_price: float = 0.0
    shares: float = 0.0
    notional_usdc: float = 0.0
    fees_usdc: float = 0.0
    slippage_cost_usdc: float = 0.0
    mark_to_market_value_usdc: float | None = None
    realised_pnl_usdc: float | None = None
    gross_edge: float = 0.0
    net_edge_after_cost: float = 0.0
    first_entry_or_reentry: str = "first"
    violation_window_id: str = ""
    preset_name: str = ""
    label: str = ""

    @field_validator("strategy_family")
    @classmethod
    def _family_required(cls, v: str) -> str:
        if not v or not v.strip() or v.strip().lower() in ("none", "unknown"):
            raise ValueError(
                "AcceptedSimulatedTradeRow requires non-empty strategy_family"
            )
        return v


class BlockedOpportunityRow(BaseModel):
    """A candidate that was blocked, with the blocker reason recorded."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    relationship_id: str
    space_id: str
    strategy_family: str = ""
    blocker_reason: str
    blocker_category: str = "other"
    signal_ts_ms: int | None = None


# ─── Bundle rows ──────────────────────────────────────────────────────────────


class BundleDiagnosticRow(BaseModel):
    """One row of bundle scan diagnostic (per outcome_space tick)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    space_id: str   # outcome_space_id for the bundle
    space_attribution_status: SpaceAttributionStatus = "bundle_space"
    bundle_basket: str = ""    # "buy_all_yes" | "buy_all_no" | "blocked_incomplete_yes" | "none"
    observed_candidates: int = 0
    known_total_candidates: int | None = None
    completeness_status: str = "unknown"
    sum_yes_prices: float = 0.0
    gross_edge: float = 0.0
    net_edge_after_costs: float = 0.0
    rejection_reason: str = ""
    signal_ts_ms: int | None = None


class BundleAcceptedTradeRow(BaseModel):
    """An accepted bundle simulated trade leg."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    trade_id: str
    space_id: str
    bundle_basket: str
    candidate: str = ""
    market_id: str = ""
    token_id: str = ""
    fill_ts_ms: int = 0
    fill_price: float = 0.0
    shares: float = 0.0
    notional_usdc: float = 0.0
    fees_usdc: float = 0.0


# ─── Aggregation rows ─────────────────────────────────────────────────────────


SpaceGrade = Literal[
    "A_PROFITABLE_ROBUST_CANDIDATE",
    "B_PROMISING_GATE_BLOCKED",
    "C_INFRASTRUCTURE_BLOCKED",
    "D_VALID_BUT_STRATEGICALLY_WEAK",
    "E_INVALID_OR_AUDIT_RISK",
    "F_OVERFIT_ONE_OFF",
    "G_ECONOMICALLY_TINY_SIGNAL",
    "UNGRADED",
]


class SpaceSummaryRow(BaseModel):
    """Aggregated per-space stats with grade and recommended action."""

    model_config = ConfigDict(extra="allow", frozen=False)

    space_id: str
    space_name: str = ""
    domain: str = ""
    space_attribution_status: SpaceAttributionStatus = "outcome_space"
    strategy_families_available: list[str] = Field(default_factory=list)

    # Counts
    market_count: int = 0
    relationship_count: int = 0
    strict_relationship_count: int = 0
    exploratory_relationship_count: int = 0
    diagnostic_only_relationship_count: int = 0

    # Data coverage
    price_history_coverage_pct: float = 0.0
    aligned_tick_count: int = 0

    # Opportunity / trade activity
    gross_violation_count: int = 0
    net_violation_count: int = 0
    accepted_trade_count: int = 0    # pairs of legs / 2
    raw_fill_count: int = 0          # leg count
    unique_violation_window_count: int = 0
    independent_violation_windows: int = 0
    distinct_relationships_traded: int = 0
    distinct_bundles_traded: int = 0

    # PnL / risk
    simulated_pnl: float = 0.0
    average_trade_return_pct: float = 0.0
    median_trade_return_pct: float = 0.0
    total_trade_cost: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown: float = 0.0
    total_slippage: float = 0.0
    avg_edge: float = 0.0
    median_edge: float = 0.0
    best_edge: float = 0.0
    worst_trade_pnl: float = 0.0
    best_trade_pnl: float = 0.0

    # Blockers / audit
    primary_blocker: str = ""
    secondary_blocker: str = ""
    suspicious_flag_count: int = 0
    accepted_trade_suspicious_flag_count: int = 0

    # Completeness (mostly for bundles)
    completeness_status: str = "unknown"
    known_total_candidates: int | None = None

    # Grade & recommendation
    space_grade: SpaceGrade = "UNGRADED"
    recommended_next_action: str = ""

    # Overfit / dominance metrics
    dominant_trade_share_of_pnl: float = 0.0
    dominant_relationship_share_of_pnl: float = 0.0

    # Detailed funnel breakdown (replays emit raw_fill_count; track each stage separately)
    raw_replay_fills: int = 0           # individual buy-leg rows in trades.csv
    gross_opportunity_rows: int = 0     # signal rows where gross_edge > 0 (pre-cost)
    net_opportunity_rows: int = 0       # signal rows that survived costs

    # Integrity
    integrity_warning: str = ""  # e.g. "accepted_trades_without_gross_violations"


class SpaceOptimisationRow(BaseModel):
    """One (space, parameter_set) result row."""

    model_config = ConfigDict(extra="allow", frozen=False)

    space_id: str
    parameter_set_id: str
    strategy_family: str = ""

    # Parameter values
    slippage_bps: int = 0
    min_edge: float = 0.0
    min_confidence: float = 0.0
    max_exposure_per_space: float = 0.0
    max_stake_per_trade: float = 0.0
    reentry_policy: str = "first_violation_only"
    cooldown_minutes: int = 0
    alignment_mode: str = "forward_fill_max_age"
    sizing_policy: str = "flat_small"

    # Results
    simulated_pnl: float = 0.0
    accepted_trades: int = 0
    raw_fills: int = 0
    unique_violation_windows: int = 0
    distinct_relationships_traded: int = 0
    distinct_bundles_traded: int = 0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_trade_pnl: float = 0.0
    median_trade_pnl: float = 0.0
    max_loss: float = 0.0
    max_win: float = 0.0
    slippage_paid: float = 0.0
    exposure_used: float = 0.0
    robustness_score: float = 0.0
    overfit_warning: bool = False
    dominant_trade_share_of_pnl: float = 0.0
    positive_after_costs: bool = False
    credibility_label: str = "exploratory_only_not_credible"
