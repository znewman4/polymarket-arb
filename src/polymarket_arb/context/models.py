"""Models and vocabularies for Phase 5.6 context rules."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTEXT_STATUSES = {
    "context_not_required",
    "context_missing",
    "context_extracted_unreviewed",
    "context_reviewed_valid",
    "context_reviewed_invalid",
}

STRATEGY_LANES = {
    "strict_context_valid",
    "reviewed_context_valid",
    "exploratory_context_unreviewed",
    "research_only",
    "analysis_only",
}

COMPLETENESS_CLASSES = {
    "known_complete",
    "known_partial",
    "open_world",
    "binary_complement",
    "partition_subset",
}

ContextStatus = Literal[
    "context_not_required",
    "context_missing",
    "context_extracted_unreviewed",
    "context_reviewed_valid",
    "context_reviewed_invalid",
]
StrategyLane = Literal[
    "strict_context_valid",
    "reviewed_context_valid",
    "exploratory_context_unreviewed",
    "research_only",
    "analysis_only",
]
CompletenessClass = Literal[
    "known_complete",
    "known_partial",
    "open_world",
    "binary_complement",
    "partition_subset",
]
ContextTimeMode = Literal["ex_post_research", "historical_replay_safe"]


class SourceRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_source_tier_for_strict: int = Field(default=1, ge=1, le=4)
    allowed_source_tiers: list[int] = Field(default_factory=lambda: [1, 2, 3])
    required_domains: list[str] = Field(default_factory=list)
    preferred_domains: list[str] = Field(default_factory=list)


class ContextSpace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_space_id: str
    display_name: str
    context_type: str
    completeness_class: CompletenessClass
    source_requirements: SourceRequirements = Field(default_factory=SourceRequirements)
    required_world_rule_types: list[str] = Field(default_factory=list)
    required_market_terms_rule_types: list[str] = Field(default_factory=list)
    allowed_strategy_lanes: list[StrategyLane] = Field(default_factory=list)
    deterministic_implications: list[str] = Field(default_factory=list)
    deterministic_exclusions: list[str] = Field(default_factory=list)
    human_review_required: bool = True
    notes: str = ""


class ContextRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_spaces: dict[str, ContextSpace]


class RuleValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_rule_id: str
    valid: bool
    status: ContextStatus
    reason: str


class ContextDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship_id: str
    context_space_id: str
    context_rule_ids: list[str]
    previous_validation_status: str
    new_validation_status: str
    previous_strategy_eligibility: str
    new_strategy_eligibility: str
    strategy_lane: StrategyLane
    decision_reason: str
    evidence_summary: str
    context_status: ContextStatus
    completeness_class: CompletenessClass
