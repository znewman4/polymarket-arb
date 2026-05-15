"""Market-data coverage audit for all discovered semantic relationships.

RESEARCH-ONLY / DIAGNOSTIC-ONLY. Not trading advice.

For every relationship in the store, this module checks both markets and
reports exactly where coverage breaks down: missing Gamma metadata, missing
CLOB token IDs, missing price history, poor coverage score, or absent
context decisions. The output explains why relationships fail the backtest
funnel before a single tick is evaluated.

Performance: all data is fetched in bulk (4-5 DuckDB queries total regardless
of the number of relationships), so this scales to thousands of relationships.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from ..storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from ..storage.parquet.markets_repo import ParquetMarketsRepository
from ..storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from ..storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)


@dataclass
class RelationshipCoverageRow:
    """Coverage report for one semantic relationship.

    RESEARCH-ONLY / DIAGNOSTIC-ONLY. Not trading advice.
    """

    relationship_id: str
    market_id_a: str
    market_id_b: str
    question_a: str
    question_b: str
    relationship_type: str
    relationship_subtype: str
    validation_status: str
    strategy_eligibility_status: str
    final_confidence: float

    # Context decision
    has_context_decision: bool
    context_space_id: str | None
    strategy_lane: str | None
    decision_eligibility: str | None

    # Market A
    gamma_exists_a: bool
    clob_token_count_a: int
    token_id_a_yes: str | None
    price_history_exists_a: bool
    tick_count_a: int
    first_ts_ms_a: int | None
    last_ts_ms_a: int | None
    coverage_score_a: float | None
    coverage_recommended_a: bool

    # Market B
    gamma_exists_b: bool
    clob_token_count_b: int
    token_id_b_yes: str | None
    price_history_exists_b: bool
    tick_count_b: int
    first_ts_ms_b: int | None
    last_ts_ms_b: int | None
    coverage_score_b: float | None
    coverage_recommended_b: bool

    # Pair summary
    coverage_score_pair: float
    both_have_price_history: bool
    final_blocker: str


def run_coverage_audit(data_root: Path) -> list[RelationshipCoverageRow]:
    """Audit every discovered relationship for data coverage.

    Uses bulk queries - 4-5 DuckDB scans total regardless of relationship count.

    RESEARCH-ONLY / DIAGNOSTIC-ONLY.
    """
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    market_repo = ParquetMarketsRepository(data_root)
    price_repo = ParquetPriceHistoryRepository(data_root)
    coverage_repo = ParquetBackfillCoverageRepository(data_root)
    decision_repo = ParquetContextRelationshipDecisionsRepository(data_root)

    # ── bulk load all reference data ─────────────────────────────────────────
    all_rels = list(rel_repo.iter_latest())
    if not all_rels:
        return []

    decisions_by_rel = {d.relationship_id: d for d in decision_repo.iter_latest()}
    coverage_by_market = {c.market_id: c for c in coverage_repo.iter_latest()}

    # Collect all market IDs and token IDs needed
    all_market_ids: list[str] = []
    for r in all_rels:
        all_market_ids.append(r.market_id_a)
        all_market_ids.append(r.market_id_b)

    markets_map = market_repo.get_many_markets(all_market_ids)

    # Resolve YES token IDs — relationship field first, fall back to market CLOB list
    def _yes_token(rel_tok: str | None, market_id: str) -> str | None:
        if rel_tok:
            return rel_tok
        m = markets_map.get(market_id)
        if m and m.clob_token_ids:
            return m.clob_token_ids[0]
        return None

    all_token_ids: list[str] = []
    for r in all_rels:
        tok_a = _yes_token(r.token_id_a_yes, r.market_id_a)
        tok_b = _yes_token(r.token_id_b_yes, r.market_id_b)
        if tok_a:
            all_token_ids.append(tok_a)
        if tok_b:
            all_token_ids.append(tok_b)

    # Single bulk stats query — {token_id: {ticks, first_ts_ms, last_ts_ms}}
    token_stats = price_repo.stats_for_tokens(all_token_ids)

    # ── per-relationship audit ────────────────────────────────────────────────
    rows: list[RelationshipCoverageRow] = []

    for rel in all_rels:
        market_a = markets_map.get(rel.market_id_a)
        market_b = markets_map.get(rel.market_id_b)

        gamma_a = market_a is not None
        gamma_b = market_b is not None
        clob_a = list(market_a.clob_token_ids) if market_a else []
        clob_b = list(market_b.clob_token_ids) if market_b else []

        tok_a = _yes_token(rel.token_id_a_yes, rel.market_id_a)
        tok_b = _yes_token(rel.token_id_b_yes, rel.market_id_b)

        stats_a = token_stats.get(tok_a) if tok_a else None
        stats_b = token_stats.get(tok_b) if tok_b else None

        ph_a = stats_a is not None
        ph_b = stats_b is not None
        tick_a = int(stats_a["ticks"]) if stats_a else 0
        tick_b = int(stats_b["ticks"]) if stats_b else 0
        first_a = int(stats_a["first_ts_ms"]) if stats_a else None
        last_a = int(stats_a["last_ts_ms"]) if stats_a else None
        first_b = int(stats_b["first_ts_ms"]) if stats_b else None
        last_b = int(stats_b["last_ts_ms"]) if stats_b else None

        cov_a = coverage_by_market.get(rel.market_id_a)
        cov_b = coverage_by_market.get(rel.market_id_b)
        score_a = float(cov_a.coverage_score) if cov_a else None
        score_b = float(cov_b.coverage_score) if cov_b else None
        rec_a = bool(cov_a and cov_a.recommended_for_backtest)
        rec_b = bool(cov_b and cov_b.recommended_for_backtest)
        score_pair = min(score_a or 0.0, score_b or 0.0)

        decision = decisions_by_rel.get(rel.relationship_id)

        blocker = _determine_blocker(
            gamma_a=gamma_a, gamma_b=gamma_b,
            clob_a=clob_a, clob_b=clob_b,
            ph_a=ph_a, ph_b=ph_b,
            cov_a=cov_a, cov_b=cov_b,
            rec_a=rec_a, rec_b=rec_b,
            score_a=score_a, score_b=score_b,
            tok_a=tok_a, tok_b=tok_b,
            decision=decision,
        )

        rows.append(RelationshipCoverageRow(
            relationship_id=rel.relationship_id,
            market_id_a=rel.market_id_a,
            market_id_b=rel.market_id_b,
            question_a=rel.question_a,
            question_b=rel.question_b,
            relationship_type=rel.relationship_type,
            relationship_subtype=rel.relationship_subtype or "",
            validation_status=rel.validation_status,
            strategy_eligibility_status=rel.strategy_eligibility_status,
            final_confidence=rel.final_confidence,
            has_context_decision=decision is not None,
            context_space_id=decision.context_space_id if decision else None,
            strategy_lane=decision.strategy_lane if decision else None,
            decision_eligibility=decision.new_strategy_eligibility if decision else None,
            gamma_exists_a=gamma_a,
            clob_token_count_a=len(clob_a),
            token_id_a_yes=tok_a,
            price_history_exists_a=ph_a,
            tick_count_a=tick_a,
            first_ts_ms_a=first_a,
            last_ts_ms_a=last_a,
            coverage_score_a=score_a,
            coverage_recommended_a=rec_a,
            gamma_exists_b=gamma_b,
            clob_token_count_b=len(clob_b),
            token_id_b_yes=tok_b,
            price_history_exists_b=ph_b,
            tick_count_b=tick_b,
            first_ts_ms_b=first_b,
            last_ts_ms_b=last_b,
            coverage_score_b=score_b,
            coverage_recommended_b=rec_b,
            coverage_score_pair=score_pair,
            both_have_price_history=ph_a and ph_b,
            final_blocker=blocker,
        ))

    return rows


def _determine_blocker(
    *,
    gamma_a: bool,
    gamma_b: bool,
    clob_a: list,
    clob_b: list,
    ph_a: bool,
    ph_b: bool,
    cov_a: object,
    cov_b: object,
    rec_a: bool,
    rec_b: bool,
    score_a: float | None,
    score_b: float | None,
    tok_a: str | None,
    tok_b: str | None,
    decision: object,
) -> str:
    if not gamma_a:
        return "no_gamma_metadata_a"
    if not gamma_b:
        return "no_gamma_metadata_b"
    if not clob_a:
        return "no_clob_token_ids_a"
    if not clob_b:
        return "no_clob_token_ids_b"
    if not tok_a:
        return "token_id_a_yes_missing"
    if not tok_b:
        return "token_id_b_yes_missing"
    if not ph_a:
        return f"no_price_history_a (token={tok_a[:20]}...)"
    if not ph_b:
        return f"no_price_history_b (token={tok_b[:20]}...)"
    if not cov_a:
        return "no_backfill_coverage_a"
    if not cov_b:
        return "no_backfill_coverage_b"
    if not rec_a:
        return f"coverage_not_recommended_a (score={score_a:.3f})"
    if not rec_b:
        return f"coverage_not_recommended_b (score={score_b:.3f})"
    if decision is None:
        return "no_context_decision"
    elig = getattr(decision, "new_strategy_eligibility", "unknown")
    if elig != "eligible":
        return f"not_strategy_eligible ({elig})"
    return "none"
