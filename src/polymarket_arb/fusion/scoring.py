"""Deterministic research-only fusion scoring."""

from __future__ import annotations

import json
import time

from ..semantics.evidence_fusion_scorer import weighted_score
from ..semantics.rulebook_models import EvidenceFusionRulebook
from ..storage.base import MarketScoreRow
from .models import FusionInputs


def score_market(inputs: FusionInputs, rulebook: EvidenceFusionRulebook) -> MarketScoreRow:
    score_inputs = {
        "semantic_confidence": inputs.semantic_confidence,
        "ambiguity_score": inputs.ambiguity_score,
        "implication_quality_score": inputs.implication_quality_score,
        "liquidity_score": inputs.liquidity_score,
        "freshness_score": inputs.freshness_score,
        "spread_score": _spread_score(inputs.spread),
        "evidence_quality_score": inputs.evidence_quality_score,
    }
    final = weighted_score(score_inputs, rulebook)
    rec = _recommendation(final, rulebook.recommendation_thresholds)
    return MarketScoreRow(
        market_id=inputs.market_id,
        model_probability_placeholder=None,
        market_midpoint=inputs.market_midpoint,
        spread=inputs.spread,
        liquidity_score=inputs.liquidity_score,
        semantic_confidence=inputs.semantic_confidence,
        ambiguity_score=inputs.ambiguity_score,
        implication_quality_score=inputs.implication_quality_score,
        resolution_risk_score=inputs.resolution_risk_score,
        evidence_quality_score=inputs.evidence_quality_score,
        freshness_score=inputs.freshness_score,
        final_signal_score=final,
        recommendation=rec,
        explanation_json=json.dumps(score_inputs, sort_keys=True),
        rulebook_id=rulebook.rulebook_id,
        rulebook_version=rulebook.rulebook_version,
        schema_version=1,
        ingested_ts_ms=int(time.time() * 1000),
    )


def _spread_score(spread: float | None) -> float:
    if spread is None:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (spread / 0.25)))


def _recommendation(score: float, thresholds: dict[str, float]) -> str:
    allowed = ["ignore", "watch", "research", "paper_signal_only"]
    current = "ignore"
    for name in allowed:
        if score >= thresholds.get(name, 1.0):
            current = name
    return current
