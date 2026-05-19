"""Standardised trade contract.

Every accepted trade from every backtest source becomes one ``StandardisedTradeRow``.
DeepSeek hypotheses must also carry a ``DeepSeekPreTradeJustification`` so the
"free-reign AI" lane is auditable.  No automatic promotion/demotion fields are
present — review fields are deliberately neutral.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

SourceLane = Literal[
    "rulebook_baseline_deterministic",
    "rulebook_aggressive_deterministic",
    "ai_freereign_deepseek",  # legacy alias — kept for backwards compat
    "ai_freereign_deepseek_validated",  # only DeepSeek-approved hypotheses
    "ai_freereign_deepseek_wild_diagnostic",  # all hypotheses incl. rejected
    "ai_embedding_only",
    "closed_form_simulator",
    "synthetic_control",
    "control_null_baseline",
    "control",
    "strict_validation",
    "diagnostic_bypass",
    "diagnostic_ultra_loose",
    "other",
]

SOURCE_LANES: tuple[str, ...] = tuple(SourceLane.__args__)  # type: ignore[attr-defined]

ReviewStatus = Literal[
    "unreviewed",
    "flagged_for_review",
    "reviewed_kept",
    "reviewed_excluded",
]


class DeepSeekPreTradeJustification(BaseModel):
    """Structured pre-trade justification for one DeepSeek hypothesis.

    Every DeepSeek-originated trade MUST attach one of these.  The justification
    is captured BEFORE replay so we can audit the reasoning independent of the
    outcome.  Promotion/demotion decisions are deliberately out of scope.
    """

    model_config = ConfigDict(extra="ignore")

    hypothesis_id: str
    relationship_claim: str = Field(
        description="One-line claim: what relationship is being asserted between markets A and B.",
    )
    why_trade_should_work: str = Field(
        description="Mechanism: why the trade is expected to be profitable.",
    )
    why_trade_may_fail: str = Field(
        description="Required failure-mode argument — never empty.",
    )
    evidence_used: str = Field(
        default="",
        description="Specific text evidence the model based its claim on.",
    )
    simulator_or_replay_method: str = Field(
        default="",
        description="One of the buy-only replay subtypes, a closed-form simulator name, "
        "or 'human_review' / 'diagnose_only'.",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertainty_flags_json: str = Field(default="[]")
    failure_modes_json: str = Field(default="[]")
    inside_existing_rulebook: bool = Field(
        default=True,
        description="Model's own assessment: does this relationship overlap an existing "
        "deterministic template?",
    )
    outside_existing_rulebook: bool = Field(
        default=False,
        description="Model's own assessment: is this hypothesis OUTSIDE the existing rulebook?",
    )
    model_name: str = ""
    model_type: str = "generative"
    prompt_version: str = ""
    routing_decision: str = ""
    falsification_test: str = ""
    proposed_trade_logic: str = ""
    raw_response_json: str = Field(
        default="{}",
        description="Full normalised JSON response from the model (string-encoded).",
    )


class StandardisedTradeRow(BaseModel):
    """One leg of one accepted trade, in the single standardised contract.

    Designed to capture every trade-generating source: rulebook deterministic,
    aggressive deterministic, embedding-only, DeepSeek free-reign, closed-form
    simulators, controls, strict validation, and diagnostic bypass runs.

    Ranking metrics are percentage-led — never default to USDC PnL for comparison.
    """

    model_config = ConfigDict(extra="ignore")

    # ── Identity ─────────────────────────────────────────────────────────────
    trade_id: str
    trade_group_id: str = Field(
        description="Stable id linking the two legs of one signal-pair execution.",
    )
    raw_trade_group_id: str = Field(
        default="",
        description="Original source engine trade_group_id / candidate_id before standardised lane scoping.",
    )
    run_id: str = Field(description="Composite parent run id of the standardised backtest.")
    subrun_id: str = Field(
        description="Per-lane sub-run id (e.g. baseline preset run, deepseek preset run).",
    )

    # ── Source lane ──────────────────────────────────────────────────────────
    source_lane: str = Field(
        description="One of SOURCE_LANES — which lane generated the trade.",
    )
    source_agent: str = Field(
        description="The actual agent that produced it — e.g. 'deterministic_template', "
        "'deepseek_generative', 'closed_form_same_yes_spread', 'null_baseline_random_pair'.",
    )
    source_preset: str = Field(default="", description="Research preset name, if any.")
    trade_kind: Literal["replay_fill", "closed_form", "control", "diagnostic"] = "replay_fill"

    # Lane labels (denormalised so reports can filter cheaply).
    is_rulebook: bool = False
    is_ai_generated: bool = False
    is_exploratory: bool = False
    is_strict_validation: bool = False
    is_control: bool = False
    is_diagnostic: bool = False

    # ── Why / what ───────────────────────────────────────────────────────────
    reason: str = Field(
        description="One-line free-text reason the trade was made.",
    )
    relationship_id: str = ""
    relationship_family: str = ""
    relationship_type: str = ""
    relationship_subtype: str = ""
    context_space_id: str = ""
    outcome_space_id: str = ""
    strategy_lane: str = ""

    # ── Hypothesis provenance ────────────────────────────────────────────────
    hypothesis_source: str = Field(
        default="deterministic_relationship",
        description="One of: deterministic_relationship, deterministic_seed, "
        "embedding_only, deepseek_generative, null_baseline, closed_form.",
    )
    hypothesis_id: str = ""
    hypothesis_model_name: str = ""
    hypothesis_model_type: str = ""
    hypothesis_prompt_version: str = ""
    hypothesis_confidence: float | None = None
    hypothesis_relationship_type: str = ""

    # ── DeepSeek / AI pre-trade justification ────────────────────────────────
    justification_present: bool = False
    justification_relationship_claim: str = ""
    justification_why_works: str = ""
    justification_why_may_fail: str = ""
    justification_evidence: str = ""
    justification_simulator_or_method: str = ""
    justification_confidence: float | None = None
    justification_uncertainty_flags_json: str = "[]"
    justification_inside_rulebook: bool | None = None
    justification_outside_rulebook: bool | None = None
    justification_json: str = Field(
        default="{}",
        description="Full normalised DeepSeekPreTradeJustification as JSON.",
    )

    # ── Market / leg ─────────────────────────────────────────────────────────
    leg: Literal["a", "b", "single"] = "a"
    token_id: str = ""
    market_id: str = ""
    side: Literal["buy", "sell", "virtual_short"] = "buy"

    # ── Timing ───────────────────────────────────────────────────────────────
    entry_ts_ms: int | None = None
    exit_ts_ms: int | None = None
    holding_period_ms: int | None = None

    # ── Prices / sizing ──────────────────────────────────────────────────────
    entry_price: float | None = None
    exit_price: float | None = None
    mark_price: float | None = None
    shares: float = 0.0
    stake_usdc: float = 0.0
    notional_usdc: float = 0.0
    fees_usdc: float = 0.0
    slippage_cost_usdc: float = 0.0
    entry_cost_usdc: float = 0.0  # stake_per_leg + fees_per_leg
    # What execution model was REQUESTED for this leg (matches preset/config).
    execution_model: str = "price_history_only"
    # What execution model was ACTUALLY USED for this leg.  When recorded depth
    # was unavailable for the leg's (token_id, entry_ts_ms), the depth pass
    # falls back to price_history_only and records that here.  This makes the
    # data-coverage gap explicit on every row.
    execution_model_used: str = "price_history_only"
    # 1.0 = recorded depth, 0.7 = spread model, 0.4 = price-history flat-bps,
    # 0.1 = mark-only (see backtest.cost_model._EXECUTION_MODEL_CONFIDENCE).
    execution_model_confidence: float | None = None
    # If a depth lookup was attempted but missed, how far away was the nearest
    # snapshot (ms)?  None = depth not attempted or token had no snapshots at all.
    depth_lookup_nearest_age_ms: int | None = None

    # ── Edge / PnL ───────────────────────────────────────────────────────────
    gross_edge: float | None = None
    net_edge_after_cost: float | None = None
    edge_implied_pnl_usdc: float | None = None
    mark_to_market_value_usdc: float | None = None
    mark_to_market_pnl_usdc: float | None = None
    realised_pnl_usdc: float | None = None
    resolution_outcome: Literal["yes", "no", "unresolved"] | None = None
    resolution_ts_ms: int | None = None

    # ── Percentage metrics (the headline ranking columns) ────────────────────
    edge_implied_return_pct: float | None = None
    mark_to_market_return_pct: float | None = None
    realised_return_pct: float | None = None
    trade_cost_adjusted_return_pct: float | None = None

    # ── Neutral review fields (NO promotion / demotion / kill) ───────────────
    review_status: ReviewStatus = "unreviewed"
    review_notes: str = ""
    observed_failure_mode: str = ""
    observed_pattern_tag: str = ""
    needs_manual_review: bool = False

    # ── Audit / labelling ────────────────────────────────────────────────────
    label: str = "STANDARDISED_BACKTEST_v1 — evidence capture only, not trading advice"
    schema_version: int = SCHEMA_VERSION
    ingested_ts_ms: int = 0


class StandardisedRunManifest(BaseModel):
    """Manifest describing one standardised backtest run."""

    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    run_id: str
    created_ts_ms: int
    label: str = (
        "STANDARDISED_BACKTEST_v1 — evidence capture only; no automatic promotion / demotion."
    )

    # Lane descriptions
    rulebook_lane_subruns: list[dict] = Field(default_factory=list)
    ai_freereign_lane_subruns: list[dict] = Field(default_factory=list)
    embedding_only_subruns: list[dict] = Field(default_factory=list)
    closed_form_simulator_subruns: list[dict] = Field(default_factory=list)
    control_subruns: list[dict] = Field(default_factory=list)
    strict_validation_subruns: list[dict] = Field(default_factory=list)
    diagnostic_subruns: list[dict] = Field(default_factory=list)

    # Inputs
    data_root: str = ""
    config_overrides: dict = Field(default_factory=dict)
    deepseek_workflow_config: dict = Field(default_factory=dict)

    # Output pack
    output_dir: str = ""
    output_files: list[str] = Field(default_factory=list)

    # Counts
    total_trades: int = 0
    trades_by_lane: dict[str, int] = Field(default_factory=dict)
    deepseek_hypotheses_with_justification: int = 0
    deepseek_trades_with_justification: int = 0
