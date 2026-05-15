from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FusionInputs:
    market_id: str
    market_midpoint: float | None
    spread: float | None
    liquidity_score: float
    semantic_confidence: float
    ambiguity_score: float
    implication_quality_score: float
    resolution_risk_score: float
    evidence_quality_score: float
    freshness_score: float
