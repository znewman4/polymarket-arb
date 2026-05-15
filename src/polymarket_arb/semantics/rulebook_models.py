"""Pydantic models for versioned YAML rulebooks."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RulebookBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rulebook_id: str
    rulebook_version: int = Field(ge=1)
    description: str | None = None


class AmbiguityRulebook(RulebookBase):
    flag_severities: dict[str, float]
    combination_rule: Literal["max_then_average"]
    review_threshold: float = Field(ge=0.0, le=1.0)


class ImplicationRulebook(RulebookBase):
    type_weights: dict[str, float]
    ambiguity_penalty_weight: float = Field(ge=0.0, le=1.0)
    review_threshold: float = Field(ge=0.0, le=1.0)


class ContradictionRulebook(RulebookBase):
    pair_type_weights: dict[str, float]
    review_threshold: float = Field(ge=0.0, le=1.0)


class EvidenceFusionRulebook(RulebookBase):
    weights: dict[str, float]
    recommendation_thresholds: dict[str, float]


class RelationshipHardGates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    min_entity_match: float = Field(ge=0.0, le=1.0)
    min_time_scope_match: float = Field(ge=0.0, le=1.0)
    forbid_embedding_only: bool = True
    require_token_ids_when_strategy_eligible: bool = True


class RelationshipSoftGates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    min_resolution_criteria_match: float = Field(ge=0.0, le=1.0)
    min_final_confidence_for_accept: float = Field(ge=0.0, le=1.0)
    manual_review_band: tuple[float, float]


class RelationshipTypeRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # v1 fields
    requires_threshold_relation: bool = False
    requires_same_variable: bool = False
    requires_same_entity: bool = False  # changed default: type-specific handling takes over
    requires_overlapping_time_scope: bool = False
    requires_same_time_scope: bool = False
    must_partition_outcome_space: bool = False
    # v2 type-specific gates (all optional, default False/None)
    ignore_min_entity_match: bool = False
    require_same_outcome_space: bool = False
    require_different_candidates: bool = False
    require_same_winner_predicate: bool = False
    require_same_or_compatible_time_scope: bool = False
    min_event_match: float | None = None
    min_terms_match: float | None = None
    requires_extractable_event_atoms: bool = False
    requires_terms_compatible: bool = False
    allow_different_entities: bool = False
    min_reference_event_match: float | None = None
    unresolved_reference_event_penalty: float = 0.0
    long_horizon_penalty: float = 0.0
    requires_same_two_events: bool = False
    requires_opposite_ordering: bool = False
    requires_three_event_chain: bool = False
    requires_shared_reference_event: bool = False
    requires_same_event_space: bool = False
    requires_mutually_impossible_yes_conditions: bool = False


class RelationshipAmbiguityPenalties(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    semantics_ambiguity_flag_present: float = 0.10
    needs_manual_review_either_market: float = 0.20
    low_semantic_confidence_either_market: float = 0.10


class RelationshipRulebook(RulebookBase):
    score_weights: dict[str, float]
    hard_gates: RelationshipHardGates
    soft_gates: RelationshipSoftGates
    type_rules: dict[str, RelationshipTypeRule]
    ambiguity_penalties: RelationshipAmbiguityPenalties
    rejection_codes: list[str]
