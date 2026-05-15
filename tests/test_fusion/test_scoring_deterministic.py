from __future__ import annotations

from polymarket_arb.fusion.models import FusionInputs
from polymarket_arb.fusion.scoring import score_market
from polymarket_arb.semantics.rulebook import load_rulebook
from polymarket_arb.semantics.rulebook_models import EvidenceFusionRulebook


def test_fixed_inputs_produce_fixed_score():
    rb = load_rulebook(__import__("pathlib").Path("configs/semantic_rules/evidence_fusion_v1.yaml"), kind="evidence_fusion")
    assert isinstance(rb, EvidenceFusionRulebook)
    inputs = FusionInputs(
        market_id="m1",
        market_midpoint=0.5,
        spread=0.04,
        liquidity_score=0.8,
        semantic_confidence=0.7,
        ambiguity_score=0.2,
        implication_quality_score=0.6,
        resolution_risk_score=0.8,
        evidence_quality_score=0.5,
        freshness_score=1.0,
    )
    a = score_market(inputs, rb)
    b = score_market(inputs, rb)
    assert a.final_signal_score == b.final_signal_score
    assert a.recommendation in {"ignore", "watch", "research", "paper_signal_only"}
