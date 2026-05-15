"""``MarketSemantics`` Pydantic v2 schema — strict, frozen, no extras.

Field set follows the approved Phase 1.5 plan. The temporal-resolution
fields (``temporal_phrase``, ``temporal_phrase_normalized``,
``temporal_resolution``, ``exact_deadline_ms``) are deliberately structured
so the deterministic ``validators.detect_temporal_phrase`` can override
the model's claims.

Phase 5.5 D+ adds terms-aware fields: ``event_atoms``, ``proposition``,
``outcome_space``, plus tie/resolution/horizon flags for temporal
relationship mining.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MarketType = Literal[
    "binary", "multi_outcome", "scalar", "event_grouped", "unknown",
]
TemporalResolution = Literal[
    "exact_date", "month", "quarter", "year", "vague", "open_ended",
]
OutcomeSpaceKind = Literal[
    "single_winner_competition", "binary_event", "temporal_order",
    "threshold", "other",
]
PropositionType = Literal[
    "temporal_order", "threshold_comparison", "categorical_outcome", "other",
]
TemporalRelation = Literal["before", "after", "by_date", "simultaneous", "unknown"]


class EventAtom(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    event_id: str
    subject: str
    event_type: str
    definition: str | None = None
    source_of_truth: str | None = None
    ambiguity_flags: list[str] = Field(default_factory=list)


class Proposition(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    type: PropositionType
    left_event: str | None = None
    relation: TemporalRelation | None = None
    right_event: str | None = None
    strictness: str | None = None  # e.g. "strict_before", "on_or_before"


class OutcomeSpace(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    kind: OutcomeSpaceKind
    competition_id: str | None = None   # normalised key e.g. "nhl_stanley_cup_2026"
    candidate: str | None = None        # the specific entrant for this market
    winner_predicate: str | None = None # e.g. "win", "champion"


class MarketSemantics(BaseModel):
    """The structured object the LLM must emit. ``model_config`` is strict:
    extra fields are rejected, fields are immutable, and types are not
    coerced silently — Pydantic raises ``ValidationError`` on any mismatch.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=False)

    # ─── echoed input ───────────────────────────────────────────────────
    source_market_id: str
    source_condition_id: str | None = None
    question: str

    # ─── normalisation ──────────────────────────────────────────────────
    canonical_question: str
    market_type: MarketType
    subject_entities: list[str] = Field(default_factory=list)
    event_entities: list[str] = Field(default_factory=list)

    # ─── temporal (the load-bearing ambiguity defense) ──────────────────
    temporal_phrase: str | None = None
    temporal_phrase_normalized: str | None = None
    temporal_resolution: TemporalResolution = "vague"
    exact_deadline_ms: int | None = None
    date_constraints: dict = Field(default_factory=dict)

    # ─── jurisdiction & resolution logic ────────────────────────────────
    jurisdiction: str | None = None
    positive_resolution_condition: str
    negative_resolution_condition: str
    necessary_conditions_for_yes: list[str] = Field(default_factory=list)
    sufficient_conditions_for_yes: list[str] = Field(default_factory=list)
    necessary_conditions_for_no: list[str] = Field(default_factory=list)
    sufficient_conditions_for_no: list[str] = Field(default_factory=list)
    evidence_required: list[str] = Field(default_factory=list)

    # ─── candidate ambiguity flags (rulebook scores in Phase 1.6) ───────
    ambiguity_flags: list[str] = Field(default_factory=list)
    semantic_confidence: float = Field(ge=0.0, le=1.0)
    needs_manual_review: bool = False

    # ─── controlled advisory explanations (NOT chain-of-thought) ────────
    explanation_summary: str | None = None
    flag_rationales: dict[str, str] = Field(default_factory=dict)
    uncertainty_notes: list[str] = Field(default_factory=list)
    rule_curation_notes: list[str] = Field(default_factory=list)

    # ─── Phase 5.5 D+ terms-aware fields (all optional; null if LLM cannot extract) ─
    event_atoms: list[EventAtom] = Field(default_factory=list)
    proposition: Proposition | None = None
    outcome_space: OutcomeSpace | None = None
    tie_rule: str | None = None
    if_event_never_occurs_rule: str | None = None
    resolution_source: str | None = None
    timezone_or_boundary: str | None = None
    terms_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    long_horizon: bool = False
    unresolved_reference_event: bool = False
