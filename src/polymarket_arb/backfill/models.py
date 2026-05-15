"""Config and result dataclasses for the backfill pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel


class BackfillConfig(BaseModel):
    requested_days: int = 180
    market_limit: int = 200
    interval: str = "1h"
    fidelity_minutes: int | None = None
    include_active: bool = True
    include_recently_resolved: bool = True
    min_coverage_score: float = 0.6
    min_price_points: int = 50
    batch_size: int = 20  # max token IDs per batch-prices-history call


@dataclass
class BackfillResult:
    markets_attempted: int = 0
    markets_succeeded: int = 0
    markets_failed: int = 0
    total_rows_written: int = 0
    skipped: list[dict] = field(default_factory=list)  # [{market_id, reason}]


@dataclass
class SemanticPipelineResult:
    total_processed: int = 0
    total_skipped: int = 0
    semantics_extracted: int = 0
    semantics_failed: int = 0
    scores_computed: int = 0
    implications_extracted: int = 0
    errors: list[dict] = field(default_factory=list)  # [{extraction_id, reason}]
