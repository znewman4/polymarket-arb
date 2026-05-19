"""Adapters: convert raw trade rows from each backtest source into ``StandardisedTradeRow``.

The legacy backtest engines (context_aware_replay, research_replay,
relationship_replay, diagnostic_replay, closed_form_simulators, null_baseline,
template_bundle_replay) each emit their own ad-hoc trade CSV / dict.  We do NOT
rewrite those engines.  Instead, we read their outputs and adapt them here so
every trade ends up in the same ``StandardisedTradeRow`` shape.

DeepSeek pre-trade justifications are loaded separately from
``deepseek_hypotheses.jsonl`` (or any equivalent jsonl) and matched onto trade
rows by ``hypothesis_id``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...backtest.resolutions import InferredResolution, infer_resolutions
from ...storage.base import MarketRow
from ...storage.parquet.markets_repo import ParquetMarketsRepository
from ...storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from .contract import (
    DeepSeekPreTradeJustification,
    StandardisedTradeRow,
)

# ── public API ───────────────────────────────────────────────────────────────


def _standardised_group_id(source_lane: str, subrun_id: str, raw_group_id: str) -> str:
    """Scope source trade groups by lane/subrun to prevent cross-lane collisions."""
    raw = raw_group_id or uuid.uuid4().hex
    return hashlib.sha256(f"{source_lane}|{subrun_id}|{raw}".encode()).hexdigest()[:32]


def adapt_replay_trade_rows(
    raw_rows: Iterable[dict[str, Any]],
    *,
    run_id: str,
    subrun_id: str,
    source_lane: str,
    source_agent: str,
    source_preset: str = "",
    trade_kind: str = "replay_fill",
    reason_default: str = "",
    is_rulebook: bool = False,
    is_ai_generated: bool = False,
    is_exploratory: bool = False,
    is_strict_validation: bool = False,
    is_control: bool = False,
    is_diagnostic: bool = False,
    justification_by_hypothesis: dict[str, DeepSeekPreTradeJustification] | None = None,
    latest_price_lookup: dict[str, tuple[int, float] | float] | None = None,
    paired_exit_lookup: dict[str, tuple[int | None, float | None]] | None = None,
    resolution_lookup: dict[str, InferredResolution] | None = None,
) -> list[StandardisedTradeRow]:
    """Adapt raw fill-leg dicts (the trades.csv emitted by replay engines) into
    standardised rows.

    ``justification_by_hypothesis`` maps hypothesis_id → pre-trade justification.
    Only DeepSeek-originated trades are expected to have a match; other sources
    leave ``justification_present=False``.

    ``latest_price_lookup`` maps token_id → latest known price.  Used to fill
    ``mark_price``/``exit_price`` when the source did not record an explicit exit.

    ``paired_exit_lookup`` maps trade_group_id → (exit_ts_ms, mid_price_pair_avg)
    when an explicit exit was recorded; otherwise the latest price stands in.
    """
    justification_by_hypothesis = justification_by_hypothesis or {}
    latest_price_lookup = latest_price_lookup or {}
    paired_exit_lookup = paired_exit_lookup or {}
    resolution_lookup = resolution_lookup or {}

    now_ms = _now_ms()
    out: list[StandardisedTradeRow] = []
    for raw in raw_rows:
        raw_trade_group_id = (
            str(raw.get("trade_group_id") or "")
            or str(raw.get("candidate_id") or "")
            or str(raw.get("bundle_event_id") or "")
            or str(raw.get("trade_id") or uuid.uuid4().hex)
        )
        trade_group_id = _standardised_group_id(source_lane, subrun_id, raw_trade_group_id)
        token_id = str(raw.get("token_id") or "")
        entry_price = _float_or_none(raw.get("fill_price") or raw.get("entry_price"))
        shares = _float(raw.get("shares"))
        fees = _float(raw.get("fees_usdc"))
        slippage_cost = _float(raw.get("slippage_cost_usdc") or raw.get("slippage_usdc"))
        notional = _float(raw.get("notional_usdc"))
        stake = notional or (shares * entry_price if entry_price else 0.0)
        entry_cost = stake + fees

        gross_edge = _float_or_none(raw.get("gross_edge"))
        net_edge = _float_or_none(raw.get("net_edge_after_cost") or raw.get("net_edge"))

        entry_ts = _int_or_none(raw.get("fill_ts_ms") or raw.get("entry_ts_ms"))
        exit_ts, exit_price = paired_exit_lookup.get(raw_trade_group_id, (None, None))
        if exit_price is None and token_id:
            latest = latest_price_lookup.get(token_id)
            if isinstance(latest, tuple):
                latest_ts, latest_px = latest
                exit_price = latest_px
                if exit_ts is None:
                    exit_ts = latest_ts
            elif isinstance(latest, (int, float)):
                exit_price = float(latest)
                if exit_ts is None:
                    exit_ts = entry_ts  # legacy callers passing bare floats — no ts info
        holding_period_ms = (
            exit_ts - entry_ts if (entry_ts is not None and exit_ts is not None) else None
        )

        mark_value = shares * exit_price if exit_price is not None else None
        mark_pnl = (mark_value - stake - fees) if mark_value is not None else None
        edge_implied_pnl = net_edge * stake if net_edge is not None else None

        realised_pnl = _float_or_none(raw.get("realised_pnl_usdc"))
        resolution_outcome_raw = raw.get("resolution_outcome")
        resolution_outcome: str | None = (
            str(resolution_outcome_raw) if resolution_outcome_raw not in (None, "") else None
        )
        resolution_ts_ms = _int_or_none(raw.get("resolution_ts_ms"))

        # ── Resolution-driven realised PnL ────────────────────────────────────
        # If the legacy engine didn't already compute realised_pnl_usdc, look up
        # the inferred resolution for this market and pay the leg out at 0 or 1
        # depending on whether the bought token is YES or NO and the inferred
        # outcome.  Markets without a resolution stay None.
        market_id = str(raw.get("market_id") or "")
        if realised_pnl is None and market_id and market_id in resolution_lookup:
            inferred = resolution_lookup[market_id]
            if resolution_outcome is None:
                resolution_outcome = inferred.yes_outcome
            if resolution_ts_ms is None:
                resolution_ts_ms = inferred.resolution_ts_ms or None
            if inferred.yes_outcome in ("yes", "no"):
                yes_token = inferred.token_id_yes
                no_token = inferred.token_id_no
                if token_id == yes_token:
                    payout_per_share = 1.0 if inferred.yes_outcome == "yes" else 0.0
                elif token_id == no_token:
                    payout_per_share = 1.0 if inferred.yes_outcome == "no" else 0.0
                else:
                    payout_per_share = None
                if payout_per_share is not None:
                    payout = shares * payout_per_share
                    realised_pnl = payout - stake - fees

        ej = {}
        try:
            ej = json.loads(str(raw.get("evidence_json") or "{}"))
        except json.JSONDecodeError:
            ej = {}

        hypothesis_source = (
            str(raw.get("hypothesis_source") or ej.get("hypothesis_source") or "")
            or "deterministic_relationship"
        )
        hypothesis_id = str(raw.get("hypothesis_id") or ej.get("hypothesis_id") or "")
        justification = justification_by_hypothesis.get(hypothesis_id) if hypothesis_id else None

        leg_raw = str(raw.get("leg") or "single").lower()
        leg = leg_raw if leg_raw in ("a", "b", "single") else "single"

        reason = _build_reason(raw, source_lane, source_agent, reason_default)

        row = StandardisedTradeRow(
            trade_id=str(raw.get("trade_id") or uuid.uuid4().hex),
            trade_group_id=trade_group_id,
            raw_trade_group_id=raw_trade_group_id,
            run_id=run_id,
            subrun_id=subrun_id,
            source_lane=source_lane,
            source_agent=source_agent,
            source_preset=source_preset,
            trade_kind=trade_kind,  # type: ignore[arg-type]
            is_rulebook=is_rulebook,
            is_ai_generated=is_ai_generated,
            is_exploratory=is_exploratory,
            is_strict_validation=is_strict_validation,
            is_control=is_control,
            is_diagnostic=is_diagnostic,
            reason=reason,
            relationship_id=str(raw.get("relationship_id") or ""),
            relationship_family=str(raw.get("relationship_family") or ""),
            relationship_type=str(raw.get("relationship_type") or ""),
            relationship_subtype=str(raw.get("relationship_subtype") or ""),
            context_space_id=str(raw.get("context_space_id") or ""),
            outcome_space_id=str(raw.get("outcome_space_id") or ""),
            strategy_lane=str(raw.get("strategy_lane") or ""),
            hypothesis_source=hypothesis_source,
            hypothesis_id=hypothesis_id,
            hypothesis_model_name=str(raw.get("hypothesis_model_name") or ej.get("model_name") or ""),
            hypothesis_model_type=str(raw.get("hypothesis_model_type") or ej.get("model_type") or ""),
            hypothesis_prompt_version=str(raw.get("hypothesis_prompt_version") or ej.get("prompt_version") or ""),
            hypothesis_confidence=_float_or_none(
                raw.get("hypothesis_confidence") or ej.get("confidence")
            ),
            hypothesis_relationship_type=str(
                raw.get("hypothesis_relationship_type") or ej.get("hypothesis_relationship_type") or ""
            ),
            leg=leg,  # type: ignore[arg-type]
            token_id=token_id,
            market_id=str(raw.get("market_id") or ""),
            side="buy",
            entry_ts_ms=entry_ts,
            exit_ts_ms=exit_ts,
            holding_period_ms=holding_period_ms,
            entry_price=entry_price,
            exit_price=exit_price,
            mark_price=exit_price,
            shares=shares,
            stake_usdc=stake,
            notional_usdc=notional or stake,
            fees_usdc=fees,
            slippage_cost_usdc=slippage_cost,
            entry_cost_usdc=entry_cost,
            execution_model=str(raw.get("execution_model") or "price_history_only"),
            gross_edge=gross_edge,
            net_edge_after_cost=net_edge,
            edge_implied_pnl_usdc=edge_implied_pnl,
            mark_to_market_value_usdc=mark_value,
            mark_to_market_pnl_usdc=mark_pnl,
            realised_pnl_usdc=realised_pnl,
            resolution_outcome=resolution_outcome,  # type: ignore[arg-type]
            resolution_ts_ms=resolution_ts_ms,
            ingested_ts_ms=now_ms,
        )
        _attach_justification(row, justification)
        _attach_percentages(row)
        out.append(row)
    return out


def adapt_closed_form_simulator_trades(
    sim_trades: Iterable[Any],
    *,
    run_id: str,
    subrun_id: str = "closed_form_simulators",
    source_preset: str = "deepseek_freereign_simulators",
    source_lane: str = "closed_form_simulator",
    source_agent: str | None = None,
    is_diagnostic: bool = False,
    is_control: bool = False,
    is_ai_generated: bool = True,
    relationship_family: str = "closed_form_hypothesis",
    hypothesis_source: str = "closed_form",
    justification_by_hypothesis: dict[str, DeepSeekPreTradeJustification] | None = None,
) -> list[StandardisedTradeRow]:
    """Adapt ``SimulatedTrade`` objects from closed_form_simulators into standardised rows.

    A simulated trade represents a single "virtual" pair-trade with two prices —
    we emit one standardised row per leg (a, b) so it lines up with replay output.
    """
    justification_by_hypothesis = justification_by_hypothesis or {}
    now_ms = _now_ms()
    out: list[StandardisedTradeRow] = []
    for t in sim_trades:
        raw_group_id = f"{t.hypothesis_id}|{t.simulator}"
        group_id = _standardised_group_id(source_lane, subrun_id, raw_group_id)
        justification = justification_by_hypothesis.get(t.hypothesis_id)
        for leg, token_id, entry_price_v, exit_price_v in (
            ("a", t.token_id_a, t.entry_price_a, t.exit_price_a),
            ("b", t.token_id_b, t.entry_price_b, t.exit_price_b),
        ):
            entry_cost = float(t.entry_cost_per_dollar)  # normalised per $1 stake
            stake = 1.0  # closed-form trades are stake-invariant; we report return_pct directly
            mark_value = exit_price_v  # one unit of "spread" position
            row = StandardisedTradeRow(
                trade_id=hashlib.sha256(f"{group_id}|{leg}".encode()).hexdigest()[:32],
                trade_group_id=group_id,
                raw_trade_group_id=raw_group_id,
                run_id=run_id,
                subrun_id=subrun_id,
                source_lane=source_lane,
                source_agent=source_agent or f"closed_form_{t.simulator}",
                source_preset=source_preset,
                trade_kind="closed_form",
                is_rulebook=False,
                is_ai_generated=is_ai_generated,
                is_exploratory=not is_diagnostic,
                is_control=is_control,
                is_diagnostic=is_diagnostic,
                reason=t.notes or f"closed-form simulator: {t.simulator}",
                relationship_type=t.relationship_type,
                relationship_subtype=f"closed_form_{t.simulator}",
                relationship_family=relationship_family,
                hypothesis_source=hypothesis_source,
                hypothesis_id=t.hypothesis_id,
                leg=leg,  # type: ignore[arg-type]
                token_id=token_id,
                market_id=t.market_id_a if leg == "a" else t.market_id_b,
                side="virtual_short" if "overround" in t.simulator else "buy",
                entry_ts_ms=int(t.entry_ts_ms),
                exit_ts_ms=int(t.exit_ts_ms),
                holding_period_ms=int(t.exit_ts_ms) - int(t.entry_ts_ms),
                entry_price=float(entry_price_v),
                exit_price=float(exit_price_v),
                mark_price=float(exit_price_v),
                shares=1.0,
                stake_usdc=stake,
                notional_usdc=stake,
                fees_usdc=0.0,
                slippage_cost_usdc=float(t.slippage_haircut_pct) * entry_cost,
                entry_cost_usdc=entry_cost,
                gross_edge=float(t.edge_implied_return_pct),
                net_edge_after_cost=float(t.edge_implied_return_pct),
                edge_implied_pnl_usdc=float(t.edge_implied_return_pct) * stake,
                mark_to_market_value_usdc=mark_value,
                mark_to_market_pnl_usdc=float(t.realised_return_pct) * entry_cost,
                edge_implied_return_pct=float(t.edge_implied_return_pct),
                mark_to_market_return_pct=float(t.realised_return_pct),
                realised_return_pct=float(t.realised_return_pct),
                trade_cost_adjusted_return_pct=float(t.edge_implied_return_pct),
                ingested_ts_ms=now_ms,
            )
            _attach_justification(row, justification)
            out.append(row)
    return out


# ── DeepSeek justification loader ────────────────────────────────────────────


def load_deepseek_justifications_jsonl(
    path: Path,
) -> dict[str, DeepSeekPreTradeJustification]:
    """Load DeepSeek hypotheses from a ``deepseek_hypotheses.jsonl`` (or equivalent).

    Tolerant of the legacy JSONL format emitted by
    ``deepseek_exploratory_backtest._normalise_deepseek_response`` — fields are
    re-mapped to the standardised pre-trade justification shape.
    """
    out: dict[str, DeepSeekPreTradeJustification] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        hyp_id = str(raw.get("hypothesis_id") or "")
        if not hyp_id:
            continue
        out[hyp_id] = _build_justification(raw)
    return out


def load_deepseek_justifications_iterable(
    hypotheses: Iterable[dict[str, Any]],
) -> dict[str, DeepSeekPreTradeJustification]:
    """Build a justification map from an in-memory iterable of hypotheses."""
    out: dict[str, DeepSeekPreTradeJustification] = {}
    for raw in hypotheses:
        hyp_id = str(raw.get("hypothesis_id") or "")
        if not hyp_id:
            continue
        out[hyp_id] = _build_justification(raw)
    return out


def _build_justification(raw: dict[str, Any]) -> DeepSeekPreTradeJustification:
    relationship_claim = (
        str(raw.get("reasoning_summary") or "")
        or str(raw.get("evidence_summary") or "")
        or f"{raw.get('relationship_type')} between {raw.get('market_id_a')} and {raw.get('market_id_b')}"
    )
    why_works = (
        str(raw.get("why_relationship_may_be_tradeable") or "")
        or str(raw.get("proposed_trade_logic") or "")
        or "(model did not provide a tradeable mechanism — flag for manual review)"
    )
    why_fail = (
        str(raw.get("why_relationship_may_be_invalid") or "")
        or str(raw.get("why_relationship_may_be_wrong") or "")
        or "(model did not provide a failure-mode argument — flag for manual review)"
    )
    evidence = str(raw.get("evidence_summary") or raw.get("resolution_criteria_comparison") or "")
    simulator = (
        str(raw.get("proposed_simulator") or "")
        or str(raw.get("route_handler") or "")
        or str(raw.get("routing_decision") or "")
    )
    confidence = float(raw.get("confidence") or 0.0)
    uncertainty = raw.get("uncertainty_flags")
    if isinstance(uncertainty, list):
        uncertainty_json = json.dumps(uncertainty)
    else:
        uncertainty_json = str(uncertainty or "[]")
    failure_modes = raw.get("failure_modes")
    if isinstance(failure_modes, list):
        failure_modes_json = json.dumps(failure_modes)
    else:
        failure_modes_json = str(failure_modes or "[]")
    inside = not bool(raw.get("outside_existing_deterministic_rulebook"))
    outside = bool(raw.get("outside_existing_deterministic_rulebook"))
    return DeepSeekPreTradeJustification(
        hypothesis_id=str(raw.get("hypothesis_id") or ""),
        relationship_claim=relationship_claim[:1000],
        why_trade_should_work=why_works[:1000],
        why_trade_may_fail=why_fail[:1000],
        evidence_used=evidence[:1000],
        simulator_or_replay_method=simulator,
        confidence=max(0.0, min(1.0, confidence)),
        uncertainty_flags_json=uncertainty_json,
        failure_modes_json=failure_modes_json,
        inside_existing_rulebook=inside,
        outside_existing_rulebook=outside,
        model_name=str(raw.get("model_name") or ""),
        model_type=str(raw.get("model_type") or "generative"),
        prompt_version=str(raw.get("prompt_version") or ""),
        routing_decision=str(raw.get("routing_decision") or raw.get("route") or ""),
        falsification_test=str(raw.get("falsification_test") or "")[:1000],
        proposed_trade_logic=str(raw.get("proposed_trade_logic") or "")[:1000],
        raw_response_json=json.dumps(raw, default=str, sort_keys=True)[:8000],
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def read_trades_csv(path: Path) -> list[dict[str, Any]]:
    """Read a trades.csv emitted by one of the legacy replay engines."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


def latest_price_lookup_for(
    data_root: Path,
    trade_rows: Iterable[dict[str, Any]],
) -> dict[str, tuple[int, float]]:
    """Build a token_id → (latest_ts_ms, latest_price) map.  One-shot scan of distinct tokens."""
    tokens = {str(r.get("token_id") or "") for r in trade_rows if r.get("token_id")}
    repo = ParquetPriceHistoryRepository(data_root)
    out: dict[str, tuple[int, float]] = {}
    for row in repo.iter_for_tokens(tok for tok in tokens if tok):
        current = out.get(row.token_id)
        if current is None or int(row.ts_ms) > current[0]:
            out[row.token_id] = (int(row.ts_ms), float(row.price))
    return out


def build_resolution_lookup(
    data_root: Path,
    trade_rows: Iterable[dict[str, Any]],
    *,
    markets: list[MarketRow] | None = None,
    price_convergence_epsilon: float = 0.02,
    price_convergence_lookback_ms: int = 7 * 24 * 3600 * 1000,
) -> dict[str, InferredResolution]:
    """Build a market_id → InferredResolution map for every market a trade touches.

    Two-pass:
    1. ``backtest.resolutions.infer_resolutions`` (price-convergence + closed-flag).
       This honours the upstream contract — open markets stay unresolved.
    2. **Price-convergence-only override** for markets whose local catalog
       metadata is stale (``closed=False`` even though the market has clearly
       resolved on-chain).  If the YES token has been within
       ``price_convergence_epsilon`` of 0 or 1 for the full
       ``price_convergence_lookback_ms`` window, we record an inferred resolution.
       This is conservative — only obvious paid-out markets flip; ambiguous ones
       stay unresolved.

    Markets whose YES token has insufficient price history get
    ``yes_outcome="unresolved"`` and stay flagged as such — we never silently
    default to ``"no"``.
    """
    target_market_ids = {str(r.get("market_id") or "") for r in trade_rows if r.get("market_id")}
    if not target_market_ids:
        return {}
    if markets is None:
        repo = ParquetMarketsRepository(data_root)
        markets = list(repo.get_many_markets(target_market_ids).values())
    markets = [m for m in markets if m.id in target_market_ids]
    price_repo = ParquetPriceHistoryRepository(data_root)
    yes_tokens = [m.clob_token_ids[0] for m in markets if len(m.clob_token_ids) >= 1]
    prices_by_token: dict[str, list] = {}
    for row in price_repo.iter_for_tokens(yes_tokens):
        prices_by_token.setdefault(row.token_id, []).append(row)

    result = infer_resolutions(markets, prices_by_token)

    # ── Price-convergence-only override ─────────────────────────────────────
    market_by_id = {m.id: m for m in markets}
    for market_id, inferred in list(result.items()):
        if inferred.yes_outcome in ("yes", "no"):
            continue
        m = market_by_id.get(market_id)
        if m is None or not m.clob_token_ids:
            continue
        yes_token = m.clob_token_ids[0]
        no_token = m.clob_token_ids[1] if len(m.clob_token_ids) > 1 else ""
        rows = prices_by_token.get(yes_token, [])
        if not rows:
            continue
        sorted_rows = sorted(rows, key=lambda r: r.ts_ms)
        cutoff = sorted_rows[-1].ts_ms - price_convergence_lookback_ms
        window = [float(r.price) for r in sorted_rows if r.ts_ms >= cutoff]
        if not window:
            continue
        avg_window = sum(window) / len(window)
        if avg_window >= 1.0 - price_convergence_epsilon:
            outcome = "yes"
            conf = avg_window
        elif avg_window <= price_convergence_epsilon:
            outcome = "no"
            conf = 1.0 - avg_window
        else:
            continue
        # Resolution timestamp = EARLIEST point at which the price entered the
        # converged zone and stayed there.  This matters for the causality gate:
        # a trade entered after this ts has access to the outcome and is leakage.
        resolution_ts = _first_convergence_ts_ms(
            sorted_rows, outcome=outcome, epsilon=price_convergence_epsilon,
        )
        result[market_id] = InferredResolution(
            market_id=market_id,
            token_id_yes=yes_token,
            token_id_no=no_token,
            resolution_ts_ms=resolution_ts,
            yes_outcome=outcome,  # type: ignore[arg-type]
            confidence=float(conf),
            inference_method="price_convergence",
        )
    return result


def _first_convergence_ts_ms(
    sorted_rows: list,
    *,
    outcome: str,
    epsilon: float,
) -> int:
    """Find the earliest tick where the price entered the converged zone and
    stayed converged through the last observation.

    For outcome="yes" the converged zone is [1-epsilon, 1.0].
    For outcome="no"  the converged zone is [0.0, epsilon].
    """
    if not sorted_rows:
        return 0
    # Walk backward; the first time we encounter a NON-converged tick, the
    # convergence began at the NEXT tick after it.  If we never find one,
    # the price has always been converged → use the earliest tick.
    threshold_hi = 1.0 - epsilon
    threshold_lo = epsilon
    for i in range(len(sorted_rows) - 1, -1, -1):
        p = float(sorted_rows[i].price)
        if outcome == "yes" and p < threshold_hi:
            # Convergence started at i+1 (or last tick if i was the very end)
            j = min(i + 1, len(sorted_rows) - 1)
            return int(sorted_rows[j].ts_ms)
        if outcome == "no" and p > threshold_lo:
            j = min(i + 1, len(sorted_rows) - 1)
            return int(sorted_rows[j].ts_ms)
    return int(sorted_rows[0].ts_ms)


def _attach_justification(
    row: StandardisedTradeRow,
    justification: DeepSeekPreTradeJustification | None,
) -> None:
    if justification is None:
        return
    row.justification_present = True
    row.justification_relationship_claim = justification.relationship_claim
    row.justification_why_works = justification.why_trade_should_work
    row.justification_why_may_fail = justification.why_trade_may_fail
    row.justification_evidence = justification.evidence_used
    row.justification_simulator_or_method = justification.simulator_or_replay_method
    row.justification_confidence = justification.confidence
    row.justification_uncertainty_flags_json = justification.uncertainty_flags_json
    row.justification_inside_rulebook = justification.inside_existing_rulebook
    row.justification_outside_rulebook = justification.outside_existing_rulebook
    row.justification_json = justification.model_dump_json()


def _attach_percentages(row: StandardisedTradeRow) -> None:
    cost = row.entry_cost_usdc
    if cost and cost > 0:
        if row.edge_implied_pnl_usdc is not None and row.edge_implied_return_pct is None:
            row.edge_implied_return_pct = row.edge_implied_pnl_usdc / cost
        if row.mark_to_market_pnl_usdc is not None and row.mark_to_market_return_pct is None:
            row.mark_to_market_return_pct = row.mark_to_market_pnl_usdc / cost
        if row.realised_pnl_usdc is not None and row.realised_return_pct is None:
            row.realised_return_pct = row.realised_pnl_usdc / cost
        if row.trade_cost_adjusted_return_pct is None:
            primary = (
                row.edge_implied_pnl_usdc
                if row.edge_implied_pnl_usdc is not None
                else row.mark_to_market_pnl_usdc
            )
            if primary is not None:
                row.trade_cost_adjusted_return_pct = primary / cost


def _build_reason(
    raw: dict[str, Any],
    source_lane: str,
    source_agent: str,
    reason_default: str,
) -> str:
    explicit = str(raw.get("reason") or "").strip()
    if explicit:
        return explicit
    if reason_default:
        return reason_default
    parts = [source_lane, source_agent]
    if raw.get("relationship_subtype"):
        parts.append(str(raw["relationship_subtype"]))
    if raw.get("gross_edge"):
        parts.append(f"gross_edge={raw['gross_edge']}")
    return " | ".join(p for p in parts if p)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _float(v: Any, default: float = 0.0) -> float:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _float_or_none(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_none(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
