"""Configuration and result models for relationship mining."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class RelationshipMiningConfig(BaseModel):
    """Configuration for a relationship mining run."""
    max_candidates_per_market: int = Field(default=50, ge=1)
    max_total_candidates: int = Field(default=200_000, ge=1)
    embedding_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    time_window_days: int = Field(default=30, ge=1)
    market_limit: int | None = None
    use_embeddings: bool = False
    rulebook_filename: str = "relationship_v1.yaml"


@dataclass
class RelationshipMiningResult:
    total_candidates_considered: int = 0
    accepted: int = 0
    rejected: int = 0
    needs_manual_review: int = 0
    by_type: dict[str, int] = field(default_factory=dict)
    top_rejection_reasons: dict[str, int] = field(default_factory=dict)
    wall_clock_ms: int = 0
