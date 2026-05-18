"""Exploratory research replay — configurable entry/exit/sizing policies.

RESEARCH-ONLY / EXPLORATORY. Not trading advice.
All outputs from non-strict presets are labelled EXPLORATORY.

This module is Phase B of the trade-surface expansion refactor.  It mirrors
context_aware_replay but adds configurable policy switches via ResearchPreset:

Entry policies
--------------
first_violation_only / one_trade_per_relationship:
    Enter once per relationship, never re-enter.  Matches current strict behaviour.
reenter_after_cooldown:
    Enter when a violation is detected, then allow re-entry after ``cooldown_ms``
    has elapsed since the last entry.  Generates more fills from persistent violations.
trade_every_distinct_violation_window:
    Enter once per contiguous violation run.  A new window starts when the previous
    tick had no violation.  Useful for counting distinct signal episodes.
one_trade_per_market_pair_per_day:
    At most one trade per (relationship, calendar-day).

Exit policies
-------------
hold_to_resolution:
    Positions are never closed explicitly.  Marked-to-market at end-of-data.
close_on_reversal:
    Close when the violation disappears (no candidate at this tick).
diagnostic_no_exit_only_mark_to_market:
    Alias for hold_to_resolution — no explicit close, pure MtM.

Sizing policies
---------------
flat_small:
    Fixed stake = preset.stake_size_usdc per leg.
edge_weighted:
    Scale stake by gross_edge relative to a reference edge (0.10).
    Bounded between 0.5x and 3x of preset.stake_size_usdc.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..context.decision_engine import normalize_context_backed_relationship
from ..research_presets import ResearchPreset
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
from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from ..strategies.models import ContextAwareBacktestConfig
from ..strategies.nesting_contradiction import evaluate_relationship_at_tick
from .cost_model import estimate_costs
from .price_alignment import align_price_series

_LABEL = "RESEARCH-ONLY — exploratory_trade_surface"
_NOTE = (
    "Results from this replay are EXPLORATORY / not credibility-valid. "
    "Designed to maximise simulated fill count for analysis. Not trading advice."
)

_SUM_BASED_TYPES = frozenset({
    "mutually_exclusive_category", "contradiction", "mutually_exclusive",
    "same_entity_exclusive", "inverse", "inverse_temporal_order",
})


# ── entry state ───────────────────────────────────────────────────────────────


@dataclass
class _EntryState:
    trade_count: int = 0
    last_trade_ts_ms: int | None = None
    prev_tick_had_violation: bool = False
    open_position: bool = False


def _should_enter(
    rel_id: str,
    ts_ms: int,
    entry_policy: str,
    cooldown_ms: int,
    max_trades: int,
    states: dict[str, _EntryState],
    is_violation: bool,
) -> tuple[bool, str]:
    """Return (enter, entry_kind) where entry_kind is 'first' | 'reentry' | blocked reason."""
    state = states[rel_id]

    # Cap always applies.
    if max_trades > 0 and state.trade_count >= max_trades:
        return False, "max_trades"

    if entry_policy in ("first_violation_only", "one_trade_per_relationship"):
        if state.trade_count > 0:
            return False, "already_traded"
        return True, "first"

    if entry_policy == "reenter_after_cooldown":
        if state.trade_count == 0:
            return True, "first"
        if state.last_trade_ts_ms is not None and (ts_ms - state.last_trade_ts_ms) < cooldown_ms:
            return False, "cooldown"
        return True, "reentry"

    if entry_policy == "trade_every_distinct_violation_window":
        # New window when the previous tick had no violation.
        if not state.prev_tick_had_violation:
            kind = "first" if state.trade_count == 0 else "reentry"
            return True, kind
        return False, "same_violation_window"

    if entry_policy == "one_trade_per_market_pair_per_day":
        day_ms = 24 * 60 * 60 * 1000
        today = ts_ms // day_ms
        last_day = state.last_trade_ts_ms // day_ms if state.last_trade_ts_ms else -1
        if today == last_day:
            return False, "same_day"
        kind = "first" if state.trade_count == 0 else "reentry"
        return True, kind

    # fallback — first_violation_only
    if state.trade_count > 0:
        return False, "already_traded"
    return True, "first"


# ── sizing ────────────────────────────────────────────────────────────────────


def _stake(
    preset: ResearchPreset,
    gross_edge: Decimal,
    *,
    ref_edge: Decimal = Decimal("0.10"),
) -> Decimal:
    base = Decimal(str(max(preset.stake_size_usdc, 1.0)))
    if preset.sizing_policy == "edge_weighted":
        if gross_edge <= Decimal("0"):
            return base
        scale = max(Decimal("0.5"), min(gross_edge / ref_edge, Decimal("3")))
        return base * scale
    return base  # flat_small


# ── helpers ───────────────────────────────────────────────────────────────────


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _violation_window_id(rel_id: str, window_start_ts_ms: int) -> str:
    raw = f"{rel_id}|{window_start_ts_ms}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _pair_is_viable(
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
    for ra, rb in zip(sorted(rows_a, key=lambda r: r.ts_ms),
                      sorted(rows_b, key=lambda r: r.ts_ms), strict=False):
        pa, pb = float(ra.price), float(rb.price)
        if min_combined > 0 and pa + pb >= min_combined:
            return True
        if min_single > 0 and (pa >= min_single or pb >= min_single):
            return True
    return False


def _reject_row(
    rel: RelationshipCandidateRow,
    decision: ContextRelationshipDecisionRow,
    reason: str,
    preset: ResearchPreset,
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
        "preset_name": preset.preset_name,
        "preset_label": preset.label,
    }


def _make_trade_rows(
    rel: RelationshipCandidateRow,
    decision: ContextRelationshipDecisionRow,
    candidate: Any,
    stake: Decimal,
    run_id: str,
    preset: ResearchPreset,
    entry_kind: str,
    window_id: str,
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
            notional_usdc=stake,
            mid_price=price,
            execution_model=cfg.execution_model,
            fee_bps=cfg.fee_bps,
            slippage_bps=cfg.slippage_bps,
        )
        shares = stake / costs.fill_price if costs.fill_price else Decimal("0")
        total_fees += costs.fee_usdc
        total_slippage += costs.slippage_usdc
        rows.append({
            "trade_id": uuid.uuid4().hex,
            "trade_group_id": trade_group_id,
            "candidate_id": candidate.candidate_id,
            "run_id": run_id,
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
            "notional_usdc": str(stake),
            "fees_usdc": str(costs.fee_usdc),
            "slippage_cost_usdc": str(costs.slippage_usdc),
            "execution_model": cfg.execution_model,
            # Phase B enrichment
            "first_entry_or_reentry": entry_kind,
            "violation_window_id": window_id,
            "entry_policy": preset.entry_policy,
            "exit_policy": preset.exit_policy,
            "sizing_policy": preset.sizing_policy,
            "alignment_quality": getattr(candidate, "_alignment_quality", "unknown"),
            "gross_edge": str(candidate.gross_edge),
            "net_edge_after_cost": str(candidate.net_edge_after_costs),
            "preset_name": preset.preset_name,
            "preset_label": preset.label,
            "label": _LABEL,
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


def _filter_decisions(
    decisions: list[ContextRelationshipDecisionRow],
    cfg: ContextAwareBacktestConfig,
) -> list[ContextRelationshipDecisionRow]:
    """Reuse the lane-filter logic from context_aware_replay (local copy to stay standalone)."""
    reviewed = {"strict_context_valid", "reviewed_context_valid"}
    lane = cfg.lane

    # When the caller asks for ALL relationships with context decisions, honour it —
    # research_only/analysis_only lanes are still RESEARCH-ONLY but should not be
    # silently dropped here.  Downstream credibility labelling still applies.
    if cfg.relationship_universe == "all_with_context_decisions":
        return list(decisions)

    if cfg.relationship_universe == "reviewed_lanes":
        if lane == "all_context_research":
            return [d for d in decisions if d.strategy_lane in reviewed]
        if lane not in reviewed:
            return []

    if lane == "all_context_research":
        allowed = {"strict_context_valid", "reviewed_context_valid", "exploratory_context_unreviewed"}
        if cfg.include_auto_approved:
            allowed = allowed | {"exploratory_context_auto_approved"}
        return [d for d in decisions if d.strategy_lane in allowed]

    return [d for d in decisions if d.strategy_lane == lane]


# ── main entry point ──────────────────────────────────────────────────────────


def run_research_backtest(
    data_root: Path,
    cfg: ContextAwareBacktestConfig,
    preset: ResearchPreset,
) -> dict[str, Any]:
    """Run the exploratory research replay with configurable policies.

    RESEARCH-ONLY / EXPLORATORY. Never produces credible_positive.
    All trade rows carry entry_policy, exit_policy, sizing_policy,
    first_entry_or_reentry, violation_window_id, alignment_quality.

    Args:
        data_root: Root of the Parquet data lake.
        cfg: Already-preset-applied ContextAwareBacktestConfig (use apply_preset()).
        preset: The ResearchPreset supplying policy fields not in cfg.

    Returns:
        dict with keys: run_id, output_dir, metrics, funnel.
    """
    run_id = cfg.run_id or uuid.uuid4().hex
    cfg = cfg.model_copy(update={"run_id": run_id})
    out_dir = data_root / "backtests" / run_id / "research" / preset.preset_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── repositories ──────────────────────────────────────────────────────────
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    decision_repo = ParquetContextRelationshipDecisionsRepository(data_root)
    price_repo = ParquetPriceHistoryRepository(data_root)
    coverage_repo = ParquetBackfillCoverageRepository(data_root)

    rels = {r.relationship_id: r for r in rel_repo.iter_latest()}
    coverage = {r.market_id: r for r in coverage_repo.iter_latest()}
    all_decisions = list(decision_repo.iter_latest())
    decisions = _filter_decisions(all_decisions, cfg)

    # Further filter by relationship_universe
    if cfg.relationship_universe != "accepted_only":
        pass  # all decisions already included
    else:
        accepted_ids = {r.relationship_id for r in rels.values() if r.validation_status == "accepted"}
        decisions = [d for d in decisions if d.relationship_id in accepted_ids]

    # Drop decisions whose relationship is missing/confidence-too-low BEFORE we
    # consume memory on price prefetch.  This keeps the working set bounded when
    # an expanded universe yields tens of thousands of low-confidence rows.
    decisions = [
        d for d in decisions
        if (r := rels.get(d.relationship_id)) is not None
        and r.final_confidence >= cfg.min_relationship_confidence
        and r.token_id_a_yes
        and r.token_id_b_yes
    ]
    # Diagnostic-only subtypes (`same_topic_no_trade`, `same_reference_clock_only`,
    # `mixed_party_nomination`) are recorded as relationships but are NEVER
    # tradeable surfaces — the space-sweep contracts strip them from PnL
    # totals.  We also strip them from the replay's working set so they do
    # not consume the simulated cash budget and drown out real signals.
    _DIAGNOSTIC_ONLY_SUBTYPES = frozenset({
        "same_topic_no_trade",
        "same_reference_clock_only",
        "mixed_party_nomination",
    })
    decisions_pre_diag = len(decisions)
    decisions = [
        d for d in decisions
        if rels[d.relationship_id].relationship_subtype not in _DIAGNOSTIC_ONLY_SUBTYPES
    ]
    decisions_dropped_diagnostic = decisions_pre_diag - len(decisions)

    include_prefixes = tuple(preset.include_relationship_subtype_prefixes or [])
    exclude_prefixes = tuple(preset.exclude_relationship_subtype_prefixes or [])
    decisions_pre_source_filter = len(decisions)
    if include_prefixes:
        decisions = [
            d for d in decisions
            if (rels[d.relationship_id].relationship_subtype or "").startswith(include_prefixes)
        ]
    if exclude_prefixes:
        decisions = [
            d for d in decisions
            if not (rels[d.relationship_id].relationship_subtype or "").startswith(exclude_prefixes)
        ]
    decisions_dropped_source_filter = decisions_pre_source_filter - len(decisions)

    # Pre-filter to relationships whose tokens have price history.  Without
    # this we shard millions of empty-result token lookups.  The covered-token
    # set is loaded once from DuckDB.
    try:
        priced_tokens = price_repo.distinct_token_ids()
    except AttributeError:
        priced_tokens = None
    if priced_tokens is not None:
        decisions_pre = len(decisions)
        decisions = [
            d for d in decisions
            if rels[d.relationship_id].token_id_a_yes in priced_tokens
            and rels[d.relationship_id].token_id_b_yes in priced_tokens
        ]
        decisions_dropped_no_prices = decisions_pre - len(decisions)
    else:
        decisions_dropped_no_prices = 0

    # Sort by confidence so the highest-signal relationships always run first,
    # but no longer hard-cap.  The replay loop is sharded by `_BATCH_SIZE`
    # below so the price-history cache stays bounded in RAM.
    decisions.sort(
        key=lambda d: rels[d.relationship_id].final_confidence,
        reverse=True,
    )
    relationships_cap_applied = False

    # ── state ─────────────────────────────────────────────────────────────────
    funnel: dict[str, Any] = {
        "run_id": run_id,
        "preset_name": preset.preset_name,
        "preset_label": preset.label,
        "counts": {
            "relationships_loaded": len(decisions),
            "relationships_cap_applied": int(relationships_cap_applied),
            "relationships_dropped_no_prices": decisions_dropped_no_prices,
            "relationships_dropped_diagnostic_only_subtype": decisions_dropped_diagnostic,
            "relationships_dropped_source_filter": decisions_dropped_source_filter,
            "rejected_by_context": 0,
            "rejected_by_coverage": 0,
            "rejected_by_viability": 0,
            "rejected_by_alignment": 0,
            "aligned_price_series": 0,
            "price_history_present": 0,
            "ticks_evaluated": 0,
            "gross_violations": 0,
            "net_violations": 0,
            "entry_blocked_policy": 0,
            "entry_blocked_costs": 0,
            "candidates_accepted": 0,
        },
    }

    cash = cfg.starting_cash_usdc
    entry_states: dict[str, _EntryState] = defaultdict(_EntryState)
    open_positions: dict[str, dict[str, Any]] = {}  # rel_id → position summary for MtM
    window_starts: dict[str, int] = {}  # rel_id → ts_ms when current window started

    signals: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    # CappedList: bounded list of rejected rows so the expanded universe doesn't
    # blow RAM via a multi-million-row reject log.  We keep counts in `funnel`.
    _REJECT_CAP = 100_000
    _NO_LOOKAHEAD_CAP = 200_000
    rejected: list[dict[str, Any]] = []
    no_lookahead_rows: list[dict[str, Any]] = []

    def _bounded_append(target: list, item: dict, cap: int) -> None:
        if len(target) < cap:
            target.append(item)
    total_fees = Decimal("0")
    total_slippage = Decimal("0")
    cooldown_ms = preset.cooldown_minutes * 60 * 1000

    price_cache: dict[str, list[PriceHistoryRow]] = {}

    def _prices(token_id: str) -> list[PriceHistoryRow]:
        if token_id not in price_cache:
            price_cache[token_id] = list(price_repo.iter_for_token(token_id))
        return price_cache[token_id]

    # Sharded price-cache. We process the decision list in fixed-size batches:
    # before each batch we pre-fetch (in one DuckDB scan) the price history for
    # exactly that batch's tokens, run the decision loop for the batch, then
    # drop those tokens from the cache before the next batch.  This keeps RAM
    # bounded on the expanded universe without a hard 1,500-relationship cap.
    _BATCH_SIZE = 300
    funnel["counts"]["replay_batches"] = 0
    funnel["counts"]["replay_batch_size"] = _BATCH_SIZE

    def _prepare_shard(start: int) -> tuple[list, list[str]]:
        end = min(start + _BATCH_SIZE, len(decisions))
        shard_decisions = decisions[start:end]
        shard_tokens: list[str] = []
        seen: set[str] = set()
        for d in shard_decisions:
            r = rels.get(d.relationship_id)
            if r is None:
                continue
            for tok in (r.token_id_a_yes, r.token_id_b_yes):
                if tok and tok not in seen:
                    seen.add(tok)
                    shard_tokens.append(tok)
        if shard_tokens:
            for row in price_repo.iter_for_tokens(shard_tokens):
                price_cache.setdefault(row.token_id, []).append(row)
        funnel["counts"]["replay_batches"] += 1
        return shard_decisions, shard_tokens

    _shard_idx = 0
    _shard_decisions, _shard_tokens = _prepare_shard(_shard_idx)
    _decisions_seen_in_shard = 0

    # ── relationship loop ─────────────────────────────────────────────────────
    for decision in decisions:
        _decisions_seen_in_shard += 1
        if _decisions_seen_in_shard > len(_shard_decisions):
            # Crossed a shard boundary: drop the prior shard's tokens and load
            # the next shard.
            for tok in _shard_tokens:
                price_cache.pop(tok, None)
            _shard_idx += _BATCH_SIZE
            _shard_decisions, _shard_tokens = _prepare_shard(_shard_idx)
            _decisions_seen_in_shard = 1
        rel = rels.get(decision.relationship_id)
        if rel is None:
            funnel["counts"]["rejected_by_context"] += 1
            continue
        rel = normalize_context_backed_relationship(rel, decision.context_space_id)

        if rel.final_confidence < cfg.min_relationship_confidence:
            funnel["counts"]["rejected_by_context"] += 1
            _bounded_append(rejected, _reject_row(rel, decision, "confidence_below_threshold", preset), _REJECT_CAP)
            continue

        # In RESEARCH mode (all_with_context_decisions), we record every relationship
        # that has a decision regardless of `new_strategy_eligibility`.  Lane labels
        # still flow through to credibility downstream.  In stricter universes we
        # still gate on eligibility.
        if (
            cfg.relationship_universe != "all_with_context_decisions"
            and decision.new_strategy_eligibility != "eligible"
        ):
            funnel["counts"]["rejected_by_context"] += 1
            _bounded_append(rejected, _reject_row(rel, decision, "context_not_strategy_eligible", preset), _REJECT_CAP)
            continue

        cov_a = coverage.get(rel.market_id_a)
        cov_b = coverage.get(rel.market_id_b)
        if not _cov_ok(cov_a, cfg.min_coverage_score) or not _cov_ok(cov_b, cfg.min_coverage_score):
            funnel["counts"]["rejected_by_coverage"] += 1
            _bounded_append(rejected, _reject_row(rel, decision, "coverage_below_threshold", preset), _REJECT_CAP)
            continue

        if not rel.token_id_a_yes or not rel.token_id_b_yes:
            funnel["counts"]["rejected_by_context"] += 1
            _bounded_append(rejected, _reject_row(rel, decision, "missing_yes_tokens", preset), _REJECT_CAP)
            continue

        rows_a = _prices(rel.token_id_a_yes)
        rows_b = _prices(rel.token_id_b_yes)
        if not rows_a or not rows_b:
            funnel["counts"]["rejected_by_context"] += 1
            _bounded_append(rejected, _reject_row(rel, decision, "missing_price_history", preset), _REJECT_CAP)
            continue

        funnel["counts"]["price_history_present"] += 1

        if not _pair_is_viable(
            rel, rows_a, rows_b,
            min_combined=cfg.min_combined_prob_for_pairwise,
            min_single=cfg.min_single_prob_for_pairwise,
        ):
            funnel["counts"]["rejected_by_viability"] += 1
            _bounded_append(rejected, _reject_row(rel, decision, "pairwise_not_viable", preset), _REJECT_CAP)
            continue

        aligned = align_price_series(
            rows_a, rows_b,
            start_ts_ms=cfg.start_ts_ms,
            end_ts_ms=cfg.end_ts_ms,
            signal_interval_ms=cfg.signal_interval_ms,
            staleness_limit_ms=cfg.quote_staleness_limit_ms,
            alignment_mode=preset.alignment_mode,  # type: ignore[arg-type]
        )
        if not aligned:
            funnel["counts"]["rejected_by_alignment"] += 1
            _bounded_append(rejected, _reject_row(rel, decision, "alignment_failed", preset), _REJECT_CAP)
            continue

        funnel["counts"]["aligned_price_series"] += 1
        rel_id = rel.relationship_id
        entry_states[rel_id]  # ensure entry in defaultdict

        # ── tick loop ─────────────────────────────────────────────────────────
        # Sample no-lookahead audit rows at most 1-in-100 ticks per relationship
        # on large universes — full enumeration blows the RAM budget.
        _NO_LOOKAHEAD_SAMPLE = 100
        for _tick_idx, point in enumerate(aligned):
            funnel["counts"]["ticks_evaluated"] += 1

            # No-lookahead audit (sampled + bounded)
            if (
                _tick_idx % _NO_LOOKAHEAD_SAMPLE == 0
                and len(no_lookahead_rows) < _NO_LOOKAHEAD_CAP
            ):
                no_lookahead_rows.append({
                    "relationship_id": rel_id,
                    "signal_ts_ms": point.ts_ms,
                    "price_a_ts_ms": point.price_a_ts_ms,
                    "price_b_ts_ms": point.price_b_ts_ms,
                    "violation": (
                        point.price_a_ts_ms > point.ts_ms
                        or point.price_b_ts_ms > point.ts_ms
                    ),
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

            state = entry_states[rel_id]
            is_violation = candidate is not None

            if is_violation:
                funnel["counts"]["gross_violations"] += 1

                # Memory guard: on the expanded universe gross violations can
                # exceed several hundred thousand rows.  Cap the persisted
                # signal sample at 50k (still gives the surface a healthy
                # audit trail without OOMing).
                if len(signals) < 50_000:
                    sig = _sig_row(rel, decision, candidate, point, preset)
                    signals.append(sig)

                if candidate.accepted_for_simulation:
                    funnel["counts"]["net_violations"] += 1

                    # Update window tracking
                    if not state.prev_tick_had_violation:
                        window_starts[rel_id] = point.ts_ms
                    win_id = _violation_window_id(rel_id, window_starts.get(rel_id, point.ts_ms))

                    enter, entry_kind = _should_enter(
                        rel_id, point.ts_ms,
                        preset.entry_policy,
                        cooldown_ms,
                        preset.max_trades_per_relationship,
                        entry_states,
                        is_violation=True,
                    )

                    if not enter:
                        funnel["counts"]["entry_blocked_policy"] += 1
                        _bounded_append(
                            rejected,
                            {
                                **_reject_row(rel, decision, entry_kind, preset),
                                "signal_ts_ms": point.ts_ms,
                                "gross_edge": str(candidate.gross_edge),
                            },
                            _REJECT_CAP,
                        )
                    elif not preset.execute_trades:
                        # gross_violation_scan: record but don't execute
                        funnel["counts"]["candidates_accepted"] += 1
                        state.trade_count += 1
                        state.last_trade_ts_ms = point.ts_ms
                    else:
                        # Check cash
                        stake = _stake(preset, candidate.gross_edge)
                        required = stake * 2
                        if cash < required and not cfg.allow_negative_cash:
                            funnel["counts"]["entry_blocked_costs"] += 1
                            _bounded_append(
                                rejected,
                                {
                                    **_reject_row(rel, decision, "cash_limit", preset),
                                    "signal_ts_ms": point.ts_ms,
                                },
                                _REJECT_CAP,
                            )
                        else:
                            leg_rows, leg_fees, leg_slippage = _make_trade_rows(
                                rel, decision, candidate, stake, run_id, preset,
                                entry_kind, win_id, cfg,
                            )
                            trades.extend(leg_rows)
                            cash -= (stake * 2 + leg_fees)
                            total_fees += leg_fees
                            total_slippage += leg_slippage
                            funnel["counts"]["candidates_accepted"] += 1
                            state.trade_count += 1
                            state.last_trade_ts_ms = point.ts_ms
                            open_positions[rel_id] = {
                                "open_ts_ms": point.ts_ms,
                                "stake": str(stake),
                            }

            else:
                # No violation — close on reversal if needed
                if preset.exit_policy == "close_on_reversal" and rel_id in open_positions:
                    open_positions.pop(rel_id)
                    # Return half the stake as a notional credit (simplified MtM)
                    stake_back = Decimal(open_positions.get(rel_id, {}).get("stake", "0")) or Decimal("0")
                    cash += stake_back  # approximate

            state.prev_tick_had_violation = is_violation

    # ── end-of-run MtM ────────────────────────────────────────────────────────
    mtm = _mark_to_market(trades, price_repo)
    ending_equity = cash + mtm
    net_pnl = ending_equity - cfg.starting_cash_usdc

    trades_executed = len(trades) // 2
    funnel["counts"]["trades_executed"] = trades_executed
    funnel["counts"]["net_pnl_usdc"] = str(net_pnl)
    funnel["credibility_label"] = "exploratory_only_not_credible"
    funnel["label"] = _LABEL
    funnel["note"] = _NOTE

    no_lookahead_summary = {
        "rows_checked": len(no_lookahead_rows),
        "violations": sum(1 for r in no_lookahead_rows if r["violation"]),
    }

    metrics: dict[str, Any] = {
        "run_id": run_id,
        "preset_name": preset.preset_name,
        "preset_label": preset.label,
        "lane": cfg.lane,
        "relationship_universe": cfg.relationship_universe,
        "entry_policy": preset.entry_policy,
        "exit_policy": preset.exit_policy,
        "sizing_policy": preset.sizing_policy,
        "alignment_mode": preset.alignment_mode,
        "include_relationship_subtype_prefixes": preset.include_relationship_subtype_prefixes,
        "exclude_relationship_subtype_prefixes": preset.exclude_relationship_subtype_prefixes,
        "starting_cash_usdc": str(cfg.starting_cash_usdc),
        "ending_cash_usdc": str(cash),
        "ending_equity_usdc": str(ending_equity),
        "net_pnl_usdc": str(net_pnl),
        "total_fees_usdc": str(total_fees),
        "total_slippage_usdc": str(total_slippage),
        "trades_executed": trades_executed,
        "distinct_relationships_traded": len({t["relationship_id"] for t in trades}),
        "signals_generated": len(signals),
        "credibility_label": "exploratory_only_not_credible",
        "label": _LABEL,
        "note": _NOTE,
    }

    _write_json(out_dir / "config.json", cfg.model_dump(mode="json"))
    _write_json(out_dir / "preset.json", preset.model_dump())
    _write_json(out_dir / "metrics.json", metrics)
    _write_json(out_dir / "funnel.json", funnel)
    _write_json(out_dir / "no_lookahead_audit.json", no_lookahead_summary)
    _write_csv(out_dir / "signals.csv", signals)
    _write_csv(out_dir / "trades.csv", trades)
    _write_csv(out_dir / "rejected_candidates.csv", rejected)
    _write_csv(out_dir / "funnel_audit.json.csv", [funnel["counts"]])

    return {
        "run_id": run_id,
        "output_dir": out_dir,
        "metrics": metrics,
        "funnel": funnel,
    }


# ── helpers ───────────────────────────────────────────────────────────────────


def _cov_ok(cov: Any, min_score: float) -> bool:
    if min_score <= 0.0:
        return True
    if cov is None:
        return False
    return float(getattr(cov, "coverage_score", 0)) >= min_score


def _sig_row(
    rel: RelationshipCandidateRow,
    decision: ContextRelationshipDecisionRow,
    candidate: Any,
    point: Any,
    preset: ResearchPreset,
) -> dict[str, Any]:
    from dataclasses import asdict
    row = asdict(candidate)
    row["strategy_lane"] = decision.strategy_lane
    row["context_space_id"] = decision.context_space_id
    row["alignment_quality"] = point.alignment_quality
    row["staleness_a_ms"] = point.staleness_a_ms
    row["staleness_b_ms"] = point.staleness_b_ms
    row["preset_name"] = preset.preset_name
    row["preset_label"] = preset.label
    row["label"] = _LABEL
    return row


def _mark_to_market(
    trades: list[dict[str, Any]],
    price_repo: ParquetPriceHistoryRepository,
) -> Decimal:
    prices: dict[str, Decimal] = {}
    for t in trades:
        tok = str(t.get("token_id", ""))
        if tok and tok not in prices:
            rows = list(price_repo.iter_for_token(tok))
            if rows:
                prices[tok] = max(rows, key=lambda r: r.ts_ms).price
    return sum(
        Decimal(str(t.get("shares", "0"))) * prices.get(str(t.get("token_id", "")), Decimal("0"))
        for t in trades
    )


def _write_json(path: Path, payload: Any) -> None:
    def _ser(v: Any) -> Any:
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, Path):
            return str(v)
        if isinstance(v, dict):
            return {k: _ser(vv) for k, vv in v.items()}
        if isinstance(v, (list, tuple)):
            return [_ser(vv) for vv in v]
        return v
    path.write_text(json.dumps(_ser(payload), indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    import csv
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({k for row in rows for k in row})
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
