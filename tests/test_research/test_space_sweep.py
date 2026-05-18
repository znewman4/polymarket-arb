"""Tests for the space sweep aggregator + classifier.

Covers:
- Full space universe built from Parquet store + backtest data
- Grade B: gross violations exist but blocked before execution
- Grade C: relationships exist but no price history
- Grade D: relationships + price coverage but no violations
- Grade E: diagnostic-only dominated space
- Grade F: overfit single-relationship
- Grade A: profitable multi-relationship
- Bundle-only spaces appear in leaderboard
- Accepted trades without gross violations trigger integrity warning
- diagnostic-only subtypes never enter accepted-trade totals
- store-only spaces appear even with no backtest run data
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_arb.research.row_contracts import AcceptedSimulatedTradeRow, SpaceSummaryRow
from polymarket_arb.research.space_sweep import (
    _build_summaries,
    _filter_typed_trades,
    _grade_space,
    _SpaceBaseStats,
    run_space_sweep,
)
from polymarket_arb.storage.base import (
    BackfillCoverageRow,
    ContextRelationshipDecisionRow,
    RelationshipCandidateRow,
)
from polymarket_arb.storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from polymarket_arb.storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)

_TS = 1_700_000_000_000


# ─── helpers ──────────────────────────────────────────────────────────────────


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({k for r in rows for k in r})
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _rel(
    rid: str,
    outcome_space_id: str,
    validation_status: str = "accepted",
    relationship_subtype: str = "championship_implies_conference",
    strategy_eligibility_status: str = "eligible",
    market_id_a: str = "m_a",
    market_id_b: str = "m_b",
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=rid,
        market_id_a=market_id_a,
        market_id_b=market_id_b,
        condition_id_a=None,
        condition_id_b=None,
        token_id_a_yes=f"{market_id_a}_yes",
        token_id_a_no=f"{market_id_a}_no",
        token_id_b_yes=f"{market_id_b}_yes",
        token_id_b_no=f"{market_id_b}_no",
        question_a="Will X win?",
        question_b="Will X qualify?",
        relationship_type="nested_a_implies_b",
        entity_match_score=1.0,
        time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.9,
        model_confidence=1.0,
        final_confidence=0.9,
        validation_status=validation_status,
        rejection_reasons_json="[]",
        rationale_summary="test",
        evidence_json="{}",
        rulebook_id="v2",
        rulebook_version=2,
        rulebook_content_hash="hash",
        relationship_subtype=relationship_subtype,
        outcome_space_id=outcome_space_id,
        strategy_eligibility_status=strategy_eligibility_status,
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _cov(market_id: str, has_price_history: bool = True) -> BackfillCoverageRow:
    return BackfillCoverageRow(
        market_id=market_id,
        condition_id=f"c_{market_id}",
        question="fixture",
        start_ts_ms=_TS,
        end_ts_ms=_TS + 3_600_000,
        requested_days=1,
        has_gamma=True,
        has_price_history=has_price_history,
        has_trade_history=False,
        has_semantics=True,
        has_rulebook_score=True,
        has_implications=True,
        has_embeddings=False,
        has_backfill_coverage=True,
        price_points_count=10 if has_price_history else 0,
        trade_points_count=0,
        first_price_ts_ms=_TS,
        last_price_ts_ms=_TS + 3_600_000,
        missing_price_gap_count=0,
        largest_price_gap_ms=0,
        price_min=Decimal("0.1"),
        price_max=Decimal("0.9"),
        price_out_of_bounds_count=0,
        duplicate_timestamp_count=0,
        coverage_score=1.0 if has_price_history else 0.0,
        recommended_for_backtest=has_price_history,
        exclusion_reasons_json="[]",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


def _dec(rel_id: str, context_space_id: str, strategy_lane: str = "strict_context_valid") -> ContextRelationshipDecisionRow:
    return ContextRelationshipDecisionRow(
        decision_id=f"d_{rel_id}",
        relationship_id=rel_id,
        context_space_id=context_space_id,
        context_rule_ids_json="[]",
        previous_validation_status="accepted",
        new_validation_status="accepted",
        previous_strategy_eligibility="eligible",
        new_strategy_eligibility="eligible",
        strategy_lane=strategy_lane,
        decision_reason="template match",
        evidence_summary="test",
        schema_version=1,
        ingested_ts_ms=_TS,
    )


# ─── _filter_typed_trades ─────────────────────────────────────────────────────


def test_filter_typed_trades_drops_diagnostic_only():
    rows = [
        {
            "trade_id": "t1",
            "relationship_id": "r1",
            "relationship_subtype": "same_topic_no_trade",
            "outcome_space_id": "s",
        },
        {
            "trade_id": "t2",
            "relationship_id": "r2",
            "relationship_subtype": "championship_implies_conference",
            "context_space_id": "sports_championship_conference_progression",
        },
    ]
    kept, drops = _filter_typed_trades(rows)
    assert len(kept) == 1
    assert kept[0].trade_id == "t2"
    assert any("diagnostic-only" in d for d in drops)


def test_filter_typed_trades_infers_family_from_context_space_id():
    rows = [
        {
            "trade_id": "t1",
            "relationship_id": "r1",
            "relationship_subtype": "",
            "context_space_id": "sports_championship_conference_progression",
        },
    ]
    kept, _ = _filter_typed_trades(rows)
    assert len(kept) == 1
    assert kept[0].strategy_family == "nesting"


# ─── grade_space: each grade path ────────────────────────────────────────────


def test_grade_c_no_price_history():
    """Relationships exist but no price coverage → C_INFRASTRUCTURE_BLOCKED."""
    s = SpaceSummaryRow(
        space_id="btc_threshold",
        relationship_count=5,
        strict_relationship_count=3,
        price_history_coverage_pct=0.0,
    )
    g, action = _grade_space(s)
    assert g == "C_INFRASTRUCTURE_BLOCKED"
    assert "price" in action.lower() or "backfill" in action.lower() or "fix" in action.lower()


def test_grade_d_has_coverage_no_violations():
    """Relationships + coverage but zero signals → D_VALID_BUT_STRATEGICALLY_WEAK."""
    s = SpaceSummaryRow(
        space_id="world_cup",
        relationship_count=50,
        strict_relationship_count=20,
        price_history_coverage_pct=0.85,
        gross_violation_count=0,
        accepted_trade_count=0,
    )
    g, _ = _grade_space(s)
    assert g == "D_VALID_BUT_STRATEGICALLY_WEAK"


def test_grade_b_violations_blocked_by_economic_gate():
    """Gross violations exist but accepted trades = 0, blocked by economic gate → B."""
    s = SpaceSummaryRow(
        space_id="epl_ranking",
        relationship_count=10,
        strict_relationship_count=5,
        price_history_coverage_pct=0.9,
        gross_violation_count=15,
        net_violation_count=0,
        accepted_trade_count=0,
        primary_blocker="economic_or_replay",
    )
    g, _ = _grade_space(s)
    assert g == "B_PROMISING_GATE_BLOCKED"


def test_grade_b_violations_no_blocker_identified():
    """Gross violations present, no blocker → also B (needs investigation)."""
    s = SpaceSummaryRow(
        space_id="nba_ranking",
        relationship_count=8,
        strict_relationship_count=4,
        price_history_coverage_pct=0.7,
        gross_violation_count=5,
        accepted_trade_count=0,
        primary_blocker="",
    )
    g, _ = _grade_space(s)
    assert g == "B_PROMISING_GATE_BLOCKED"


def test_grade_e_diagnostic_only_no_trades():
    """Space dominated by diagnostic-only relationships and no valid trades → E."""
    s = SpaceSummaryRow(
        space_id="same_topic_space",
        relationship_count=10,
        strict_relationship_count=0,
        diagnostic_only_relationship_count=10,
        gross_violation_count=0,
        accepted_trade_count=0,
    )
    g, _ = _grade_space(s)
    assert g == "E_INVALID_OR_AUDIT_RISK"


def test_grade_e_does_not_fire_when_trades_exist():
    """Even with some diagnostic-only rels, accepted trades prevent grade E."""
    s = SpaceSummaryRow(
        space_id="mixed_space",
        relationship_count=10,
        strict_relationship_count=3,
        diagnostic_only_relationship_count=7,
        accepted_trade_count=5,
        simulated_pnl=10.0,
        distinct_relationships_traded=3,
    )
    g, _ = _grade_space(s)
    assert g != "E_INVALID_OR_AUDIT_RISK"


def test_grade_a_profitable_multi_rel():
    """Positive PnL + economic gates + independent windows → A."""
    s = SpaceSummaryRow(
        space_id="nba_finals",
        relationship_count=8,
        strict_relationship_count=5,
        simulated_pnl=50.0,
        accepted_trade_count=20,
        distinct_relationships_traded=5,
        independent_violation_windows=10,
        median_trade_return_pct=0.02,
        total_return_pct=0.01,
        dominant_relationship_share_of_pnl=0.6,
        price_history_coverage_pct=1.0,
    )
    g, _ = _grade_space(s)
    assert g == "A_PROFITABLE_ROBUST_CANDIDATE"


def test_grade_f_overfit():
    """Single-relationship dominance → F."""
    s = SpaceSummaryRow(
        space_id="epl_finish",
        simulated_pnl=40.0,
        accepted_trade_count=3,
        distinct_relationships_traded=1,
        dominant_relationship_share_of_pnl=1.0,
    )
    g, _ = _grade_space(s)
    assert g == "F_OVERFIT_ONE_OFF"


def test_grade_g_low_median_trade_return():
    s = SpaceSummaryRow(
        space_id="tiny_multi_rel",
        simulated_pnl=5.0,
        accepted_trade_count=10,
        distinct_relationships_traded=3,
        independent_violation_windows=5,
        median_trade_return_pct=0.0005,
        total_return_pct=0.01,
        dominant_relationship_share_of_pnl=0.5,
    )
    g, action = _grade_space(s)
    assert g == "G_ECONOMICALLY_TINY_SIGNAL"
    assert "per-trade" in action


def test_grade_g_few_independent_windows():
    s = SpaceSummaryRow(
        space_id="few_windows",
        simulated_pnl=20.0,
        accepted_trade_count=10,
        distinct_relationships_traded=3,
        independent_violation_windows=1,
        median_trade_return_pct=0.05,
        total_return_pct=0.03,
        dominant_relationship_share_of_pnl=0.5,
    )
    g, _ = _grade_space(s)
    assert g == "G_ECONOMICALLY_TINY_SIGNAL"


def test_grade_a_passes_full_economic_gates():
    s = SpaceSummaryRow(
        space_id="full_gate_pass",
        simulated_pnl=50.0,
        accepted_trade_count=20,
        distinct_relationships_traded=5,
        independent_violation_windows=10,
        median_trade_return_pct=0.02,
        total_return_pct=0.01,
        dominant_relationship_share_of_pnl=0.5,
    )
    g, _ = _grade_space(s)
    assert g == "A_PROFITABLE_ROBUST_CANDIDATE"


def test_grade_a_fails_when_median_below_1pct():
    s = SpaceSummaryRow(
        space_id="median_too_low",
        simulated_pnl=50.0,
        accepted_trade_count=20,
        distinct_relationships_traded=5,
        independent_violation_windows=10,
        median_trade_return_pct=0.005,
        total_return_pct=0.01,
        dominant_relationship_share_of_pnl=0.5,
    )
    g, _ = _grade_space(s)
    assert g == "G_ECONOMICALLY_TINY_SIGNAL"


def test_existing_sports_championship_fixture_grades_g_not_a():
    s = SpaceSummaryRow(
        space_id="sports_championship_conference_progression",
        simulated_pnl=5.98,
        accepted_trade_count=4,
        distinct_relationships_traded=3,
        independent_violation_windows=1,
        median_trade_return_pct=0.0115,
        total_return_pct=0.0115,
        dominant_relationship_share_of_pnl=0.5,
    )
    g, _ = _grade_space(s)
    assert g == "G_ECONOMICALLY_TINY_SIGNAL"
    assert g != "A_PROFITABLE_ROBUST_CANDIDATE"


# ─── _build_summaries: full store + backtest merge ────────────────────────────


def test_build_summaries_includes_store_only_spaces():
    """A space from the store with 0 backtest data still gets a summary row."""
    universe = {
        "world_cup_champion": _SpaceBaseStats(
            space_id="world_cup_champion",
            attribution_status="outcome_space",
            total_rel_count=50,
            strategy_eligible_rel_count=40,
            strict_lane_rel_count=20,
            market_ids={"m1", "m2"},
            markets_with_price_history=set(),  # no coverage
        ),
    }
    summaries = _build_summaries(
        universe,
        trades=[],
        blocked=[],
        signals=[],
        bundle_diagnostics=[],
        bundle_trades=[],
        diagnostic_only_from_run=[],
    )
    by_id = {s.space_id: s for s in summaries}
    assert "world_cup_champion" in by_id
    space = by_id["world_cup_champion"]
    assert space.relationship_count == 50
    assert space.price_history_coverage_pct == 0.0
    assert space.accepted_trade_count == 0


def test_build_summaries_bundle_only_space_appears():
    """A bundle space that had diagnostics but no accepted trades still appears."""
    from polymarket_arb.research.row_contracts import BundleDiagnosticRow
    bundle_diags = [
        BundleDiagnosticRow(
            space_id="world_cup_2026",
            bundle_basket="buy_all_no",
            observed_candidates=32,
            completeness_status="complete",
            gross_edge=0.05,
            net_edge_after_costs=0.02,
        ),
    ]
    summaries = _build_summaries(
        {},  # no store entries for this space
        trades=[],
        blocked=[],
        signals=[],
        bundle_diagnostics=bundle_diags,
        bundle_trades=[],
        diagnostic_only_from_run=[],
    )
    by_id = {s.space_id: s for s in summaries}
    assert "world_cup_2026" in by_id
    space = by_id["world_cup_2026"]
    assert space.gross_violation_count >= 1


def test_build_summaries_integrity_warning_no_gross_violations():
    """Accepted trades with zero gross_violation_count triggers integrity warning."""

    trades = [
        AcceptedSimulatedTradeRow(
            trade_id="t1",
            relationship_id="r1",
            space_id="nba_space",
            space_attribution_status="context_space",
            strategy_family="nesting",
            leg="a",
            notional_usdc=5.0,
        ),
        AcceptedSimulatedTradeRow(
            trade_id="t1",
            relationship_id="r1",
            space_id="nba_space",
            space_attribution_status="context_space",
            strategy_family="nesting",
            leg="b",
            notional_usdc=5.0,
        ),
    ]
    summaries = _build_summaries(
        {},
        trades=trades,
        blocked=[],
        signals=[],  # no signals → gross_violation_count stays 0
        bundle_diagnostics=[],
        bundle_trades=[],
        diagnostic_only_from_run=[],
    )
    nba = next(s for s in summaries if s.space_id == "nba_space")
    assert nba.accepted_trade_count == 1
    assert nba.gross_violation_count == 0
    assert "accepted_trades_without" in nba.integrity_warning


def test_per_trade_economics_computed():
    trades = [
        AcceptedSimulatedTradeRow(
            trade_id="t1",
            relationship_id="r1",
            space_id="econ_space",
            space_attribution_status="context_space",
            strategy_family="nesting",
            leg="a",
            notional_usdc=100.0,
            net_edge_after_cost=0.02,
            violation_window_id="w1",
        ),
        AcceptedSimulatedTradeRow(
            trade_id="t1",
            relationship_id="r1",
            space_id="econ_space",
            space_attribution_status="context_space",
            strategy_family="nesting",
            leg="b",
            notional_usdc=50.0,
            net_edge_after_cost=0.04,
            violation_window_id="w1",
        ),
        AcceptedSimulatedTradeRow(
            trade_id="t2",
            relationship_id="r2",
            space_id="econ_space",
            space_attribution_status="context_space",
            strategy_family="nesting",
            leg="a",
            notional_usdc=100.0,
            net_edge_after_cost=0.01,
            violation_window_id="w2",
        ),
        AcceptedSimulatedTradeRow(
            trade_id="t2",
            relationship_id="r2",
            space_id="econ_space",
            space_attribution_status="context_space",
            strategy_family="nesting",
            leg="b",
            notional_usdc=100.0,
            net_edge_after_cost=0.01,
            violation_window_id="w2",
        ),
    ]
    summaries = _build_summaries(
        {},
        trades=trades,
        blocked=[],
        signals=[],
        bundle_diagnostics=[],
        bundle_trades=[],
        diagnostic_only_from_run=[],
    )
    s = next(row for row in summaries if row.space_id == "econ_space")
    assert s.total_trade_cost == pytest.approx(350.0)
    assert s.simulated_pnl == pytest.approx(6.0)
    assert s.total_return_pct == pytest.approx(6.0 / 350.0)
    assert s.average_trade_return_pct == pytest.approx(((4.0 / 150.0) + (2.0 / 200.0)) / 2)
    assert s.median_trade_return_pct == pytest.approx(((4.0 / 150.0) + (2.0 / 200.0)) / 2)
    assert s.independent_violation_windows == 2


def test_build_summaries_diagnostic_only_not_in_trade_totals():
    """Diagnostic-only relationships are counted separately, not in trade totals."""
    from polymarket_arb.research.row_contracts import DiagnosticOnlyRelationshipRow

    diag = [
        DiagnosticOnlyRelationshipRow(
            relationship_id="rd",
            relationship_subtype="same_topic_no_trade",
            space_id="fake_space",
        ),
    ]
    summaries = _build_summaries(
        {},
        trades=[],
        blocked=[],
        signals=[],
        bundle_diagnostics=[],
        bundle_trades=[],
        diagnostic_only_from_run=diag,
    )
    fake = next((s for s in summaries if s.space_id == "fake_space"), None)
    assert fake is not None
    assert fake.diagnostic_only_relationship_count >= 1
    assert fake.accepted_trade_count == 0
    assert fake.gross_violation_count == 0


# ─── run_space_sweep integration: store-seeded universe ───────────────────────


def _seed_store_spaces(data_root: Path) -> None:
    """Write relationships for 3 distinct spaces into the Parquet store."""
    rel_repo = ParquetRelationshipCandidatesRepository(data_root)
    cov_repo = ParquetBackfillCoverageRepository(data_root)
    dec_repo = ParquetContextRelationshipDecisionsRepository(data_root)

    # space A: has rels + coverage + a strict context decision
    rel_repo.append(_rel("r_a1", "space_a", market_id_a="m_a1", market_id_b="m_a2"))
    rel_repo.append(_rel("r_a2", "space_a", market_id_a="m_a1", market_id_b="m_a3"))
    cov_repo.append(_cov("m_a1", has_price_history=True))
    cov_repo.append(_cov("m_a2", has_price_history=True))
    cov_repo.append(_cov("m_a3", has_price_history=True))
    dec_repo.append(_dec("r_a1", "space_a_context", "strict_context_valid"))
    dec_repo.append(_dec("r_a2", "space_a_context", "strict_context_valid"))

    # space B: has rels but NO price coverage → should be C_INFRASTRUCTURE_BLOCKED
    rel_repo.append(_rel("r_b1", "space_b_no_cov", market_id_a="m_b1", market_id_b="m_b2"))
    rel_repo.append(_rel("r_b2", "space_b_no_cov", market_id_a="m_b1", market_id_b="m_b3"))
    # no coverage rows for these markets

    # space C: diagnostic-only relationships
    rel_repo.append(_rel(
        "r_c1", "space_c_diag",
        relationship_subtype="same_topic_no_trade",
        validation_status="rejected",
        strategy_eligibility_status="ineligible",
    ))


def test_run_space_sweep_surfaces_all_store_spaces(tmp_data_root):
    """run_space_sweep must include ALL spaces from the Parquet store."""
    _seed_store_spaces(tmp_data_root)
    result = run_space_sweep(data_root=tmp_data_root, run_id="no_such_run")
    by_id = {s.space_id: s for s in result.summaries}
    # space_a and space_a_context — note outcome_space_id "space_a" takes precedence
    assert "space_a" in by_id
    # space_b — no coverage
    assert "space_b_no_cov" in by_id
    # space_c — diagnostic-only; might be grouped under space_c_diag
    assert any("space_c" in sid for sid in by_id)


def test_run_space_sweep_grades_no_coverage_as_c(tmp_data_root):
    _seed_store_spaces(tmp_data_root)
    result = run_space_sweep(data_root=tmp_data_root, run_id="no_such_run")
    by_id = {s.space_id: s for s in result.summaries}
    s = by_id["space_b_no_cov"]
    assert s.space_grade == "C_INFRASTRUCTURE_BLOCKED"


def test_run_space_sweep_grades_diagnostic_only_as_e_or_ungraded(tmp_data_root):
    _seed_store_spaces(tmp_data_root)
    result = run_space_sweep(data_root=tmp_data_root, run_id="no_such_run")
    by_id = {s.space_id: s for s in result.summaries}
    # Find the diagnostic-only space
    diag_space = next((s for sid, s in by_id.items() if "diag" in sid or "c_diag" in sid), None)
    if diag_space:
        assert diag_space.space_grade in ("E_INVALID_OR_AUDIT_RISK", "UNGRADED")


def test_run_space_sweep_with_backtest_signals_and_blockers(tmp_data_root):
    """Backtest data overlaid on store universe: blocked → B, not just 'ungraded'."""
    _seed_store_spaces(tmp_data_root)

    # Write backtest signals for space_a_context (gross violations but no accepted trades)
    run_id = "test_b_grade"
    lane_dir = tmp_data_root / "backtests" / run_id / "context_aware" / "strict_context_valid"
    _write_csv(lane_dir / "signals.csv", [
        {
            "relationship_id": "r_a1",
            "context_space_id": "space_a_context",
            "relationship_subtype": "championship_implies_conference",
            "accepted_for_simulation": "false",
            "gross_edge": "0.05",
        },
    ])
    _write_csv(lane_dir / "trades.csv", [])
    _write_csv(lane_dir / "rejected_candidates.csv", [
        {
            "relationship_id": "r_a1",
            "context_space_id": "space_a_context",
            "rejection_reason": "economic_or_replay",
        },
    ])
    _write_json(lane_dir / "funnel_audit.json", {"counts": {}})

    result = run_space_sweep(data_root=tmp_data_root, run_id=run_id)
    by_id = {s.space_id: s for s in result.summaries}

    # space_a should see gross violations and blocked opportunities
    ctx = by_id.get("space_a_context") or by_id.get("space_a")
    assert ctx is not None
    assert ctx.gross_violation_count >= 1
    assert ctx.accepted_trade_count == 0


def test_run_space_sweep_report_integrity_ok_when_run_missing(tmp_data_root):
    """Missing run dir should produce store-only result, report_integrity=ok (not failed)."""
    _seed_store_spaces(tmp_data_root)
    result = run_space_sweep(data_root=tmp_data_root, run_id="does_not_exist")
    assert result.report_integrity == "ok"
    assert len(result.summaries) >= 2  # at least store spaces


def test_run_space_sweep_more_spaces_than_just_traded(tmp_data_root):
    """Total spaces analysed must exceed the number of spaces with accepted trades."""
    _seed_store_spaces(tmp_data_root)

    run_id = "prove_expansion"
    lane_dir = tmp_data_root / "backtests" / run_id / "context_aware" / "strict_context_valid"
    _write_csv(lane_dir / "trades.csv", [
        {
            "trade_id": "t1",
            "relationship_id": "r_a1",
            "context_space_id": "space_a_context",
            "relationship_subtype": "championship_implies_conference",
            "leg": "a",
            "notional_usdc": "10",
        },
    ])
    _write_json(lane_dir / "funnel_audit.json", {"counts": {}})

    result = run_space_sweep(data_root=tmp_data_root, run_id=run_id)
    spaces_with_trades = sum(1 for s in result.summaries if s.accepted_trade_count > 0)
    total_spaces = len(result.summaries)
    assert total_spaces > spaces_with_trades, (
        f"Expected more total spaces ({total_spaces}) than spaces with trades ({spaces_with_trades})"
    )


def test_run_space_sweep_existing_tests_still_pass(tmp_data_root):
    """Legacy: sweeping a run with trades still surfaces those trades correctly."""
    run_id = "legacy_test"
    lane_dir = tmp_data_root / "backtests" / run_id / "context_aware" / "strict_context_valid"
    _write_csv(lane_dir / "trades.csv", [
        {
            "trade_id": "t1",
            "relationship_id": "r1",
            "context_space_id": "nba_finals",
            "relationship_subtype": "championship_implies_conference",
            "leg": "a",
            "notional_usdc": "5",
            "net_edge_after_cost": "0.05",
            "gross_edge": "0.08",
        },
        {
            "trade_id": "t1",
            "relationship_id": "r1",
            "context_space_id": "nba_finals",
            "relationship_subtype": "championship_implies_conference",
            "leg": "b",
            "notional_usdc": "5",
            "net_edge_after_cost": "0.05",
            "gross_edge": "0.08",
        },
    ])
    _write_csv(lane_dir / "signals.csv", [
        {
            "relationship_id": "r1",
            "context_space_id": "nba_finals",
            "relationship_subtype": "championship_implies_conference",
            "accepted_for_simulation": "true",
            "gross_edge": "0.08",
        },
    ])
    _write_csv(lane_dir / "rejected_candidates.csv", [
        {
            "relationship_id": "r99",
            "context_space_id": "nba_finals",
            "rejection_reason": "cash_limit",
        },
    ])
    _write_json(lane_dir / "funnel_audit.json", {"counts": {}})

    result = run_space_sweep(data_root=tmp_data_root, run_id=run_id)
    by_id = {s.space_id: s for s in result.summaries}
    assert "nba_finals" in by_id
    nba = by_id["nba_finals"]
    assert nba.raw_fill_count == 2
    assert nba.accepted_trade_count == 1
    assert nba.gross_violation_count >= 1
    assert nba.primary_blocker == "economic_or_replay"
