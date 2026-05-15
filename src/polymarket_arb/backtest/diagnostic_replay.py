"""Diagnostic bypass backtest for context-aware semantic relationships.

RESEARCH-ONLY / DIAGNOSTIC-ONLY. Not trading advice.

All runs are labelled ``diagnostic_only_not_credible`` regardless of PnL.
This module exists to answer two questions:
  1. Why did 21/22 auto-approved relationships fail data coverage?
  2. Can the simulated wallet open, hold, mark, close, and value positions
     using the current semantic relationships once coverage bypasses are on?

Bypass flags are mutually independent and additive:
  bypass_review_lane_checks   — accept all relationships regardless of lane/eligibility
  bypass_min_confidence       — treat confidence threshold as 0.0
  bypass_coverage_threshold   — skip coverage score / recommended_for_backtest
  include_all_validation_statuses — include rejected/needs_manual_review relationships
"""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..storage.base import (
    ContextRelationshipDecisionRow,
    PriceHistoryRow,
    RelationshipCandidateRow,
)
from ..storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from ..storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from ..storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from ..storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)
from ..strategies.models import DiagnosticBacktestConfig
from ..strategies.nesting_contradiction import AlignedPricePoint, evaluate_relationship_at_tick
from .cost_model import estimate_costs
from .price_alignment import align_price_series

_LABEL = "diagnostic_only_not_credible"
_NOTE = "RESEARCH-ONLY / DIAGNOSTIC-ONLY. Not trading advice."

_SUM_BASED_TYPES = frozenset({
    "mutually_exclusive_category", "contradiction", "mutually_exclusive",
    "same_entity_exclusive", "inverse", "inverse_temporal_order",
})


def _pair_is_economically_viable(
    rel: RelationshipCandidateRow,
    rows_a: list[PriceHistoryRow],
    rows_b: list[PriceHistoryRow],
    *,
    min_combined: float,
    min_single: float,
) -> bool:
    if rel.relationship_type not in _SUM_BASED_TYPES:
        return True
    if min_combined <= 0.0 and min_single <= 0.0:
        return True
    sorted_a = sorted(rows_a, key=lambda r: r.ts_ms)
    sorted_b = sorted(rows_b, key=lambda r: r.ts_ms)
    for ra, rb in zip(sorted_a, sorted_b, strict=False):
        pa, pb = float(ra.price), float(rb.price)
        if min_combined > 0 and pa + pb >= min_combined:
            return True
        if min_single > 0 and (pa >= min_single or pb >= min_single):
            return True
    return False


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# ── per-relationship open position ───────────────────────────────────────────


@dataclass
class _DiagPosition:
    position_id: str
    relationship_id: str
    open_ts_ms: int
    leg_a_token_id: str
    leg_b_token_id: str
    leg_a_market_id: str
    leg_b_market_id: str
    leg_a_shares: Decimal
    leg_b_shares: Decimal
    leg_a_entry_price: Decimal
    leg_b_entry_price: Decimal
    leg_a_entry_cost: Decimal
    leg_b_entry_cost: Decimal
    total_cost_usdc: Decimal


# ── main entry point ─────────────────────────────────────────────────────────


def run_diagnostic_backtest(
    data_root: Path,
    cfg: DiagnosticBacktestConfig,
) -> dict[str, Any]:
    """Run the diagnostic bypass backtest.

    RESEARCH-ONLY / DIAGNOSTIC-ONLY. Always returns
    credibility_label = 'diagnostic_only_not_credible'.
    """
    run_id = cfg.run_id or uuid.uuid4().hex
    cfg = cfg.model_copy(update={"run_id": run_id})
    out_dir = data_root / "backtests" / run_id / "diagnostic"
    out_dir.mkdir(parents=True, exist_ok=True)

    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    decision_repo = ParquetContextRelationshipDecisionsRepository(data_root)
    price_repo = ParquetPriceHistoryRepository(data_root)
    coverage_repo = ParquetBackfillCoverageRepository(data_root)

    all_decisions = {d.relationship_id: d for d in decision_repo.iter_latest()}
    all_coverage = {c.market_id: c for c in coverage_repo.iter_latest()}

    if cfg.include_all_validation_statuses:
        all_rels = list(rel_repo.iter_latest())
    else:
        all_rels = [r for r in rel_repo.iter_latest() if r.validation_status == "accepted"]

    funnel: dict[str, Any] = _new_funnel(run_id, cfg)
    funnel["counts"]["relationships_loaded"] = len(all_rels)

    cash = cfg.starting_cash_usdc
    open_positions: dict[str, _DiagPosition] = {}
    fills_open: list[dict[str, Any]] = []
    fills_close: list[dict[str, Any]] = []
    per_rel_funnel: dict[str, dict[str, Any]] = {}
    total_fees = Decimal("0")
    total_slippage = Decimal("0")

    # Pre-fetch all price history in one bulk scan (vastly faster than per-token queries)
    all_yes_tokens: list[str] = []
    for r in all_rels:
        if r.token_id_a_yes:
            all_yes_tokens.append(r.token_id_a_yes)
        if r.token_id_b_yes:
            all_yes_tokens.append(r.token_id_b_yes)

    price_cache: dict[str, list[PriceHistoryRow]] = {}
    for row in price_repo.iter_for_tokens(all_yes_tokens):
        price_cache.setdefault(row.token_id, []).append(row)

    def _prices(token_id: str) -> list[PriceHistoryRow]:
        return price_cache.get(token_id, [])

    for rel in all_rels:
        decision = all_decisions.get(rel.relationship_id)
        rel_id = rel.relationship_id

        lane = decision.strategy_lane if decision else "unassigned"
        per_rel_funnel[rel_id] = _new_rel_entry(rel, lane)

        # ── filter: review lane / eligibility ────────────────────────────────
        if not cfg.bypass_review_lane_checks:
            if decision is None:
                _inc(funnel, "rejected_no_decision")
                per_rel_funnel[rel_id]["final_blocker"] = "no_context_decision"
                continue
            if decision.new_strategy_eligibility != "eligible":
                _inc(funnel, "rejected_by_context")
                per_rel_funnel[rel_id]["final_blocker"] = (
                    f"not_eligible:{decision.new_strategy_eligibility}"
                )
                continue

        # ── filter: confidence ───────────────────────────────────────────────
        eff_conf = cfg.effective_min_confidence
        if rel.final_confidence < eff_conf:
            _inc(funnel, "rejected_by_confidence")
            per_rel_funnel[rel_id]["final_blocker"] = (
                f"confidence_below_threshold ({rel.final_confidence:.2f} < {eff_conf:.2f})"
            )
            continue

        # ── require yes tokens ───────────────────────────────────────────────
        if not rel.token_id_a_yes or not rel.token_id_b_yes:
            _inc(funnel, "rejected_missing_tokens")
            per_rel_funnel[rel_id]["final_blocker"] = "missing_yes_tokens"
            continue

        # ── filter: coverage ─────────────────────────────────────────────────
        cov_a = all_coverage.get(rel.market_id_a)
        cov_b = all_coverage.get(rel.market_id_b)
        score_a = float(cov_a.coverage_score) if cov_a else 0.0
        score_b = float(cov_b.coverage_score) if cov_b else 0.0
        score_pair = min(score_a, score_b)
        per_rel_funnel[rel_id]["coverage_score_pair"] = score_pair

        if not cfg.bypass_coverage_threshold:
            eff_cov = cfg.effective_min_coverage_score
            if not cov_a or not cov_a.recommended_for_backtest or score_a < eff_cov:
                _inc(funnel, "rejected_by_coverage")
                per_rel_funnel[rel_id]["final_blocker"] = (
                    f"coverage_a_insufficient (score={score_a:.3f})"
                )
                continue
            if not cov_b or not cov_b.recommended_for_backtest or score_b < eff_cov:
                _inc(funnel, "rejected_by_coverage")
                per_rel_funnel[rel_id]["final_blocker"] = (
                    f"coverage_b_insufficient (score={score_b:.3f})"
                )
                continue

        # ── price history ────────────────────────────────────────────────────
        rows_a = _prices(rel.token_id_a_yes)
        rows_b = _prices(rel.token_id_b_yes)
        ph_a = bool(rows_a)
        ph_b = bool(rows_b)
        per_rel_funnel[rel_id]["has_price_history"] = ph_a and ph_b

        if not ph_a or not ph_b:
            _inc(funnel, "rejected_no_price_history")
            if not ph_a:
                per_rel_funnel[rel_id]["final_blocker"] = (
                    f"no_price_history_a (token={rel.token_id_a_yes})"
                )
            else:
                per_rel_funnel[rel_id]["final_blocker"] = (
                    f"no_price_history_b (token={rel.token_id_b_yes})"
                )
            continue

        _inc(funnel, "price_history_present")

        if not _pair_is_economically_viable(
            rel, rows_a, rows_b,
            min_combined=cfg.min_combined_prob_for_pairwise,
            min_single=cfg.min_single_prob_for_pairwise,
        ):
            _inc(funnel, "rejected_by_pair_viability")
            per_rel_funnel[rel_id]["final_blocker"] = "real_relationship_but_pairwise_not_tradeable"
            continue

        # ── align price series ───────────────────────────────────────────────
        aligned = align_price_series(
            rows_a,
            rows_b,
            start_ts_ms=cfg.start_ts_ms,
            end_ts_ms=cfg.end_ts_ms,
            signal_interval_ms=cfg.signal_interval_ms,
            staleness_limit_ms=cfg.quote_staleness_limit_ms,
        )

        if not aligned:
            _inc(funnel, "rejected_alignment_failed")
            per_rel_funnel[rel_id]["final_blocker"] = "alignment_failed"
            continue

        per_rel_funnel[rel_id]["tick_count"] = len(aligned)
        _inc(funnel, "aligned_price_series")

        # ── tick loop ────────────────────────────────────────────────────────
        for point in aligned:
            _inc(funnel, "ticks_evaluated")

            candidate = evaluate_relationship_at_tick(
                rel=rel,
                point=point,
                run_id=run_id,
                min_gross_edge=cfg.min_gross_edge,
                fee_bps=cfg.fee_bps,
                slippage_bps=cfg.slippage_bps,
                min_net_edge=cfg.min_net_edge,
                execution_model=cfg.execution_model,
                execution_model_confidence=0.4,
                stake_usdc=cfg.max_stake_per_trade_usdc,
            )

            if candidate is not None:
                _inc(funnel, "gross_violations")
                per_rel_funnel[rel_id]["gross_violations"] += 1

                if rel_id not in open_positions and candidate.accepted_for_simulation:
                    required = cfg.max_stake_per_trade_usdc * 2
                    if cash >= required or cfg.allow_negative_cash:
                        pos, leg_fills, leg_fees, leg_slippage = _open_position(
                            rel, decision, candidate, cfg, run_id
                        )
                        open_positions[rel_id] = pos
                        fills_open.extend(leg_fills)
                        cash -= pos.total_cost_usdc
                        total_fees += leg_fees
                        total_slippage += leg_slippage
                        per_rel_funnel[rel_id]["trades_opened"] += 1
                        _inc(funnel, "positions_opened")
            else:
                # No violation at this tick — close any open position (reversal)
                if rel_id in open_positions:
                    pos = open_positions.pop(rel_id)
                    close_row, realized = _close_position(
                        pos, point, rel, run_id, "reversal"
                    )
                    fills_close.append(close_row)
                    cash += pos.total_cost_usdc + realized
                    per_rel_funnel[rel_id]["trades_closed"] += 1
                    _add_realized(per_rel_funnel[rel_id], "realized_pnl_usdc", realized)
                    _inc(funnel, "positions_closed_reversal")

        # ── end-of-window: close remaining position for this relationship ────
        if rel_id in open_positions:
            pos = open_positions.pop(rel_id)
            if aligned:
                last_point = aligned[-1]
                close_row, realized = _close_position(
                    pos, last_point, rel, run_id, "end_of_window"
                )
                fills_close.append(close_row)
                cash += pos.total_cost_usdc + realized
                per_rel_funnel[rel_id]["trades_closed"] += 1
                _add_realized(per_rel_funnel[rel_id], "realized_pnl_usdc", realized)
                per_rel_funnel[rel_id]["mark_to_market_pnl_usdc"] = str(realized)
                _inc(funnel, "positions_closed_eow")

        if per_rel_funnel[rel_id]["final_blocker"] == "pending":
            if per_rel_funnel[rel_id]["gross_violations"] > 0:
                per_rel_funnel[rel_id]["final_blocker"] = "none"
            else:
                per_rel_funnel[rel_id]["final_blocker"] = "no_gross_violations"

    # ── aggregate metrics ─────────────────────────────────────────────────────
    net_pnl = cash - cfg.starting_cash_usdc
    per_rel_rows = list(per_rel_funnel.values())
    funnel["counts"]["net_pnl_usdc"] = str(net_pnl)
    funnel["credibility_label"] = _LABEL
    funnel["diagnostic_note"] = _NOTE

    metrics: dict[str, Any] = {
        "run_id": run_id,
        "label": _LABEL,
        "note": _NOTE,
        "bypass_review_lane_checks": cfg.bypass_review_lane_checks,
        "bypass_min_confidence": cfg.bypass_min_confidence,
        "bypass_coverage_threshold": cfg.bypass_coverage_threshold,
        "include_all_validation_statuses": cfg.include_all_validation_statuses,
        "starting_cash_usdc": str(cfg.starting_cash_usdc),
        "ending_cash_usdc": str(cash),
        "ending_equity_usdc": str(cash),
        "net_pnl_usdc": str(net_pnl),
        "total_fees_usdc": str(total_fees),
        "total_slippage_usdc": str(total_slippage),
        "relationships_considered": len(all_rels),
        "positions_opened": funnel["counts"].get("positions_opened", 0),
        "positions_closed_reversal": funnel["counts"].get("positions_closed_reversal", 0),
        "positions_closed_eow": funnel["counts"].get("positions_closed_eow", 0),
        "slippage_bps": str(cfg.slippage_bps),
        "credibility_label": _LABEL,
    }

    _write_json(out_dir / "config.json", cfg.model_dump(mode="json"))
    _write_json(out_dir / "metrics.json", metrics)
    _write_json(out_dir / "funnel.json", funnel)
    _write_csv(out_dir / "per_relationship_funnel.csv", per_rel_rows)
    _write_csv(out_dir / "fills_open.csv", fills_open)
    _write_csv(out_dir / "fills_close.csv", fills_close)
    _write_equity_curve(out_dir, cfg, cash)

    return {
        "run_id": run_id,
        "output_dir": out_dir,
        "metrics": metrics,
        "funnel": funnel,
        "per_rel_funnel": per_rel_rows,
    }


# ── wallet helpers ────────────────────────────────────────────────────────────


def _open_position(
    rel: RelationshipCandidateRow,
    decision: ContextRelationshipDecisionRow | None,
    candidate: Any,
    cfg: DiagnosticBacktestConfig,
    run_id: str,
) -> tuple[_DiagPosition, list[dict[str, Any]], Decimal, Decimal]:
    position_id = uuid.uuid4().hex
    lane = decision.strategy_lane if decision else "unassigned"
    context_space = decision.context_space_id if decision else ""
    fills = []
    total_fees = Decimal("0")
    total_slippage = Decimal("0")
    leg_a_cost = Decimal("0")
    leg_b_cost = Decimal("0")
    leg_a_fill_price = candidate.price_a
    leg_b_fill_price = candidate.price_b

    for leg_key, token_id, market_id, mid_price in [
        ("a", candidate.token_id_a, rel.market_id_a, candidate.price_a),
        ("b", candidate.token_id_b, rel.market_id_b, candidate.price_b),
    ]:
        costs = estimate_costs(
            notional_usdc=cfg.max_stake_per_trade_usdc,
            mid_price=mid_price,
            execution_model=cfg.execution_model,
            fee_bps=cfg.fee_bps,
            slippage_bps=cfg.slippage_bps,
        )
        shares = (
            cfg.max_stake_per_trade_usdc / costs.fill_price
            if costs.fill_price
            else Decimal("0")
        )
        cost = shares * costs.fill_price + costs.fee_usdc
        if leg_key == "a":
            leg_a_fill_price = costs.fill_price
            leg_a_cost = cost
        else:
            leg_b_fill_price = costs.fill_price
            leg_b_cost = cost
        total_fees += costs.fee_usdc
        total_slippage += costs.slippage_usdc
        fills.append({
            "fill_id": uuid.uuid4().hex,
            "position_id": position_id,
            "run_id": run_id,
            "relationship_id": rel.relationship_id,
            "context_space_id": context_space,
            "strategy_lane": lane,
            "leg": leg_key,
            "side": "buy",
            "token_id": token_id,
            "market_id": market_id,
            "fill_ts_ms": candidate.signal_ts_ms,
            "fill_price": str(costs.fill_price),
            "shares": str(shares),
            "notional_usdc": str(shares * costs.fill_price),
            "fees_usdc": str(costs.fee_usdc),
            "slippage_usdc": str(costs.slippage_usdc),
            "label": _LABEL,
        })

    leg_a_shares = Decimal(fills[0]["shares"])
    leg_b_shares = Decimal(fills[1]["shares"])

    pos = _DiagPosition(
        position_id=position_id,
        relationship_id=rel.relationship_id,
        open_ts_ms=candidate.signal_ts_ms,
        leg_a_token_id=candidate.token_id_a,
        leg_b_token_id=candidate.token_id_b,
        leg_a_market_id=rel.market_id_a,
        leg_b_market_id=rel.market_id_b,
        leg_a_shares=leg_a_shares,
        leg_b_shares=leg_b_shares,
        leg_a_entry_price=leg_a_fill_price,
        leg_b_entry_price=leg_b_fill_price,
        leg_a_entry_cost=leg_a_cost,
        leg_b_entry_cost=leg_b_cost,
        total_cost_usdc=leg_a_cost + leg_b_cost,
    )
    return pos, fills, total_fees, total_slippage


def _token_price_at(
    point: AlignedPricePoint,
    token_id: str,
    rel: RelationshipCandidateRow,
) -> Decimal:
    """Map a token_id back to its price at this tick using aligned YES prices."""
    if token_id == rel.token_id_a_yes:
        return point.price_a
    if token_id == rel.token_id_a_no:
        return Decimal("1") - point.price_a
    if token_id == rel.token_id_b_yes:
        return point.price_b
    if token_id == rel.token_id_b_no:
        return Decimal("1") - point.price_b
    return point.price_a  # fallback; only reached for unknown tokens


def _close_position(
    pos: _DiagPosition,
    point: AlignedPricePoint,
    rel: RelationshipCandidateRow,
    run_id: str,
    reason: str,
) -> tuple[dict[str, Any], Decimal]:
    close_price_a = _token_price_at(point, pos.leg_a_token_id, rel)
    close_price_b = _token_price_at(point, pos.leg_b_token_id, rel)
    proceeds_a = pos.leg_a_shares * close_price_a
    proceeds_b = pos.leg_b_shares * close_price_b
    realized = (proceeds_a - pos.leg_a_entry_cost) + (proceeds_b - pos.leg_b_entry_cost)
    close_row = {
        "fill_id": uuid.uuid4().hex,
        "position_id": pos.position_id,
        "run_id": run_id,
        "relationship_id": pos.relationship_id,
        "close_ts_ms": point.ts_ms,
        "close_reason": reason,
        "leg_a_token_id": pos.leg_a_token_id,
        "leg_b_token_id": pos.leg_b_token_id,
        "leg_a_close_price": str(close_price_a),
        "leg_b_close_price": str(close_price_b),
        "leg_a_shares": str(pos.leg_a_shares),
        "leg_b_shares": str(pos.leg_b_shares),
        "leg_a_entry_cost": str(pos.leg_a_entry_cost),
        "leg_b_entry_cost": str(pos.leg_b_entry_cost),
        "leg_a_proceeds": str(proceeds_a),
        "leg_b_proceeds": str(proceeds_b),
        "realized_pnl_usdc": str(realized),
        "label": _LABEL,
    }
    return close_row, realized


# ── funnel helpers ────────────────────────────────────────────────────────────


def _new_funnel(run_id: str, cfg: DiagnosticBacktestConfig) -> dict[str, Any]:
    keys = [
        "relationships_loaded",
        "rejected_no_decision",
        "rejected_by_context",
        "rejected_by_confidence",
        "rejected_missing_tokens",
        "rejected_by_coverage",
        "rejected_no_price_history",
        "rejected_alignment_failed",
        "price_history_present",
        "aligned_price_series",
        "ticks_evaluated",
        "gross_violations",
        "positions_opened",
        "positions_closed_reversal",
        "positions_closed_eow",
        "rejected_by_pair_viability",
    ]
    return {
        "run_id": run_id,
        "label": _LABEL,
        "bypass_review_lane_checks": cfg.bypass_review_lane_checks,
        "bypass_min_confidence": cfg.bypass_min_confidence,
        "bypass_coverage_threshold": cfg.bypass_coverage_threshold,
        "include_all_validation_statuses": cfg.include_all_validation_statuses,
        "counts": {k: 0 for k in keys},
    }


def _new_rel_entry(rel: RelationshipCandidateRow, lane: str) -> dict[str, Any]:
    return {
        "relationship_id": rel.relationship_id,
        "question_a": rel.question_a,
        "question_b": rel.question_b,
        "lane": lane,
        "validation_status": rel.validation_status,
        "strategy_eligibility_status": rel.strategy_eligibility_status,
        "final_confidence": rel.final_confidence,
        "coverage_score_pair": 0.0,
        "has_price_history": False,
        "tick_count": 0,
        "gross_violations": 0,
        "trades_opened": 0,
        "trades_closed": 0,
        "realized_pnl_usdc": "0",
        "mark_to_market_pnl_usdc": "0",
        "final_blocker": "pending",
        "label": _LABEL,
    }


def _inc(funnel: dict[str, Any], key: str, amount: int = 1) -> None:
    funnel["counts"][key] = int(funnel["counts"].get(key, 0)) + amount


def _add_realized(entry: dict[str, Any], key: str, amount: Decimal) -> None:
    entry[key] = str(Decimal(entry.get(key, "0")) + amount)


def _write_equity_curve(
    out_dir: Path,
    cfg: DiagnosticBacktestConfig,
    ending_cash: Decimal,
) -> None:
    row = {
        "ts_ms": _now_ms(),
        "cash_usdc": str(ending_cash),
        "equity_usdc": str(ending_cash),
        "label": _LABEL,
    }
    _write_csv(out_dir / "equity_curve.csv", [row])
    table = pa.Table.from_pylist([row])
    pq.write_table(table, out_dir / "equity_curve.parquet")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
