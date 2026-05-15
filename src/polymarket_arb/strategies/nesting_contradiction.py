"""Signal evaluation for nesting/contradiction/inverse strategy.

Closed enumeration of all representable trade structures on Polymarket
(both sides are always 'buy' — no shorting).

| Type                         | Violation condition               | Buy on A | Buy on B |
|------------------------------|-----------------------------------|----------|----------|
| nested_a_implies_b           | P(A) > P(B) + edge                | NO_A     | YES_B    |
| nested_b_implies_a           | P(B) > P(A) + edge                | YES_A    | NO_B     |
| contradiction                | P(A) + P(B) > 1 + edge            | NO_A     | NO_B     |
| inverse (overround)          | P(A) + P(B) > 1 + edge            | NO_A     | NO_B     |
| inverse (underround)         | P(A) + P(B) < 1 - edge            | YES_A    | YES_B    |
| mutually_exclusive           | P(A) + P(B) > 1 + edge (pairwise) | NO_A     | NO_B     |
| same_entity_exclusive        | P(A) + P(B) > 1 + edge (pairwise) | NO_A     | NO_B     |
| mutually_exclusive_category  | P(A) + P(B) > 1 + edge (pairwise) | NO_A     | NO_B     |
| inverse_temporal_order ovr   | P(A) + P(B) > 1 + edge            | NO_A     | NO_B     |
| inverse_temporal_order und   | P(A) + P(B) < 1 - edge            | YES_A    | YES_B    |
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from ..storage.base import RelationshipCandidateRow, StrategyCandidateRow


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


@dataclass(frozen=True)
class AlignedPricePoint:
    ts_ms: int
    price_a: Decimal
    price_b: Decimal
    price_a_ts_ms: int   # actual timestamp of the price_a snapshot
    price_b_ts_ms: int


def _candidate_id(relationship_id: str, signal_ts_ms: int, token_id_a: str, token_id_b: str) -> str:
    raw = f"{relationship_id}|{signal_ts_ms}|{token_id_a}|{token_id_b}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _rejection_row(
    rel: RelationshipCandidateRow,
    point: AlignedPricePoint,
    run_id: str,
    token_id_a: str,
    token_id_b: str,
    reason: str,
    execution_model: str,
) -> StrategyCandidateRow:
    ts = _now_ms()
    return StrategyCandidateRow(
        candidate_id=_candidate_id(rel.relationship_id, point.ts_ms, token_id_a, token_id_b),
        run_id=run_id,
        relationship_id=rel.relationship_id,
        market_id_a=rel.market_id_a,
        market_id_b=rel.market_id_b,
        token_id_a=token_id_a,
        token_id_b=token_id_b,
        relationship_type=rel.relationship_type,
        signal_ts_ms=point.ts_ms,
        price_a=point.price_a,
        price_b=point.price_b,
        price_a_ts_ms=point.price_a_ts_ms,
        price_b_ts_ms=point.price_b_ts_ms,
        inequality_violated="",
        theoretical_edge=Decimal("0"),
        gross_edge=Decimal("0"),
        estimated_fee=Decimal("0"),
        estimated_slippage=Decimal("0"),
        net_edge_after_costs=Decimal("0"),
        execution_model=execution_model,
        execution_model_confidence=0.0,
        accepted_for_simulation=False,
        rejection_reason=reason,
        simulated_position_json="[]",
        stake_usdc=Decimal("0"),
        expected_payout_json="{}",
        schema_version=1,
        ingested_ts_ms=ts,
    )


def evaluate_relationship_at_tick(
    rel: RelationshipCandidateRow,
    point: AlignedPricePoint,
    run_id: str,
    min_gross_edge: float,
    fee_bps: Decimal,
    slippage_bps: Decimal,
    min_net_edge: float,
    execution_model: str = "price_history_only",
    execution_model_confidence: float = 0.4,
    stake_usdc: Decimal = Decimal("250"),
) -> StrategyCandidateRow | None:
    """Evaluate one relationship at one price tick.

    Returns None if no violation at this tick (common, no row needed).
    Returns a StrategyCandidateRow (accepted or rejected) if there is a potential violation.
    """
    rt = rel.relationship_type
    pa = point.price_a
    pb = point.price_b

    # Determine (token_id_a, token_id_b, inequality, theoretical_edge)
    token_id_a: str | None = None
    token_id_b: str | None = None
    inequality_str = ""
    theoretical_edge = Decimal("0")

    if rt == "nested_a_implies_b":
        # A implies B → P(A) ≤ P(B). Violation: P(A) > P(B) + edge
        edge = pa - pb
        if edge <= Decimal(str(min_gross_edge)):
            return None
        token_id_a = rel.token_id_a_no   # buy NO_A
        token_id_b = rel.token_id_b_yes  # buy YES_B
        inequality_str = f"P(A)={float(pa):.4f} > P(B)={float(pb):.4f}"
        theoretical_edge = edge

    elif rt == "nested_b_implies_a":
        # B implies A → P(B) ≤ P(A). Violation: P(B) > P(A) + edge
        edge = pb - pa
        if edge <= Decimal(str(min_gross_edge)):
            return None
        token_id_a = rel.token_id_a_yes  # buy YES_A
        token_id_b = rel.token_id_b_no   # buy NO_B
        inequality_str = f"P(B)={float(pb):.4f} > P(A)={float(pa):.4f}"
        theoretical_edge = edge

    elif rt in ("contradiction", "mutually_exclusive", "same_entity_exclusive",
                "mutually_exclusive_category"):
        # Cannot both be true → P(A) + P(B) ≤ 1. Violation: sum > 1 + edge
        s = pa + pb
        edge = s - Decimal("1")
        if edge <= Decimal(str(min_gross_edge)):
            return None
        token_id_a = rel.token_id_a_no
        token_id_b = rel.token_id_b_no
        inequality_str = f"P(A)+P(B)={float(s):.4f} > 1"
        theoretical_edge = edge

    elif rt in ("inverse", "inverse_temporal_order"):
        s = pa + pb
        if s > Decimal("1") + Decimal(str(min_gross_edge)):
            # Overround: buy NO_A + NO_B
            token_id_a = rel.token_id_a_no
            token_id_b = rel.token_id_b_no
            theoretical_edge = s - Decimal("1")
            inequality_str = f"P(A)+P(B)={float(s):.4f} > 1 (overround)"
        elif s < Decimal("1") - Decimal(str(min_gross_edge)):
            # Underround: buy YES_A + YES_B
            token_id_a = rel.token_id_a_yes
            token_id_b = rel.token_id_b_yes
            theoretical_edge = Decimal("1") - s
            inequality_str = f"P(A)+P(B)={float(s):.4f} < 1 (underround)"
        else:
            return None
    else:
        return None

    # Guard: missing token IDs
    if not token_id_a or not token_id_b:
        return _rejection_row(
            rel, point, run_id,
            token_id_a or "", token_id_b or "",
            "unsupported_trade_structure",
            execution_model,
        )

    # Compute costs
    notional = stake_usdc  # approximate: assume price x shares = stake
    total_bps = fee_bps + slippage_bps
    cost_fraction = total_bps / Decimal("10000")
    total_cost = notional * cost_fraction * Decimal("2")  # two legs
    per_unit_cost = total_cost / notional if notional > 0 else Decimal("0")

    gross_edge = theoretical_edge
    net_edge = gross_edge - per_unit_cost

    estimated_fee = notional * (fee_bps / Decimal("10000")) * 2
    estimated_slippage = notional * (slippage_bps / Decimal("10000")) * 2

    accepted = net_edge >= Decimal(str(min_net_edge))
    rejection_reason: str | None = None
    if not accepted:
        rejection_reason = f"net_edge={float(net_edge):.4f} < min={min_net_edge}"

    position = [
        {"token_id": token_id_a, "shares": str(stake_usdc / pa if pa > 0 else 0), "side": "buy"},
        {"token_id": token_id_b, "shares": str(stake_usdc / pb if pb > 0 else 0), "side": "buy"},
    ]

    ts = _now_ms()
    return StrategyCandidateRow(
        candidate_id=_candidate_id(rel.relationship_id, point.ts_ms, token_id_a, token_id_b),
        run_id=run_id,
        relationship_id=rel.relationship_id,
        market_id_a=rel.market_id_a,
        market_id_b=rel.market_id_b,
        token_id_a=token_id_a,
        token_id_b=token_id_b,
        relationship_type=rt,
        signal_ts_ms=point.ts_ms,
        price_a=pa,
        price_b=pb,
        price_a_ts_ms=point.price_a_ts_ms,
        price_b_ts_ms=point.price_b_ts_ms,
        inequality_violated=inequality_str,
        theoretical_edge=theoretical_edge,
        gross_edge=gross_edge,
        estimated_fee=estimated_fee,
        estimated_slippage=estimated_slippage,
        net_edge_after_costs=net_edge,
        execution_model=execution_model,
        execution_model_confidence=execution_model_confidence,
        accepted_for_simulation=accepted,
        rejection_reason=rejection_reason,
        simulated_position_json=json.dumps(position),
        stake_usdc=stake_usdc,
        expected_payout_json=json.dumps({}),
        schema_version=1,
        ingested_ts_ms=ts,
    )
