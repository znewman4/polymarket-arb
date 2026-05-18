"""Context-aware relationship replay.

Research-only simulation: all legs are local buy-only YES/NO token accounting.
"""

from __future__ import annotations

import csv
import hashlib
import json
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..context.decision_engine import normalize_context_backed_relationship
from ..storage.base import (
    ContextRelationshipDecisionRow,
    PriceHistoryRow,
    RelationshipCandidateRow,
)
from ..storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from ..storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from ..storage.parquet.context_rules_repo import ParquetContextRulesRepository
from ..storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from ..strategies.models import ContextAwareBacktestConfig
from ..strategies.nesting_contradiction import evaluate_relationship_at_tick
from .cost_model import estimate_costs
from .price_alignment import align_price_series

LANES = {
    "strict_context_valid",
    "reviewed_context_valid",
    "exploratory_context_unreviewed",
    "exploratory_context_auto_approved",
    "all_context_research",
}

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


def run_context_aware_backtest(
    data_root: Path,
    cfg: ContextAwareBacktestConfig,
) -> dict[str, Any]:
    if not cfg.run_id:
        cfg = cfg.model_copy(update={"run_id": uuid.uuid4().hex})
    run_id = cfg.run_id
    lane_dir = data_root / "backtests" / run_id / "context_aware" / cfg.lane
    lane_dir.mkdir(parents=True, exist_ok=True)

    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    decision_repo = ParquetContextRelationshipDecisionsRepository(data_root)
    price_repo = ParquetPriceHistoryRepository(data_root)
    coverage_repo = ParquetBackfillCoverageRepository(data_root)
    rules_repo = ParquetContextRulesRepository(data_root)

    rels = {r.relationship_id: r for r in rel_repo.iter_latest()}
    coverage = {r.market_id: r for r in coverage_repo.iter_latest()}
    decisions = _filter_decisions(
        list(decision_repo.iter_latest()),
        cfg.lane,
        include_auto_approved=cfg.include_auto_approved,
        relationship_universe=cfg.relationship_universe,
    )
    rules = {r.context_rule_id: r for r in rules_repo.iter_latest()}

    funnel = _new_funnel(run_id, cfg)
    decisions = _filter_decisions_by_relationship_universe(decisions, rels, cfg.relationship_universe)
    funnel["counts"]["relationships_loaded"] = len(decisions)
    price_cache: dict[str, list[PriceHistoryRow]] = {}

    def prices(token_id: str) -> list[PriceHistoryRow]:
        if token_id not in price_cache:
            price_cache[token_id] = list(price_repo.iter_for_token(token_id))
        return price_cache[token_id]

    cash = cfg.starting_cash_usdc
    per_market_exposure: dict[str, Decimal] = defaultdict(Decimal)
    open_positions = 0
    signals: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    no_lookahead_rows: list[dict[str, Any]] = []
    total_fees = Decimal("0")
    total_slippage = Decimal("0")

    for decision in decisions:
        rel = rels.get(decision.relationship_id)
        if rel is None:
            _inc(funnel, "rejected_by_context")
            continue
        rel = normalize_context_backed_relationship(rel, decision.context_space_id)
        if rel.final_confidence < cfg.min_relationship_confidence:
            _inc(funnel, "rejected_by_context")
            rejected.append(_reject_row(rel, decision, "relationship_confidence_below_threshold"))
            continue
        _classify_context(funnel, decision)
        if decision.new_strategy_eligibility != "eligible":
            _inc(funnel, "rejected_by_context")
            rejected.append(_reject_row(rel, decision, "context_not_strategy_eligible"))
            continue
        if cfg.context_time_mode == "historical_replay_safe" and not _rules_replay_safe(decision, rules):
            _inc(funnel, "rejected_by_context")
            rejected.append(_reject_row(rel, decision, "context_not_replay_safe"))
            continue
        cov_a = coverage.get(rel.market_id_a)
        cov_b = coverage.get(rel.market_id_b)
        if not _coverage_ok(cov_a, cfg.min_coverage_score) or not _coverage_ok(cov_b, cfg.min_coverage_score):
            _inc(funnel, "rejected_by_coverage")
            rejected.append(_reject_row(rel, decision, "coverage_below_threshold"))
            continue
        if not rel.token_id_a_yes or not rel.token_id_b_yes:
            _inc(funnel, "rejected_by_context")
            rejected.append(_reject_row(rel, decision, "missing_yes_tokens"))
            continue
        rows_a = prices(rel.token_id_a_yes)
        rows_b = prices(rel.token_id_b_yes)
        if not rows_a or not rows_b:
            _inc(funnel, "rejected_by_context")
            rejected.append(_reject_row(rel, decision, "missing_price_history"))
            continue
        _inc(funnel, "price_history_present")
        if not _pair_is_economically_viable(
            rel, rows_a, rows_b,
            min_combined=cfg.min_combined_prob_for_pairwise,
            min_single=cfg.min_single_prob_for_pairwise,
        ):
            _inc(funnel, "rejected_by_pair_viability")
            rejected.append(_reject_row(rel, decision, "real_relationship_but_pairwise_not_tradeable"))
            continue
        aligned = align_price_series(
            rows_a,
            rows_b,
            start_ts_ms=cfg.start_ts_ms,
            end_ts_ms=cfg.end_ts_ms,
            signal_interval_ms=cfg.signal_interval_ms,
            staleness_limit_ms=cfg.quote_staleness_limit_ms,
        )
        if not aligned:
            _inc(funnel, "rejected_by_staleness_coverage")
            rejected.append(_reject_row(rel, decision, "alignment_failed"))
            continue
        _inc(funnel, "aligned_price_series")

        for point in aligned:
            _inc(funnel, "ticks_evaluated")
            no_lookahead_rows.append({
                "relationship_id": rel.relationship_id,
                "signal_ts_ms": point.ts_ms,
                "price_a_ts_ms": point.price_a_ts_ms,
                "price_b_ts_ms": point.price_b_ts_ms,
                "context_rule_ids_json": decision.context_rule_ids_json,
                "context_time_mode": cfg.context_time_mode,
                "violation": point.price_a_ts_ms > point.ts_ms or point.price_b_ts_ms > point.ts_ms,
            })
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
            if candidate is None:
                continue
            signal = asdict(candidate)
            signal["strategy_lane"] = decision.strategy_lane
            signal["context_space_id"] = decision.context_space_id
            signal["holdout_bucket"] = _holdout_bucket(decision)
            signals.append(signal)
            _inc(funnel, "gross_violations")
            if not candidate.accepted_for_simulation:
                _inc(funnel, "rejected_by_costs")
                rejected.append(_reject_row(rel, decision, candidate.rejection_reason or "not_accepted"))
                continue
            required_cash = cfg.max_stake_per_trade_usdc * 2
            if cash < required_cash and not cfg.allow_negative_cash:
                _inc(funnel, "rejected_by_costs")
                rejected.append(_reject_row(rel, decision, "cash_limit"))
                continue
            if open_positions >= cfg.max_concurrent_positions:
                _inc(funnel, "rejected_by_costs")
                rejected.append(_reject_row(rel, decision, "position_limit"))
                continue
            if (
                per_market_exposure[rel.market_id_a] + cfg.max_stake_per_trade_usdc
                > cfg.max_stake_per_market_usdc
            ):
                _inc(funnel, "rejected_by_costs")
                rejected.append(_reject_row(rel, decision, "market_exposure_limit"))
                continue
            _inc(funnel, "candidates_accepted")
            leg_rows, leg_fees, leg_slippage = _execute_candidate(rel, decision, candidate, cfg)
            trades.extend(leg_rows)
            cash -= sum(Decimal(str(t["notional_usdc"])) + Decimal(str(t["fees_usdc"])) for t in leg_rows)
            total_fees += leg_fees
            total_slippage += leg_slippage
            per_market_exposure[rel.market_id_a] += cfg.max_stake_per_trade_usdc
            per_market_exposure[rel.market_id_b] += cfg.max_stake_per_trade_usdc
            open_positions += 1

    mtm = _mark_to_market(trades, price_repo)
    ending_equity = cash + mtm
    net_pnl = ending_equity - cfg.starting_cash_usdc
    funnel["counts"]["trades_executed"] = len(trades) // 2
    funnel["counts"]["net_pnl"] = str(net_pnl)
    concentration = _concentration(trades)
    no_lookahead = {
        "rows_checked": len(no_lookahead_rows),
        "violations": sum(1 for row in no_lookahead_rows if row["violation"]),
    }
    credibility, rationale = _credibility(cfg, len(trades) // 2, net_pnl, concentration, no_lookahead)
    funnel["credibility_label"] = credibility
    metrics = {
        "run_id": run_id,
        "lane": cfg.lane,
        "context_time_mode": cfg.context_time_mode,
        "relationship_universe": cfg.relationship_universe,
        "starting_cash_usdc": str(cfg.starting_cash_usdc),
        "ending_cash_usdc": str(cash),
        "ending_equity_usdc": str(ending_equity),
        "net_pnl_usdc": str(net_pnl),
        "total_fees_usdc": str(total_fees),
        "total_slippage_usdc": str(total_slippage),
        "trades_executed": len(trades) // 2,
        "signals_generated": len(signals),
        "credibility_label": credibility,
        "credibility_rationale": rationale,
        "execution_model": cfg.execution_model,
    }
    equity_curve = [{
        "ts_ms": _now_ms(),
        "cash_usdc": str(cash),
        "equity_usdc": str(ending_equity),
    }]

    _write_json(lane_dir / "config.json", cfg.model_dump(mode="json"))
    _write_json(lane_dir / "metrics.json", metrics)
    _write_json(lane_dir / "funnel_audit.json", funnel)
    _write_json(lane_dir / "concentration.json", concentration)
    _write_json(lane_dir / "no_lookahead_audit.json", no_lookahead)
    _write_csv(lane_dir / "signals.csv", signals)
    _write_csv(lane_dir / "trades.csv", trades)
    _write_csv(lane_dir / "rejected_candidates.csv", rejected)
    _write_csv(lane_dir / "example_trades.csv", trades[:20])
    _write_csv(lane_dir / "equity_curve.csv", equity_curve)
    _write_parquet(lane_dir / "equity_curve.parquet", equity_curve)
    return {"run_id": run_id, "output_dir": lane_dir, "metrics": metrics, "funnel": funnel}


def run_context_null_baseline(data_root: Path, run_id: str) -> dict[str, Any]:
    out_dir = data_root / "backtests" / run_id / "context_aware" / "null_baseline"
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = {
        "run_id": run_id,
        "strategy": "context_null_baseline",
        "trades_executed": 0,
        "net_pnl_usdc": "0",
        "credibility_label": "data_insufficient",
        "message": "matched random baseline is not tradeable without context-approved pairs",
    }
    _write_json(out_dir / "metrics.json", metrics)
    return {"run_id": run_id, "output_dir": out_dir, "metrics": metrics}


def run_context_sensitivity_grid(
    data_root: Path,
    base_cfg: ContextAwareBacktestConfig,
    *,
    grid: str = "full",
) -> list[dict[str, Any]]:
    slippage_values = [25, 50, 100, 200]
    fee_values = [0, 25, 50, 100]
    edge_values = [0.005, 0.01, 0.02, 0.05]
    confidence_values = [0.5, 0.65, 0.8]
    if grid == "slim":
        slippage_values = [50, 100]
        fee_values = [0, 50]
        edge_values = [0.01, 0.02]
        confidence_values = [0.65, 0.8]
    confidence_values = sorted({*confidence_values, base_cfg.min_relationship_confidence})
    rows: list[dict[str, Any]] = []
    for slippage in slippage_values:
        for fee in fee_values:
            for edge in edge_values:
                for confidence in confidence_values:
                    cfg = base_cfg.model_copy(update={
                        "run_id": (
                            f"{base_cfg.run_id}_sens_s{slippage}_f{fee}_e"
                            f"{str(edge).replace('.', '')}_c{str(confidence).replace('.', '')}"
                        ),
                        "slippage_bps": Decimal(str(slippage)),
                        "fee_bps": Decimal(str(fee)),
                        "min_net_edge": edge,
                        "min_relationship_confidence": confidence,
                    })
                    result = run_context_aware_backtest(data_root, cfg)
                    metrics = result["metrics"]
                    rows.append({
                        "lane": cfg.lane,
                        "slippage_bps": slippage,
                        "fee_bps": fee,
                        "min_net_edge": edge,
                        "min_relationship_confidence": confidence,
                        "trades_executed": metrics["trades_executed"],
                        "net_pnl_usdc": metrics["net_pnl_usdc"],
                        "credibility_label": metrics["credibility_label"],
                    })
    out_dir = data_root / "backtests" / base_cfg.run_id / "context_aware" / "sensitivity"
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "sensitivity_grid.csv", rows)
    return rows


def _filter_decisions(
    decisions: list[ContextRelationshipDecisionRow],
    lane: str,
    *,
    include_auto_approved: bool = False,
    relationship_universe: str = "reviewed_lanes",
) -> list[ContextRelationshipDecisionRow]:
    if relationship_universe == "reviewed_lanes":
        reviewed = {"strict_context_valid", "reviewed_context_valid"}
        if lane == "all_context_research":
            return [d for d in decisions if d.strategy_lane in reviewed]
        if lane not in reviewed:
            return []
    if lane == "all_context_research":
        allowed = {"strict_context_valid", "reviewed_context_valid", "exploratory_context_unreviewed"}
        if include_auto_approved:
            allowed = allowed | {"exploratory_context_auto_approved"}
        return [d for d in decisions if d.strategy_lane in allowed]
    return [d for d in decisions if d.strategy_lane == lane]


def _filter_decisions_by_relationship_universe(
    decisions: list[ContextRelationshipDecisionRow],
    rels: dict[str, RelationshipCandidateRow],
    relationship_universe: str,
) -> list[ContextRelationshipDecisionRow]:
    if relationship_universe != "accepted_only":
        return decisions
    return [
        decision for decision in decisions
        if (rel := rels.get(decision.relationship_id)) is not None
        and rel.validation_status == "accepted"
    ]


def _new_funnel(run_id: str, cfg: ContextAwareBacktestConfig) -> dict[str, Any]:
    keys = [
        "relationships_loaded",
        "context_required",
        "world_context_valid",
        "world_context_missing",
        "world_context_invalid",
        "market_terms_valid",
        "market_terms_missing",
        "market_terms_invalid",
        "strategy_eligible",
        "price_history_present",
        "aligned_price_series",
        "ticks_evaluated",
        "gross_violations",
        "rejected_by_costs",
        "rejected_by_context",
        "rejected_by_terms",
        "rejected_by_completeness",
        "rejected_by_staleness_coverage",
        "rejected_by_coverage",
        "candidates_accepted",
        "trades_executed",
        "rejected_by_pair_viability",
    ]
    return {
        "run_id": run_id,
        "lane": cfg.lane,
        "context_time_mode": cfg.context_time_mode,
        "relationship_universe": cfg.relationship_universe,
        "counts": {k: 0 for k in keys},
        "completeness_class_counts": {},
    }


def _inc(funnel: dict[str, Any], key: str, amount: int = 1) -> None:
    funnel["counts"][key] = int(funnel["counts"].get(key, 0)) + amount


def _classify_context(funnel: dict[str, Any], decision: ContextRelationshipDecisionRow) -> None:
    _inc(funnel, "context_required")
    if decision.new_strategy_eligibility == "eligible":
        _inc(funnel, "strategy_eligible")
    if "missing" in decision.decision_reason:
        _inc(funnel, "world_context_missing")
    else:
        _inc(funnel, "world_context_valid")
    if "market terms" in decision.decision_reason and "incomplete" in decision.decision_reason:
        _inc(funnel, "market_terms_missing")
    elif decision.strategy_lane == "strict_context_valid":
        _inc(funnel, "market_terms_valid")
    else:
        _inc(funnel, "market_terms_missing")


def _coverage_ok(row: Any, min_score: float) -> bool:
    return bool(row and row.recommended_for_backtest and row.coverage_score >= min_score)


def _rules_replay_safe(
    decision: ContextRelationshipDecisionRow,
    rules: dict[str, Any],
) -> bool:
    try:
        ids = json.loads(decision.context_rule_ids_json or "[]")
    except json.JSONDecodeError:
        return False
    return bool(ids) and all(rule_id in rules for rule_id in ids)


def _holdout_bucket(decision: ContextRelationshipDecisionRow) -> str:
    raw = f"{decision.context_space_id}|{decision.relationship_id}"
    value = int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16) % 10
    return "holdout" if value >= 7 else "discovery"


def _reject_row(
    rel: RelationshipCandidateRow,
    decision: ContextRelationshipDecisionRow,
    reason: str,
) -> dict[str, Any]:
    return {
        "relationship_id": rel.relationship_id,
        "market_id_a": rel.market_id_a,
        "market_id_b": rel.market_id_b,
        "relationship_type": rel.relationship_type,
        "relationship_subtype": rel.relationship_subtype,
        "context_space_id": decision.context_space_id,
        "strategy_lane": decision.strategy_lane,
        "rejection_reason": reason,
    }


def _execute_candidate(
    rel: RelationshipCandidateRow,
    decision: ContextRelationshipDecisionRow,
    candidate: Any,
    cfg: ContextAwareBacktestConfig,
) -> tuple[list[dict[str, Any]], Decimal, Decimal]:
    rows = []
    total_fees = Decimal("0")
    total_slippage = Decimal("0")
    provenance = _relationship_provenance(rel)
    trade_group_id = candidate.candidate_id
    for leg, token_id, market_id, price in [
        ("a", candidate.token_id_a, rel.market_id_a, candidate.price_a),
        ("b", candidate.token_id_b, rel.market_id_b, candidate.price_b),
    ]:
        costs = estimate_costs(
            notional_usdc=cfg.max_stake_per_trade_usdc,
            mid_price=price,
            execution_model=cfg.execution_model,
            fee_bps=cfg.fee_bps,
            slippage_bps=cfg.slippage_bps,
        )
        shares = cfg.max_stake_per_trade_usdc / costs.fill_price if costs.fill_price else Decimal("0")
        total_fees += costs.fee_usdc
        total_slippage += costs.slippage_usdc
        rows.append({
            "trade_id": uuid.uuid4().hex,
            "trade_group_id": trade_group_id,
            "candidate_id": candidate.candidate_id,
            "run_id": cfg.run_id,
            "relationship_id": rel.relationship_id,
            "relationship_type": rel.relationship_type,
            "relationship_subtype": rel.relationship_subtype,
            "relationship_family": rel.relationship_family,
            "strategy_family": rel.strategy_family,
            "outcome_space_id": rel.outcome_space_id,
            "context_space_id": decision.context_space_id,
            "strategy_lane": decision.strategy_lane,
            "leg": leg,
            "token_id": token_id,
            "market_id": market_id,
            "side": "buy",
            "fill_ts_ms": candidate.signal_ts_ms,
            "fill_price": str(costs.fill_price),
            "shares": str(shares),
            "notional_usdc": str(shares * costs.fill_price),
            "fees_usdc": str(costs.fee_usdc),
            "slippage_cost_usdc": str(costs.slippage_usdc),
            "execution_model": cfg.execution_model,
            "holdout_bucket": _holdout_bucket(decision),
            **provenance,
        })
    return rows, total_fees, total_slippage


def _relationship_provenance(rel: RelationshipCandidateRow) -> dict[str, Any]:
    try:
        evidence = json.loads(rel.evidence_json or "{}")
    except json.JSONDecodeError:
        evidence = {}
    source = (
        evidence.get("hypothesis_source")
        or evidence.get("source")
        or evidence.get("source_candidate_method")
        or "deterministic_relationship"
    )
    model_type = evidence.get("model_type") or (
        "deterministic" if source == "deterministic_relationship" else ""
    )
    return {
        "hypothesis_source": source,
        "hypothesis_id": evidence.get("hypothesis_id") or rel.relationship_id,
        "hypothesis_model_name": evidence.get("model_name") or evidence.get("deepseek_model") or "",
        "hypothesis_model_type": model_type,
        "hypothesis_prompt_version": evidence.get("prompt_version") or "",
        "hypothesis_relationship_type": evidence.get("hypothesis_relationship_type") or evidence.get("relationship_type") or "",
        "hypothesis_confidence": evidence.get("confidence") or rel.model_confidence,
    }


def _mark_to_market(trades: list[dict[str, Any]], price_repo: ParquetPriceHistoryRepository) -> Decimal:
    value = Decimal("0")
    for trade in trades:
        rows = list(price_repo.iter_for_token(str(trade["token_id"])))
        if not rows:
            continue
        latest = max(rows, key=lambda r: r.ts_ms)
        value += latest.price * Decimal(str(trade["shares"]))
    return value


def _concentration(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by_market = Counter(str(t["market_id"]) for t in trades)
    by_space = Counter(str(t["context_space_id"]) for t in trades)
    total = max(len(trades), 1)
    largest_market_share = max(by_market.values(), default=0) / total
    largest_space_share = max(by_space.values(), default=0) / total
    return {
        "by_market": dict(by_market),
        "by_context_space": dict(by_space),
        "largest_market_share": largest_market_share,
        "largest_context_space_share": largest_space_share,
        "concentration_flag": largest_market_share > 0.5 or largest_space_share > 0.7,
    }


def _credibility(
    cfg: ContextAwareBacktestConfig,
    trades_executed: int,
    net_pnl: Decimal,
    concentration: dict[str, Any],
    no_lookahead: dict[str, Any],
) -> tuple[str, str]:
    if int(no_lookahead.get("violations", 0)) > 0:
        return "not_credible", "no-lookahead audit found price timestamp violations"
    if trades_executed < 30:
        return "data_insufficient", "fewer than 30 trade pairs executed"
    # Auto-approved lane is research/exploratory only; never headline credible
    if cfg.lane == "exploratory_context_auto_approved" or cfg.include_auto_approved:
        if net_pnl > 0:
            return (
                "inconclusive",
                "positive result includes auto-approved relationships; "
                "not eligible for headline credibility",
            )
        return "not_credible", "net PnL is not positive (auto-approved lane)"
    if cfg.context_time_mode == "ex_post_research" and net_pnl > 0:
        return "inconclusive", "positive result is ex-post research and cannot be headline credible"
    if concentration.get("concentration_flag"):
        return "inconclusive", "result is too concentrated in one market or context space"
    if net_pnl > 0 and cfg.lane in {"strict_context_valid", "reviewed_context_valid"}:
        return "credible_positive", "positive strict/reviewed result passed local gates"
    if net_pnl <= 0:
        return "not_credible", "net PnL is not positive"
    return "inconclusive", "positive exploratory or mixed-lane result needs review"


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


def _write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path)
