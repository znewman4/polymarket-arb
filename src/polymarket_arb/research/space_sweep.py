"""Space sweep — aggregate across ALL relationship/context/bundle/backtest sources.

The space universe is built in two stages:

Stage 1 — Store universe (Parquet):
  Every relationship in the Parquet store contributes to a space row regardless
  of whether it ever appeared in a backtest run.  This surfaces:
    * spaces that have relationships but no price history (→ C_INFRASTRUCTURE_BLOCKED)
    * spaces with price history but no violations (→ D_VALID_BUT_STRATEGICALLY_WEAK)
    * spaces dominated by diagnostic-only relationships (→ E_INVALID_OR_AUDIT_RISK)
    * spaces not yet analysed in any backtest run

Stage 2 — Backtest run merge:
  Signals, trades, rejected candidates, and bundle data from a specific backtest
  run_id are layered on top of the store universe.

Only after both stages are merged does grading happen, so Grade B/C/D/E spaces
are visible even when they produced zero accepted trades.

Typed-row enforcement:
  * diagnostic-only subtypes (same_topic_no_trade, etc.) never enter accepted-trade
    or gross-violation totals — they go to diagnostic_only_rel_count only.
  * Accepted trades without matching gross_violation_count > 0 get an integrity_warning.

RESEARCH-ONLY.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .row_contracts import (
    AcceptedSimulatedTradeRow,
    BlockedOpportunityRow,
    BundleAcceptedTradeRow,
    BundleDiagnosticRow,
    DiagnosticOnlyRelationshipRow,
    SpaceAttributionStatus,
    SpaceGrade,
    SpaceSummaryRow,
    is_diagnostic_only_subtype,
    space_id_for,
)

_REPORT_LABEL = "RESEARCH-ONLY — space sweep, diagnostic / exploratory only"

_STRICT_LANES = frozenset({"strict_context_valid", "reviewed_context_valid"})
_EXPLORATORY_LANES = frozenset({
    "exploratory_context_unreviewed",
    "exploratory_context_auto_approved",
    "all_context_research",
})


@dataclass
class SpaceSweepResult:
    run_id: str
    output_dir: Path
    summaries: list[SpaceSummaryRow]
    accepted_trades: list[AcceptedSimulatedTradeRow]
    blocked: list[BlockedOpportunityRow]
    bundle_diagnostics: list[BundleDiagnosticRow]
    bundle_trades: list[BundleAcceptedTradeRow]
    diagnostic_only: list[DiagnosticOnlyRelationshipRow]
    report_integrity: str   # "ok" | "failed"
    credibility: str
    integrity_notes: list[str]


# ─── Stage 1: store universe ──────────────────────────────────────────────────


@dataclass
class _SpaceBaseStats:
    """Per-space counts built from the Parquet relationship/coverage store."""
    space_id: str
    attribution_status: SpaceAttributionStatus = "outcome_space"
    total_rel_count: int = 0
    strategy_eligible_rel_count: int = 0
    strict_lane_rel_count: int = 0
    exploratory_lane_rel_count: int = 0
    diagnostic_only_rel_count: int = 0
    needs_review_rel_count: int = 0
    market_ids: set = field(default_factory=set)
    markets_with_price_history: set = field(default_factory=set)
    strategy_families: Counter = field(default_factory=Counter)
    relationship_subtypes: Counter = field(default_factory=Counter)
    lane_counts: Counter = field(default_factory=Counter)


def _load_space_universe_from_store(data_root: Path) -> dict[str, _SpaceBaseStats]:
    """Read ALL relationships + decisions + coverage from the Parquet store.

    Returns a dict keyed by the resolved space_id using the precedence rule:
      outcome_space_id > context_space_id > synthetic
    """
    universe: dict[str, _SpaceBaseStats] = {}

    def _get(space: str, status: SpaceAttributionStatus) -> _SpaceBaseStats:
        if space not in universe:
            universe[space] = _SpaceBaseStats(space_id=space, attribution_status=status)
        return universe[space]

    # ── load context decisions → rel_id → (context_space_id, strategy_lane) ─
    try:
        from ..storage.parquet.context_relationship_decisions_repo import (
            ParquetContextRelationshipDecisionsRepository,
        )
        dec_repo = ParquetContextRelationshipDecisionsRepository(data_root)
        rel_to_decision: dict[str, tuple[str, str, str]] = {}  # rel_id → (ctx_space, lane, eligibility)
        for d in dec_repo.iter_latest():
            rel_to_decision[d.relationship_id] = (
                d.context_space_id or "",
                d.strategy_lane or "",
                d.new_strategy_eligibility or "unknown",
            )
    except Exception:
        rel_to_decision = {}

    # ── load coverage → market_id → has_price_history ─────────────────────────
    try:
        from ..storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
        cov_repo = ParquetBackfillCoverageRepository(data_root)
        market_has_coverage: dict[str, bool] = {
            c.market_id: bool(c.has_price_history)
            for c in cov_repo.iter_latest()
        }
    except Exception:
        market_has_coverage = {}

    # ── load all relationship candidates ─────────────────────────────────────
    try:
        from ..storage.parquet.relationship_candidates_repo import (
            ParquetRelationshipCandidatesRepository,
        )
        rel_repo = ParquetRelationshipCandidatesRepository(data_root)
        for rel in rel_repo.iter_latest():
            rid = rel.relationship_id
            outcome_sp = (rel.outcome_space_id or "").strip()
            ctx_sp, lane, eligibility = rel_to_decision.get(rid, ("", "", "unknown"))

            # Resolve space_id via precedence rule
            sid, status = space_id_for(
                outcome_space_id=outcome_sp or None,
                context_space_id=ctx_sp or None,
                fallback=None,
            )
            if not sid or sid == "unattributed":
                # Truly unattributed — group all together but mark as synthetic
                sid = "unattributed_no_space"
                status = "synthetic_or_missing"

            s = _get(sid, status)
            s.total_rel_count += 1
            s.market_ids.add(rel.market_id_a)
            s.market_ids.add(rel.market_id_b)

            subtype = (rel.relationship_subtype or "").strip()
            if subtype:
                s.relationship_subtypes[subtype] += 1

            sf = (getattr(rel, "strategy_family", "") or "").strip()
            if sf:
                s.strategy_families[sf] += 1

            # Classify the relationship
            if is_diagnostic_only_subtype(subtype):
                s.diagnostic_only_rel_count += 1
            elif eligibility == "eligible" or rel.strategy_eligibility_status == "eligible":
                s.strategy_eligible_rel_count += 1
            elif rel.validation_status == "needs_manual_review":
                s.needs_review_rel_count += 1

            if lane:
                s.lane_counts[lane] += 1
                if lane in _STRICT_LANES:
                    s.strict_lane_rel_count += 1
                elif lane in _EXPLORATORY_LANES:
                    s.exploratory_lane_rel_count += 1

        # Annotate coverage
        for _sid, s in universe.items():
            for mid in s.market_ids:
                if market_has_coverage.get(mid, False):
                    s.markets_with_price_history.add(mid)

    except Exception:
        pass  # Store may not exist in test environments

    return universe


def _load_relationship_lookup(data_root: Path) -> dict[str, Any]:
    try:
        from ..storage.parquet.relationship_candidates_repo import (
            ParquetRelationshipCandidatesRepository,
        )

        return {
            rel.relationship_id: rel
            for rel in ParquetRelationshipCandidatesRepository(data_root).iter_latest()
        }
    except Exception:
        return {}


# ─── CSV/JSON loaders ────────────────────────────────────────────────────────


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_subtype(row: dict[str, Any]) -> str:
    return (row.get("relationship_subtype") or row.get("relationship_family") or "").strip()


def _merge_relationship_metadata(
    row: dict[str, Any],
    relationship_lookup: dict[str, Any] | None,
) -> dict[str, Any]:
    """Fill missing replay CSV metadata from the relationship store.

    Older research replays wrote only `relationship_id` plus context IDs.  That
    made later accounting depend on fragile context-name inference and caused
    valid fills to be excluded from trade-cost/PnL totals.  New replays write
    these fields directly; this enrichment keeps legacy rows auditable too.
    """
    if not relationship_lookup:
        return dict(row)
    rel_id = str(row.get("relationship_id") or "")
    rel = relationship_lookup.get(rel_id)
    if rel is None:
        return dict(row)
    out = dict(row)
    for key in (
        "relationship_type",
        "relationship_subtype",
        "relationship_family",
        "strategy_family",
        "outcome_space_id",
        "evidence_json",
        "rulebook_id",
    ):
        if not out.get(key):
            out[key] = getattr(rel, key, "") or ""
    return out


def _strategy_family_for(
    subtype: str,
    family: str,
    relationship_type: str,
    context_space_id: str = "",
) -> str:
    if family and family.strip().lower() not in ("none", "unknown", ""):
        return family.strip()
    st = (subtype or "").lower()
    ctx = (context_space_id or "").lower()
    if (
        st.startswith("llm_hypothesis_")
        or st.startswith("embedding_hypothesis_")
        or st.startswith("deepseek_hypothesis_")
        or ctx.startswith("llm_hyp_")
        or ctx.startswith("embedding_hyp_")
        or ctx.startswith("deepseek_hyp_")
    ):
        return "hypothesis_research"
    rt = (relationship_type or "").lower()
    if "implies" in rt or "nested" in rt:
        return "nesting"
    if "mutual" in rt or "exclusive" in rt or "exclusion" in rt:
        return "mutual_exclusion"
    if "contradiction" in rt or "inverse" in rt:
        return "contradiction"
    if "temporal" in rt or "before" in rt:
        return "temporal"
    # Try from subtype directly (e.g. "championship_implies_conference" → nesting)
    if any(k in st for k in ("implies", "nesting", "championship", "finish_implies",
                              "top_n", "earlier_implies", "threshold")):
        return "nesting"
    if any(k in st for k in ("exclusive", "mutual", "contradiction")):
        return "mutual_exclusion"
    if "temporal" in st or "before" in st or "date_" in st:
        return "temporal"
    # Fallback via context_space_id
    if any(k in ctx for k in ("championship", "progression", "nesting", "ranking",
                               "threshold", "date_time", "exact_finish", "top_n",
                               "finals", "conference")):
        return "nesting"
    if any(k in ctx for k in ("mutual", "exclusion", "electoral", "bundle",
                               "title", "winner")):
        return "mutual_exclusion"
    if any(k in ctx for k in ("temporal", "before", "clock")):
        return "temporal"
    return ""


def _space_from_row(row: dict[str, Any]) -> tuple[str, SpaceAttributionStatus]:
    return space_id_for(
        outcome_space_id=row.get("outcome_space_id") or None,
        context_space_id=row.get("context_space_id") or None,
        fallback=row.get("relationship_id") or row.get("trade_id") or None,
    )


# ─── Stage 2: backtest-run typed row filters ────────────────────────────────


def _filter_typed_trades(rows: list[dict[str, Any]]) -> tuple[
    list[AcceptedSimulatedTradeRow], list[str]
]:
    return _filter_typed_trades_with_relationships(rows, relationship_lookup=None)


def _filter_typed_trades_with_relationships(
    rows: list[dict[str, Any]],
    relationship_lookup: dict[str, Any] | None,
) -> tuple[list[AcceptedSimulatedTradeRow], list[str]]:
    kept: list[AcceptedSimulatedTradeRow] = []
    dropped: list[str] = []
    for raw_row in rows:
        row = _merge_relationship_metadata(raw_row, relationship_lookup)
        subtype = _safe_subtype(row)
        if subtype and is_diagnostic_only_subtype(subtype):
            dropped.append(
                f"trade_id={row.get('trade_id')} dropped: diagnostic-only subtype {subtype!r}"
            )
            continue
        family = _strategy_family_for(
            subtype,
            row.get("relationship_family", "") or row.get("strategy_family", ""),
            row.get("relationship_type", ""),
            row.get("context_space_id", ""),
        )
        if not family:
            dropped.append(f"trade_id={row.get('trade_id')} dropped: no strategy_family")
            continue
        rel_id = row.get("relationship_id") or ""
        if not rel_id:
            dropped.append(f"trade_id={row.get('trade_id')} dropped: no relationship_id")
            continue
        space, status = _space_from_row(row)
        try:
            kept.append(AcceptedSimulatedTradeRow(
                trade_id=str(row.get("trade_id", "")),
                candidate_id=str(row.get("candidate_id", "") or ""),
                trade_group_id=str(
                    row.get("trade_group_id")
                    or row.get("position_id")
                    or row.get("candidate_id")
                    or row.get("trade_id")
                    or ""
                ),
                relationship_id=rel_id,
                relationship_type=row.get("relationship_type", "") or "",
                relationship_subtype=subtype,
                relationship_family=row.get("relationship_family", "") or "",
                space_id=space,
                space_attribution_status=status,
                strategy_family=family,
                template_id=row.get("template_id", "") or "",
                leg=row.get("leg", "") or "",
                token_id=row.get("token_id", "") or "",
                fill_ts_ms=_int(row.get("fill_ts_ms")),
                fill_price=_float(row.get("fill_price")),
                shares=_float(row.get("shares")),
                notional_usdc=_float(row.get("notional_usdc")),
                fees_usdc=_float(row.get("fees_usdc")),
                slippage_cost_usdc=_float(row.get("slippage_cost_usdc")),
                mark_to_market_value_usdc=(
                    _float(row.get("mark_to_market_value_usdc"))
                    if row.get("mark_to_market_value_usdc") not in (None, "")
                    else None
                ),
                realised_pnl_usdc=(
                    _float(row.get("realised_pnl_usdc"))
                    if row.get("realised_pnl_usdc") not in (None, "")
                    else None
                ),
                gross_edge=_float(row.get("gross_edge")),
                net_edge_after_cost=_float(row.get("net_edge_after_cost")),
                first_entry_or_reentry=row.get("first_entry_or_reentry", "first") or "first",
                violation_window_id=row.get("violation_window_id", "") or "",
                preset_name=row.get("preset_name", "") or "",
                label=row.get("label", "") or "",
            ))
        except Exception as e:
            dropped.append(f"trade_id={row.get('trade_id')} dropped: {e!r}")
    return kept, dropped


def _filter_blocked(rows: list[dict[str, Any]]) -> list[BlockedOpportunityRow]:
    kept: list[BlockedOpportunityRow] = []
    for row in rows:
        rel_id = row.get("relationship_id") or ""
        if not rel_id:
            continue
        space, _status = _space_from_row(row)
        reason = row.get("rejection_reason") or row.get("blocker_reason") or "unknown"
        try:
            kept.append(BlockedOpportunityRow(
                relationship_id=rel_id,
                space_id=space,
                strategy_family=_strategy_family_for(
                    _safe_subtype(row),
                    row.get("relationship_family", "") or row.get("strategy_family", ""),
                    row.get("relationship_type", ""),
                    row.get("context_space_id", ""),
                ),
                blocker_reason=str(reason),
                blocker_category=_blocker_category(str(reason)),
                signal_ts_ms=_int(row.get("signal_ts_ms"), 0) or None,
            ))
        except Exception:
            continue
    return kept


def _blocker_category(reason: str) -> str:
    r = (reason or "").lower()
    if "coverage" in r or "price_history" in r or "missing_yes_tokens" in r or "alignment" in r:
        return "infrastructure"
    if "cash" in r or "cost" in r or "exposure" in r or "stake" in r or "max_trades" in r:
        return "economic_or_replay"
    if "context" in r or "eligibility" in r or "lane" in r:
        return "context_lane"
    if "confidence" in r:
        return "confidence_gate"
    if "cooldown" in r or "window" in r or "already_traded" in r or "already_open" in r:
        return "replay_policy"
    if "completeness" in r or "incomplete" in r:
        return "completeness"
    if "viability" in r:
        return "viability"
    return "other"


def _filter_diagnostic_only(rows: list[dict[str, Any]]) -> list[DiagnosticOnlyRelationshipRow]:
    kept: list[DiagnosticOnlyRelationshipRow] = []
    for row in rows:
        subtype = _safe_subtype(row)
        if not subtype or not is_diagnostic_only_subtype(subtype):
            continue
        rel_id = row.get("relationship_id") or ""
        if not rel_id:
            continue
        space, _ = _space_from_row(row)
        try:
            kept.append(DiagnosticOnlyRelationshipRow(
                relationship_id=rel_id,
                relationship_subtype=subtype,
                reason=row.get("rationale_summary", "") or "",
                space_id=space,
            ))
        except Exception:
            continue
    return kept


# ─── Per-run backtest readers ────────────────────────────────────────────────


def _load_pairwise_run(run_dir: Path, include_exploratory: bool) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"signals": [], "trades": [], "rejected": [], "funnel": []}

    def _absorb(dir_path: Path, is_exploratory: bool) -> None:
        if not dir_path.exists():
            return
        if is_exploratory and not include_exploratory:
            return
        out["signals"].extend(_read_csv(dir_path / "signals.csv"))
        out["trades"].extend(_read_csv(dir_path / "trades.csv"))
        out["rejected"].extend(_read_csv(dir_path / "rejected_candidates.csv"))
        for fn in ("funnel_audit.json", "funnel.json"):
            f = _read_json(dir_path / fn)
            if f:
                out["funnel"].append(f)

    context_dir = run_dir / "context_aware"
    research_dir = run_dir / "research"
    diag_dir = run_dir / "diagnostic"

    if context_dir.exists():
        for lane_dir in sorted(context_dir.iterdir()):
            if not lane_dir.is_dir():
                continue
            is_expl = lane_dir.name in (
                "exploratory_context_unreviewed",
                "exploratory_context_auto_approved",
            )
            _absorb(lane_dir, is_expl)

    if research_dir.exists():
        for preset_dir in sorted(research_dir.iterdir()):
            if preset_dir.is_dir():
                _absorb(preset_dir, is_exploratory=True)

    _absorb(diag_dir, is_exploratory=True)
    return out


def _load_bundle_run(run_dir: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"opportunities": [], "trades": [], "scan": []}
    bundle_dir = run_dir / "template_bundle"
    if not bundle_dir.exists():
        return out
    out["opportunities"].extend(_read_csv(bundle_dir / "opportunities.csv"))
    out["trades"].extend(_read_csv(bundle_dir / "trades.csv"))
    out["scan"].extend(_read_csv(bundle_dir / "bundle_scan.csv"))
    return out


# ─── Stage 2 + 3: merge and build SpaceSummaryRow per space ─────────────────


def _build_summaries(
    store_universe: dict[str, _SpaceBaseStats],
    *,
    trades: list[AcceptedSimulatedTradeRow],
    blocked: list[BlockedOpportunityRow],
    signals: list[dict[str, Any]],
    bundle_diagnostics: list[BundleDiagnosticRow],
    bundle_trades: list[BundleAcceptedTradeRow],
    diagnostic_only_from_run: list[DiagnosticOnlyRelationshipRow],
) -> list[SpaceSummaryRow]:
    """Merge all data sources into one SpaceSummaryRow per space.

    The store_universe seeds all spaces.  Backtest data is overlaid on top.
    Spaces that exist only in the backtest (not in the store) are included too.
    """
    summaries: dict[str, SpaceSummaryRow] = {}

    def _get(space: str, status: SpaceAttributionStatus = "synthetic_or_missing") -> SpaceSummaryRow:
        if space not in summaries:
            summaries[space] = SpaceSummaryRow(
                space_id=space,
                space_name=space,
                space_attribution_status=status,
            )
        return summaries[space]

    # Seed from store universe
    for sid, base in store_universe.items():
        s = _get(sid, base.attribution_status)
        s.relationship_count = base.total_rel_count
        s.strict_relationship_count = base.strict_lane_rel_count
        s.exploratory_relationship_count = base.exploratory_lane_rel_count
        s.diagnostic_only_relationship_count = base.diagnostic_only_rel_count
        # Coverage
        n_markets = len(base.market_ids)
        n_covered = len(base.markets_with_price_history)
        s.price_history_coverage_pct = (n_covered / n_markets) if n_markets else 0.0
        # Strategy families
        s.strategy_families_available = sorted(
            {f for f in base.strategy_families if f}
        )

    # Diagnostic-only from run (add on top of store counts)
    diag_by_space: Counter = Counter(d.space_id for d in diagnostic_only_from_run)
    for space, count in diag_by_space.items():
        s = _get(space)
        # Don't double-count if already seeded from store
        if s.diagnostic_only_relationship_count == 0:
            s.diagnostic_only_relationship_count = count

    # Signals (gross opportunities) — exclude diagnostic-only subtypes
    gross_by_space: dict[str, int] = defaultdict(int)
    net_by_space: dict[str, int] = defaultdict(int)
    gross_opp_by_space: dict[str, int] = defaultdict(int)
    net_opp_by_space: dict[str, int] = defaultdict(int)
    best_edge_by_space: dict[str, float] = defaultdict(float)

    for sig in signals:
        subtype = _safe_subtype(sig)
        if subtype and is_diagnostic_only_subtype(subtype):
            continue
        space, status = _space_from_row(sig)
        _get(space, status)
        edge = _float(sig.get("gross_edge"))
        gross_by_space[space] += 1
        gross_opp_by_space[space] += 1
        best_edge_by_space[space] = max(best_edge_by_space[space], edge)
        if str(sig.get("accepted_for_simulation", "")).lower() == "true":
            net_by_space[space] += 1
            net_opp_by_space[space] += 1

    for space, count in gross_by_space.items():
        s = _get(space)
        s.gross_violation_count = count
        s.gross_opportunity_rows = gross_opp_by_space[space]
        s.net_violation_count = net_by_space.get(space, 0)
        s.net_opportunity_rows = net_opp_by_space.get(space, 0)
        s.best_edge = best_edge_by_space[space]

    # Accepted trades
    trades_by_space: dict[str, list[AcceptedSimulatedTradeRow]] = defaultdict(list)
    for t in trades:
        _get(t.space_id, t.space_attribution_status)
        trades_by_space[t.space_id].append(t)

    for space, rows in trades_by_space.items():
        s = _get(space)
        s.raw_replay_fills = len(rows)
        s.raw_fill_count = len(rows)
        s.accepted_trade_count = len(rows) // 2
        s.distinct_relationships_traded = len({r.relationship_id for r in rows})
        s.unique_violation_window_count = len({r.violation_window_id for r in rows if r.violation_window_id})
        s.independent_violation_windows = s.unique_violation_window_count
        pnl = sum((r.net_edge_after_cost or 0.0) * (r.notional_usdc or 0.0) for r in rows)
        s.simulated_pnl = pnl
        s.total_slippage = sum(r.slippage_cost_usdc for r in rows)
        trades_by_id: dict[str, list[AcceptedSimulatedTradeRow]] = defaultdict(list)
        for r in rows:
            group_id = r.trade_group_id or r.candidate_id or r.trade_id
            trades_by_id[group_id].append(r)
        trade_costs: list[float] = []
        trade_pnls: list[float] = []
        trade_returns: list[float] = []
        for trade_rows in trades_by_id.values():
            trade_cost = sum(float(r.notional_usdc or 0.0) for r in trade_rows)
            trade_pnl = sum(
                float(r.net_edge_after_cost or 0.0) * float(r.notional_usdc or 0.0)
                for r in trade_rows
            )
            trade_costs.append(trade_cost)
            trade_pnls.append(trade_pnl)
            if trade_cost > 0:
                trade_returns.append(trade_pnl / trade_cost)
        s.total_trade_cost = sum(trade_costs)
        s.total_return_pct = pnl / max(s.total_trade_cost, 1e-9)
        s.average_trade_return_pct = statistics.fmean(trade_returns) if trade_returns else 0.0
        s.median_trade_return_pct = statistics.median(trade_returns) if trade_returns else 0.0
        s.worst_trade_pnl = min(trade_pnls) if trade_pnls else 0.0
        s.best_trade_pnl = max(trade_pnls) if trade_pnls else 0.0
        edge_values = [float(r.net_edge_after_cost or 0.0) for r in rows]
        s.avg_edge = statistics.fmean(edge_values) if edge_values else 0.0
        s.median_edge = statistics.median(edge_values) if edge_values else 0.0
        if s.strategy_families_available == [] and rows:
            s.strategy_families_available = sorted({r.strategy_family for r in rows if r.strategy_family})
        # Dominance
        if pnl != 0:
            rel_pnls = defaultdict(float)
            for r in rows:
                rel_pnls[r.relationship_id] += (r.net_edge_after_cost or 0.0) * (r.notional_usdc or 0.0)
            s.dominant_relationship_share_of_pnl = (
                max(abs(v) for v in rel_pnls.values()) / abs(pnl)
                if rel_pnls and pnl
                else 0.0
            )

    # Integrity check: accepted trades without gross violations
    for space, _rows in trades_by_space.items():
        s = summaries.get(space)
        if s and s.accepted_trade_count > 0 and s.gross_violation_count == 0:
            s.integrity_warning = (
                "accepted_trades_without_matching_gross_violations — "
                "likely replay vs signal metric mismatch or signals.csv missing"
            )

    # Blocked opportunities
    blocker_counts_by_space: dict[str, Counter] = defaultdict(Counter)
    for b in blocked:
        _get(b.space_id)
        blocker_counts_by_space[b.space_id][b.blocker_category] += 1
    for space, counts in blocker_counts_by_space.items():
        s = _get(space)
        top = counts.most_common(2)
        if top:
            s.primary_blocker = top[0][0]
        if len(top) > 1:
            s.secondary_blocker = top[1][0]

    # Bundle diagnostics
    for b in bundle_diagnostics:
        s = _get(b.space_id, "bundle_space")
        s.completeness_status = b.completeness_status
        s.known_total_candidates = b.known_total_candidates
        if b.gross_edge > 0:
            s.gross_violation_count += 1
            s.gross_opportunity_rows += 1
        if b.net_edge_after_costs > 0:
            s.net_violation_count += 1
            s.net_opportunity_rows += 1

    # Bundle trades
    bundle_by_space: dict[str, list[BundleAcceptedTradeRow]] = defaultdict(list)
    for bt in bundle_trades:
        _get(bt.space_id, "bundle_space")
        bundle_by_space[bt.space_id].append(bt)
    for space, bts in bundle_by_space.items():
        s = _get(space)
        s.distinct_bundles_traded = len({bt.fill_ts_ms for bt in bts})
        s.raw_fill_count += len(bts)
        s.raw_replay_fills += len(bts)
        if s.unique_violation_window_count and not s.independent_violation_windows:
            s.independent_violation_windows = s.unique_violation_window_count

    # Remove truly unattributed spaces with nothing useful (keep if any rel count or violations)
    summaries = {
        sid: s for sid, s in summaries.items()
        if (
            s.relationship_count > 0
            or s.gross_violation_count > 0
            or s.accepted_trade_count > 0
            or s.distinct_bundles_traded > 0
            or s.diagnostic_only_relationship_count > 0
        )
    }

    return list(summaries.values())


# ─── Grading ─────────────────────────────────────────────────────────────────


def _grade_space(s: SpaceSummaryRow) -> tuple[SpaceGrade, str]:
    """Classify a SpaceSummaryRow into grade A-G with recommended action."""

    # ── E: diagnostic-only with NO valid trades ───────────────────────────────
    if (
        s.diagnostic_only_relationship_count > 0
        and s.strict_relationship_count == 0
        and s.accepted_trade_count == 0
        and s.distinct_bundles_traded == 0
        # Allow through if there are gross violations (may be worth investigation)
        and s.gross_violation_count == 0
    ):
        return (
            "E_INVALID_OR_AUDIT_RISK",
            "Repair taxonomy/templates — space is dominated by diagnostic-only relationships.",
        )
    if s.suspicious_flag_count > 0 and s.suspicious_flag_count >= max(1, s.relationship_count // 2):
        return (
            "E_INVALID_OR_AUDIT_RISK",
            "Repair guards/templates — majority of relationships are suspicious.",
        )

    # ── F: overfit / single-relationship ────────────────────────────────────
    if (
        s.simulated_pnl > 0
        and s.accepted_trade_count >= 1
        and s.dominant_relationship_share_of_pnl >= 0.9
        and s.distinct_relationships_traded <= 2
    ):
        return (
            "F_OVERFIT_ONE_OFF",
            "Do not optimise — single-relationship PnL is statistically unreliable.",
        )

    multi_relationship_or_bundle = (
        s.distinct_relationships_traded >= 3
        or s.distinct_bundles_traded >= 2
    )
    non_dominated = s.dominant_relationship_share_of_pnl < 0.8
    economic_gates_pass = (
        s.median_trade_return_pct >= 0.01
        and s.total_return_pct >= 0.005
        and s.independent_violation_windows >= 5
    )

    # ── A: profitable + economically meaningful + independent evidence ───────
    if (
        s.simulated_pnl > 0
        and economic_gates_pass
        and multi_relationship_or_bundle
        and non_dominated
    ):
        return (
            "A_PROFITABLE_ROBUST_CANDIDATE",
            "Optimise parameters per space and run robustness grid; per-trade returns clear the economic gates.",
        )

    # ── G: structurally clean, positive, but too tiny / too sparse ───────────
    if (
        s.simulated_pnl > 0
        and multi_relationship_or_bundle
        and non_dominated
        and not economic_gates_pass
    ):
        return (
            "G_ECONOMICALLY_TINY_SIGNAL",
            "Bundle with other promising spaces or skip — per-trade return too weak to pursue alone.",
        )

    # ── B: violations exist but blocked before execution ────────────────────
    if s.gross_violation_count > 0 and s.accepted_trade_count == 0:
        if s.primary_blocker in ("economic_or_replay", "replay_policy", "confidence_gate"):
            return (
                "B_PROMISING_GATE_BLOCKED",
                "Run looser economic/replay preset (exploratory_trade_surface) on this space.",
            )
        # Gross violations but blocked by unknown/other — still promising
        if s.primary_blocker in ("", None) or s.primary_blocker == "other":
            return (
                "B_PROMISING_GATE_BLOCKED",
                "Gross violations exist but no blocker identified — "
                "check alignment and run exploratory preset.",
            )

    # ── C: relationships exist + no price coverage ──────────────────────────
    if s.relationship_count > 0 and s.price_history_coverage_pct < 0.1:
        return (
            "C_INFRASTRUCTURE_BLOCKED",
            "Backfill price history for markets in this space, "
            "then run alignment and a backtest.",
        )
    if s.primary_blocker in ("infrastructure", "completeness"):
        return (
            "C_INFRASTRUCTURE_BLOCKED",
            "Fix data/metadata — backfill prices, run alignment, or set known_total_candidates.",
        )

    # ── D: clean relationships + price coverage + no violations ─────────────
    if (
        s.relationship_count > 0
        and s.price_history_coverage_pct >= 0.1
        and s.gross_violation_count == 0
        and s.accepted_trade_count == 0
    ):
        return (
            "D_VALID_BUT_STRATEGICALLY_WEAK",
            "Low priority — relationships exist and have price coverage but no signal observed.",
        )

    return ("UNGRADED", "Insufficient evidence to grade — run a backtest on this space.")


def grade_summaries(summaries: list[SpaceSummaryRow]) -> None:
    """In-place grade + recommended_action assignment for every summary row."""
    for s in summaries:
        grade, action = _grade_space(s)
        s.space_grade = grade
        s.recommended_next_action = action


# ─── Main entry point ─────────────────────────────────────────────────────────


def run_space_sweep(
    data_root: Path,
    run_id: str,
    *,
    include_pairwise: bool = True,
    include_bundles: bool = True,
    include_exploratory: bool = True,
    output_dir: Path | None = None,
) -> SpaceSweepResult:
    """Build the full space universe and overlay one backtest run's data.

    The universe starts from ALL relationships in the Parquet store so that
    spaces with zero accepted trades (but real relationships) receive a row
    and a grade.
    """
    out_dir = output_dir or (data_root.parent / "reports" / "space_sweep" / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_dir = data_root / "backtests" / run_id
    integrity_notes: list[str] = []

    # Stage 1: full store universe (runs even if run_dir doesn't exist)
    store_universe = _load_space_universe_from_store(data_root)
    relationship_lookup = _load_relationship_lookup(data_root)

    if not run_dir.exists():
        integrity_notes.append(f"backtest run dir not found: {run_dir} — showing store-only universe")

    # Stage 2: backtest run data
    trades: list[AcceptedSimulatedTradeRow] = []
    blocked: list[BlockedOpportunityRow] = []
    bundle_diags: list[BundleDiagnosticRow] = []
    bundle_trades: list[BundleAcceptedTradeRow] = []
    diagnostic_only: list[DiagnosticOnlyRelationshipRow] = []
    signals_for_agg: list[dict[str, Any]] = []

    if run_dir.exists() and include_pairwise:
        pw = _load_pairwise_run(run_dir, include_exploratory=include_exploratory)
        signals_for_agg = pw["signals"]
        typed_trades, drops = _filter_typed_trades_with_relationships(
            pw["trades"],
            relationship_lookup=relationship_lookup,
        )
        trades.extend(typed_trades)
        integrity_notes.extend(drops[:10])
        blocked.extend(_filter_blocked(pw["rejected"]))
        diagnostic_only.extend(_filter_diagnostic_only(pw["signals"] + pw["rejected"]))

    if run_dir.exists() and include_bundles:
        bd = _load_bundle_run(run_dir)
        for row in bd["opportunities"]:
            space = (row.get("outcome_space_id") or "").strip() or "unattributed"
            try:
                bundle_diags.append(BundleDiagnosticRow(
                    space_id=space,
                    bundle_basket=row.get("best_executable_basket", "") or "",
                    observed_candidates=_int(row.get("candidate_count_observed", row.get("candidate_count"))),
                    known_total_candidates=(
                        _int(row.get("known_total_candidates"))
                        if row.get("known_total_candidates") else None
                    ),
                    completeness_status=row.get("completeness_status", "") or "unknown",
                    sum_yes_prices=_float(row.get("sum_yes_prices")),
                    gross_edge=_float(row.get("gross_edge")),
                    net_edge_after_costs=_float(row.get("net_edge_after_costs")),
                    rejection_reason=row.get("rejection_reason", "") or "",
                    signal_ts_ms=_int(row.get("signal_ts_ms"), 0) or None,
                ))
            except Exception as e:
                integrity_notes.append(f"bundle opp row dropped: {e}")
        for row in bd["trades"]:
            space = (row.get("outcome_space_id") or "").strip() or "unattributed"
            try:
                bundle_trades.append(BundleAcceptedTradeRow(
                    trade_id=row.get("trade_id", "") or "",
                    space_id=space,
                    bundle_basket=row.get("basket", "") or "",
                    candidate=row.get("candidate", "") or "",
                    market_id=row.get("market_id", "") or "",
                    token_id=row.get("token_id", "") or "",
                    fill_ts_ms=_int(row.get("fill_ts_ms")),
                    fill_price=_float(row.get("fill_price")),
                    shares=_float(row.get("shares")),
                    notional_usdc=_float(row.get("notional_usdc")),
                    fees_usdc=_float(row.get("fees_usdc")),
                ))
            except Exception as e:
                integrity_notes.append(f"bundle trade row dropped: {e}")

    # Build and grade summaries from the merged universe
    summaries = _build_summaries(
        store_universe,
        trades=trades,
        blocked=blocked,
        signals=signals_for_agg,
        bundle_diagnostics=bundle_diags,
        bundle_trades=bundle_trades,
        diagnostic_only_from_run=diagnostic_only,
    )
    grade_summaries(summaries)

    failed = any("report_integrity=failed" in n for n in integrity_notes)
    return SpaceSweepResult(
        run_id=run_id,
        output_dir=out_dir,
        summaries=summaries,
        accepted_trades=trades,
        blocked=blocked,
        bundle_diagnostics=bundle_diags,
        bundle_trades=bundle_trades,
        diagnostic_only=diagnostic_only,
        report_integrity="failed" if failed else "ok",
        credibility="report_invalid_for_strategy_conclusions" if failed else "exploratory_only_not_credible",
        integrity_notes=integrity_notes,
    )
