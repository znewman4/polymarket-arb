"""Historical relationship strategy replay against aligned price series.

This is independent of backtest/replay_engine.py (which replays recorded
quote/snapshot events). This module replays accepted relationships against
PriceHistoryRow time series.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..storage.base import (
    BacktestMetricsRow,
    PriceHistoryRow,
    RelationshipCandidateRow,
    SimulatedTradeRow,
    StrategyCandidateRow,
)
from ..storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from ..storage.parquet.backtest_metrics_repo import ParquetBacktestMetricsRepository
from ..storage.parquet.markets_repo import ParquetMarketsRepository
from ..storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from ..storage.parquet.simulated_trades_repo import ParquetSimulatedTradesRepository
from ..storage.parquet.strategy_candidates_repo import ParquetStrategyCandidatesRepository
from ..strategies.models import RelationshipBacktestConfig
from ..strategies.nesting_contradiction import evaluate_relationship_at_tick
from .cost_model import estimate_costs
from .price_alignment import align_price_series
from .resolutions import infer_resolutions

_FUNNEL_COUNT_KEYS = (
    "accepted_relationships_loaded",
    "strategy_eligible_relationships",
    "relationships_with_price_history",
    "relationships_with_aligned_price_series",
    "ticks_evaluated",
    "gross_violations_found",
    "net_violations_after_costs",
    "candidates_accepted_for_simulation",
    "trades_executed",
)

_FUNNEL_REJECTION_KEYS = (
    "no_price_violation",
    "net_edge_below_threshold",
    "coverage_too_low",
    "missing_price_history",
    "alignment_failed",
    "unsupported_trade_structure",
    "missing_yes_no_token",
    "manual_review_blocked",
    "cash_limit",
)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _config_hash(cfg: RelationshipBacktestConfig) -> str:
    d = cfg.model_dump()
    raw = json.dumps(d, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _get_token_ids_for_relationship(
    rel: RelationshipCandidateRow,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (token_id_a for_leg_a, token_id_b for_leg_b) depending on relationship type."""
    rt = rel.relationship_type
    if rt == "nested_a_implies_b":
        return rel.token_id_a_no, rel.token_id_b_yes, rel.token_id_a_yes, rel.token_id_b_no
    elif rt == "nested_b_implies_a":
        return rel.token_id_a_yes, rel.token_id_b_no, rel.token_id_a_no, rel.token_id_b_yes
    elif rt in ("contradiction", "mutually_exclusive", "same_entity_exclusive",
                "mutually_exclusive_category"):
        return rel.token_id_a_no, rel.token_id_b_no, rel.token_id_a_yes, rel.token_id_b_yes
    elif rt in ("inverse", "inverse_temporal_order"):
        # Both overround (NO_A + NO_B) and underround (YES_A + YES_B) possible;
        # return YES tokens as "primary" price series for alignment
        return rel.token_id_a_yes, rel.token_id_b_yes, rel.token_id_a_no, rel.token_id_b_no
    return None, None, None, None


def _load_price_rows(
    price_repo: ParquetPriceHistoryRepository,
    token_id: str,
) -> list[PriceHistoryRow]:
    return list(price_repo.iter_for_token(token_id))


def _new_funnel_audit(run_id: str, strategy_name: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "relationship_family": strategy_name,
        "counts": {key: 0 for key in _FUNNEL_COUNT_KEYS},
        "rejections": {key: 0 for key in _FUNNEL_REJECTION_KEYS},
    }


def _inc(mapping: dict[str, int], key: str, amount: int = 1) -> None:
    mapping[key] = mapping.get(key, 0) + amount


def _is_net_edge_rejection(reason: str | None) -> bool:
    return bool(reason and reason.startswith("net_edge="))


def run_relationship_backtest(
    data_root: Path,
    cfg: RelationshipBacktestConfig,
) -> dict[str, Any]:
    """Full relationship strategy backtest.

    Returns a dict with run_id, output_dir, and metrics.
    """
    if not cfg.run_id:
        cfg = cfg.model_copy(update={"run_id": uuid.uuid4().hex})
    run_id = cfg.run_id

    # Repos
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    price_repo = ParquetPriceHistoryRepository(data_root)
    cov_repo = ParquetBackfillCoverageRepository(data_root)
    cand_repo = ParquetStrategyCandidatesRepository(data_root)
    trades_repo = ParquetSimulatedTradesRepository(data_root)
    metrics_repo = ParquetBacktestMetricsRepository(data_root)
    markets_repo = ParquetMarketsRepository(data_root)

    # Load relationships
    relationships = [
        r for r in rel_repo.iter_accepted()
        if r.final_confidence >= cfg.min_relationship_confidence
        and r.relationship_type in cfg.relationship_types
        and (cfg.allow_manual_review_relationships or r.validation_status == "accepted")
    ]
    funnel = _new_funnel_audit(run_id, cfg.strategy_name)
    funnel["counts"]["accepted_relationships_loaded"] = len(relationships)
    funnel["counts"]["relationships_loaded"] = len(relationships)
    funnel["counts"]["strategy_eligible_relationships"] = sum(
        1 for r in relationships if r.strategy_eligibility_status == "eligible"
    )

    # Load coverage
    coverage_by_market = {row.market_id: row for row in cov_repo.iter_latest()}

    # Load markets for resolution inference
    markets_list = list(markets_repo.iter_active_markets())

    # Cache price series per token
    price_cache: dict[str, list[PriceHistoryRow]] = {}

    def _get_prices(tid: str) -> list[PriceHistoryRow]:
        if tid not in price_cache:
            price_cache[tid] = _load_price_rows(price_repo, tid)
        return price_cache[tid]

    # Bankroll state
    cash = cfg.starting_cash_usdc
    per_market_exposure: dict[str, Decimal] = defaultdict(Decimal)
    concurrent_positions = 0

    all_candidates: list[StrategyCandidateRow] = []
    all_trades: list[SimulatedTradeRow] = []

    signals_generated = 0
    candidates_accepted = 0
    candidates_rejected = 0
    rejection_counts: dict[str, int] = defaultdict(int)
    total_fees = Decimal("0")
    total_slippage = Decimal("0")

    # Open positions: token_id → {shares, fill_price, fill_ts_ms, candidate_id}
    open_positions: dict[str, dict] = {}

    # Process relationships in deterministic order
    for rel in sorted(relationships, key=lambda r: r.relationship_id):
        if rel.validation_status == "needs_manual_review" and not cfg.allow_manual_review_relationships:
            _inc(funnel["rejections"], "manual_review_blocked")
            continue
        if rel.strategy_eligibility_status and rel.strategy_eligibility_status != "eligible":
            _inc(funnel["rejections"], "coverage_too_low")
            continue

        # Coverage filter
        cov_a = coverage_by_market.get(rel.market_id_a)
        cov_b = coverage_by_market.get(rel.market_id_b)
        if cov_a and not cov_a.recommended_for_backtest:
            _inc(funnel["rejections"], "coverage_too_low")
            continue
        if cov_b and not cov_b.recommended_for_backtest:
            _inc(funnel["rejections"], "coverage_too_low")
            continue
        if cov_a and cov_a.coverage_score < cfg.min_coverage_score:
            _inc(funnel["rejections"], "coverage_too_low")
            continue
        if cov_b and cov_b.coverage_score < cfg.min_coverage_score:
            _inc(funnel["rejections"], "coverage_too_low")
            continue

        # Determine which token IDs to load for price alignment
        tok_a_yes = rel.token_id_a_yes
        tok_b_yes = rel.token_id_b_yes
        if not tok_a_yes or not tok_b_yes:
            _inc(funnel["rejections"], "missing_yes_no_token")
            continue

        rows_a = _get_prices(tok_a_yes)
        rows_b = _get_prices(tok_b_yes)
        if not rows_a or not rows_b:
            _inc(funnel["rejections"], "missing_price_history")
            continue
        _inc(funnel["counts"], "relationships_with_price_history")

        aligned = align_price_series(
            rows_a, rows_b,
            start_ts_ms=cfg.start_ts_ms,
            end_ts_ms=cfg.end_ts_ms,
            signal_interval_ms=cfg.signal_interval_ms,
            staleness_limit_ms=cfg.quote_staleness_limit_ms,
        )
        if not aligned:
            _inc(funnel["rejections"], "alignment_failed")
            continue
        _inc(funnel["counts"], "relationships_with_aligned_price_series")

        for point in aligned:
            signals_generated += 1
            _inc(funnel["counts"], "ticks_evaluated")
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
                _inc(funnel["rejections"], "no_price_violation")
                continue

            _inc(funnel["counts"], "gross_violations_found")
            all_candidates.append(candidate)

            if not candidate.accepted_for_simulation:
                candidates_rejected += 1
                reason = candidate.rejection_reason or "unknown"
                rejection_counts[reason] += 1
                if _is_net_edge_rejection(reason):
                    _inc(funnel["rejections"], "net_edge_below_threshold")
                elif reason == "unsupported_trade_structure":
                    _inc(funnel["rejections"], "unsupported_trade_structure")
                continue

            _inc(funnel["counts"], "net_violations_after_costs")
            # Budget checks
            if cash < cfg.max_stake_per_trade_usdc * 2 and not cfg.allow_negative_cash:
                candidates_rejected += 1
                rejection_counts["insufficient_cash"] += 1
                _inc(funnel["rejections"], "cash_limit")
                all_candidates[-1] = StrategyCandidateRow(
                    **{**asdict(candidate), "accepted_for_simulation": False, "rejection_reason": "insufficient_cash"}
                )
                continue

            if (per_market_exposure[rel.market_id_a] + cfg.max_stake_per_trade_usdc
                    > cfg.max_stake_per_market_usdc):
                candidates_rejected += 1
                rejection_counts["market_budget_exhausted"] += 1
                _inc(funnel["rejections"], "cash_limit")
                all_candidates[-1] = StrategyCandidateRow(
                    **{**asdict(candidate), "accepted_for_simulation": False, "rejection_reason": "market_budget_exhausted"}
                )
                continue

            if concurrent_positions >= cfg.max_concurrent_positions:
                candidates_rejected += 1
                rejection_counts["max_positions_reached"] += 1
                _inc(funnel["rejections"], "cash_limit")
                all_candidates[-1] = StrategyCandidateRow(
                    **{**asdict(candidate), "accepted_for_simulation": False, "rejection_reason": "max_positions_reached"}
                )
                continue

            candidates_accepted += 1
            _inc(funnel["counts"], "candidates_accepted_for_simulation")
            stake = cfg.max_stake_per_trade_usdc

            # Execute both legs
            for leg, (token_id, price_leg) in enumerate([
                (candidate.token_id_a, candidate.price_a),
                (candidate.token_id_b, candidate.price_b),
            ]):
                if price_leg <= Decimal("0"):
                    continue
                cost_est = estimate_costs(
                    notional_usdc=stake,
                    mid_price=price_leg,
                    execution_model=cfg.execution_model,
                    fee_bps=cfg.fee_bps,
                    slippage_bps=cfg.slippage_bps,
                )
                shares = stake / cost_est.fill_price if cost_est.fill_price > 0 else Decimal("0")
                notional = shares * cost_est.fill_price

                trade = SimulatedTradeRow(
                    trade_id=uuid.uuid4().hex,
                    candidate_id=candidate.candidate_id,
                    run_id=run_id,
                    relationship_id=rel.relationship_id,
                    leg="a" if leg == 0 else "b",
                    token_id=token_id,
                    market_id=rel.market_id_a if leg == 0 else rel.market_id_b,
                    side="buy",
                    fill_ts_ms=candidate.signal_ts_ms,
                    fill_price=cost_est.fill_price,
                    shares=shares,
                    notional_usdc=notional,
                    fees_usdc=cost_est.fee_usdc,
                    slippage_cost_usdc=cost_est.slippage_usdc,
                    execution_model=cfg.execution_model,
                    mark_to_market_ts_ms=None,
                    mark_to_market_value_usdc=None,
                    resolution_ts_ms=None,
                    resolution_outcome=None,
                    realised_pnl_usdc=None,
                    schema_version=1,
                    ingested_ts_ms=_now_ms(),
                )
                all_trades.append(trade)
                cash -= notional + cost_est.fee_usdc
                total_fees += cost_est.fee_usdc
                total_slippage += cost_est.slippage_usdc
                per_market_exposure[trade.market_id] += stake
                open_positions[token_id] = {
                    "shares": shares,
                    "fill_price": cost_est.fill_price,
                    "fill_ts_ms": candidate.signal_ts_ms,
                    "candidate_id": candidate.candidate_id,
                }
                concurrent_positions = len(open_positions)

    # Infer resolutions and compute realized PnL
    prices_by_token: dict[str, list[PriceHistoryRow]] = {}
    for tok_id in open_positions:
        prices_by_token[tok_id] = _get_prices(tok_id)

    resolutions = infer_resolutions(markets_list, prices_by_token)

    # Update trades with resolution info
    updated_trades: list[SimulatedTradeRow] = []
    for trade in all_trades:
        market_res = resolutions.get(trade.market_id)
        if market_res and market_res.yes_outcome != "unresolved":
            is_yes_token = trade.token_id == (
                next((m.clob_token_ids[0] for m in markets_list if m.id == trade.market_id), None)
            )
            if is_yes_token:
                outcome = market_res.yes_outcome
            else:
                outcome = "no" if market_res.yes_outcome == "yes" else "yes"

            resolution_value = Decimal("1") if outcome == "yes" else Decimal("0")
            realised_pnl = (resolution_value - trade.fill_price) * trade.shares - trade.fees_usdc

            updated = SimulatedTradeRow(
                **{**asdict(trade),
                   "resolution_ts_ms": market_res.resolution_ts_ms,
                   "resolution_outcome": outcome,
                   "realised_pnl_usdc": realised_pnl,
                   "mark_to_market_value_usdc": resolution_value * trade.shares,
                   "mark_to_market_ts_ms": market_res.resolution_ts_ms,
                   }
            )
            updated_trades.append(updated)
            cash += resolution_value * trade.shares
        else:
            updated_trades.append(trade)

    all_trades = updated_trades

    # Final equity
    mtm_value = Decimal("0")
    for trade in all_trades:
        if trade.resolution_outcome is None:
            rows = _get_prices(trade.token_id)
            if rows:
                latest_price = max(rows, key=lambda r: r.ts_ms).price
                mtm_value += latest_price * trade.shares
        elif trade.mark_to_market_value_usdc is not None:
            mtm_value += trade.mark_to_market_value_usdc
    ending_equity = cash + mtm_value

    # Metrics
    gross_pnl = ending_equity - cfg.starting_cash_usdc
    net_pnl = gross_pnl
    total_return_pct = float(gross_pnl / cfg.starting_cash_usdc * 100) if cfg.starting_cash_usdc > 0 else 0.0

    resolved_trades = [t for t in all_trades if t.realised_pnl_usdc is not None]
    win_rate: float | None = None
    if resolved_trades:
        wins = sum(1 for t in resolved_trades if (t.realised_pnl_usdc or Decimal("0")) > 0)
        win_rate = wins / len(resolved_trades)

    avg_gross_edge = 0.0
    avg_net_edge = 0.0
    accepted_cands = [c for c in all_candidates if c.accepted_for_simulation]
    if accepted_cands:
        avg_gross_edge = float(sum(c.gross_edge for c in accepted_cands) / len(accepted_cands))
        avg_net_edge = float(sum(c.net_edge_after_costs for c in accepted_cands) / len(accepted_cands))

    # Build simple equity curve
    equity_curve = [
        {"ts_ms": _now_ms(), "cash_usdc": str(cash), "equity_usdc": str(ending_equity)}
    ]

    # Max drawdown (simplified: using final equity only for now)
    max_drawdown_pct = max(0.0, float(
        (cfg.starting_cash_usdc - ending_equity) / cfg.starting_cash_usdc * 100
    )) if cfg.starting_cash_usdc > 0 else 0.0

    # PnL by relationship type
    pnl_by_type: dict[str, float] = defaultdict(float)
    for t in all_trades:
        if t.realised_pnl_usdc is not None:
            cand = next((c for c in all_candidates if c.candidate_id == t.candidate_id), None)
            if cand:
                pnl_by_type[cand.relationship_type] += float(t.realised_pnl_usdc)

    credibility_label, credibility_rationale = classify_credibility(
        trades_executed=len(all_trades) // 2,  # pairs
        net_pnl_usdc=net_pnl,
        candidates_accepted=candidates_accepted,
        candidates_rejected=candidates_rejected,
        rejection_counts=dict(rejection_counts),
        max_drawdown_pct=max_drawdown_pct,
        coverage_by_market=coverage_by_market,
    )

    metrics = BacktestMetricsRow(
        run_id=run_id,
        config_hash=_config_hash(cfg),
        starting_cash_usdc=cfg.starting_cash_usdc,
        ending_cash_usdc=cash,
        ending_equity_usdc=ending_equity,
        total_return_pct=total_return_pct,
        gross_pnl_usdc=gross_pnl,
        net_pnl_usdc=net_pnl,
        total_fees_usdc=total_fees,
        total_slippage_usdc=total_slippage,
        relationships_considered=len(relationships),
        signals_generated=signals_generated,
        candidates_accepted=candidates_accepted,
        candidates_rejected=candidates_rejected,
        trades_executed=len(all_trades) // 2,
        rejection_reason_counts_json=json.dumps(dict(rejection_counts)),
        win_rate_when_resolved=win_rate,
        avg_gross_edge=avg_gross_edge,
        avg_net_edge=avg_net_edge,
        avg_hold_time_ms=None,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_like=None,
        pnl_by_relationship_type_json=json.dumps({k: round(v, 4) for k, v in pnl_by_type.items()}),
        pnl_by_execution_model_json=json.dumps({cfg.execution_model: float(net_pnl)}),
        pnl_by_confidence_bucket_json=json.dumps({}),
        null_baseline_pnl_usdc=None,
        null_baseline_win_rate=None,
        credibility_label=credibility_label,
        credibility_rationale=credibility_rationale,
        schema_version=1,
        ingested_ts_ms=_now_ms(),
    )

    # Persist
    family_dir = "relationship_strategy" if cfg.strategy_name == "nesting_contradiction" else cfg.strategy_name
    out_dir = data_root / "backtests" / run_id / family_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    funnel["counts"]["trades_executed"] = len(all_trades) // 2
    ticks = int(funnel["counts"].get("ticks_evaluated", 0))
    no_violation = int(funnel["rejections"].get("no_price_violation", 0))
    funnel["counts"]["no_price_violation_count"] = no_violation
    funnel["counts"]["no_price_violation_pct"] = round(no_violation / ticks, 6) if ticks else 0.0
    funnel["counts"]["candidates_accepted"] = funnel["counts"].get("candidates_accepted_for_simulation", 0)
    funnel["counts"]["rejected_by_costs"] = funnel["rejections"].get("net_edge_below_threshold", 0)
    funnel["counts"]["rejected_by_coverage"] = funnel["rejections"].get("coverage_too_low", 0)
    funnel["counts"]["rejected_by_manual_review"] = funnel["rejections"].get("manual_review_blocked", 0)
    funnel["counts"]["net_pnl"] = str(net_pnl)
    funnel["credibility_label"] = credibility_label

    _write_json(out_dir / "config.json", cfg.model_dump(mode="json"))
    _write_json(out_dir / "funnel_audit.json", funnel)
    _write_csv(out_dir / "signals.csv", [asdict(c) for c in all_candidates])
    _write_csv(out_dir / "trades.csv", [asdict(t) for t in all_trades])
    _write_csv(out_dir / "rejected_candidates.csv",
               [asdict(c) for c in all_candidates if not c.accepted_for_simulation])
    _write_csv(out_dir / "equity_curve.csv", equity_curve)
    _write_csv(out_dir / "positions.csv",
               [{"token_id": k, **v} for k, v in open_positions.items()])
    _write_json(out_dir / "metrics.json", asdict(metrics))

    # Also write parquet for equity curve
    _write_parquet(out_dir / "equity_curve.parquet", equity_curve)

    # Persist to normalised repos
    if all_candidates:
        cand_repo.append_many(all_candidates)
    if all_trades:
        trades_repo.append_many(all_trades)
    metrics_repo.append(metrics)

    return {
        "run_id": run_id,
        "output_dir": out_dir,
        "metrics": metrics,
    }


def classify_credibility(
    *,
    trades_executed: int,
    net_pnl_usdc: Decimal,
    candidates_accepted: int,
    candidates_rejected: int,
    rejection_counts: dict[str, int],
    max_drawdown_pct: float,
    coverage_by_market: dict,
    null_pnl: Decimal | None = None,
) -> tuple[str, str]:
    """Classify the credibility of a backtest run."""
    # data_insufficient checks first
    total_candidates = candidates_accepted + candidates_rejected
    unsupported_count = rejection_counts.get("unsupported_trade_structure", 0)

    avg_coverage = 0.0
    if coverage_by_market:
        avg_coverage = sum(c.coverage_score for c in coverage_by_market.values()) / len(coverage_by_market)

    if trades_executed < 30:
        return "data_insufficient", (
            f"Only {trades_executed} trade pairs executed; need ≥30 for credibility. "
            "Consider running backfill with more data or lowering min_net_edge."
        )
    if avg_coverage < 0.5:
        return "data_insufficient", (
            f"Average coverage score {avg_coverage:.2f} < 0.5. "
            "Run backfill prices + coverage first."
        )
    if total_candidates > 0 and unsupported_count / max(total_candidates, 1) >= 0.8:
        return "data_insufficient", (
            f"{unsupported_count}/{total_candidates} candidates rejected for unsupported_trade_structure. "
            "Most markets may lack binary token IDs."
        )

    # not_credible
    if net_pnl_usdc <= Decimal("0"):
        return "not_credible", (
            f"Net PnL after costs is {float(net_pnl_usdc):.2f} USDC (non-positive). "
            f"Max drawdown: {max_drawdown_pct:.1f}%."
        )
    if max_drawdown_pct > 50.0:
        return "not_credible", (
            f"Max drawdown {max_drawdown_pct:.1f}% exceeds 50% threshold."
        )
    if null_pnl is not None and null_pnl >= net_pnl_usdc:
        return "not_credible", (
            f"Null baseline PnL ({float(null_pnl):.2f}) ≥ strategy PnL ({float(net_pnl_usdc):.2f}). "
            "No evidence of signal."
        )

    # credible_positive — needs all gates
    if net_pnl_usdc > Decimal("0") and max_drawdown_pct <= 25.0:
        return "credible_positive", (
            f"Net PnL {float(net_pnl_usdc):.2f} USDC positive, "
            f"max drawdown {max_drawdown_pct:.1f}% ≤ 25%. "
            f"{trades_executed} trade pairs on {candidates_accepted} accepted candidates. "
            "Further sensitivity analysis recommended before drawing conclusions."
        )

    return "inconclusive", (
        f"Net PnL {float(net_pnl_usdc):.2f} USDC, drawdown {max_drawdown_pct:.1f}%. "
        "Results neither clearly positive nor negative."
    )


def _write_json(path: Path, payload: Any) -> None:
    import json
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    import csv
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_parquet(path: Path, rows: list[dict]) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        if not rows:
            return
        table = pa.Table.from_pylist([{k: str(v) for k, v in r.items()} for r in rows])
        pq.write_table(table, path)
    except Exception:
        pass
