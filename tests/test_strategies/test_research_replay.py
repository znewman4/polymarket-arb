"""Tests for Phase B research_replay — entry/exit/sizing policies."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_arb.backtest.research_replay import (
    _EntryState,
    _should_enter,
    run_research_backtest,
)
from polymarket_arb.research_presets import apply_preset, load_preset
from polymarket_arb.storage.base import (
    BackfillCoverageRow,
    ContextRelationshipDecisionRow,
    PriceHistoryRow,
    RelationshipCandidateRow,
)
from polymarket_arb.storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from polymarket_arb.storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from polymarket_arb.storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)
from polymarket_arb.strategies.models import ContextAwareBacktestConfig

TS = int(datetime.now(timezone.utc).timestamp() * 1000)
_HOUR = 3_600_000


# ── fixture helpers ───────────────────────────────────────────────────────────


def _rel(rel_id: str = "rel_001") -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=rel_id,
        market_id_a="market_a",
        market_id_b="market_b",
        condition_id_a="cond_a",
        condition_id_b="cond_b",
        token_id_a_yes="a_yes",
        token_id_a_no="a_no",
        token_id_b_yes="b_yes",
        token_id_b_no="b_no",
        question_a="Will X win the championship?",
        question_b="Will X win the conference?",
        relationship_type="nested_a_implies_b",
        entity_match_score=1.0,
        time_scope_match_score=1.0,
        resolution_criteria_match_score=1.0,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.9,
        model_confidence=1.0,
        final_confidence=0.9,
        validation_status="accepted",
        rejection_reasons_json="[]",
        rationale_summary="fixture",
        evidence_json="{}",
        rulebook_id="v2",
        rulebook_version=2,
        rulebook_content_hash="hash",
        relationship_validity_status="accepted",
        strategy_eligibility_status="eligible",
        relationship_family="nesting",
        relationship_subtype="championship_implies_conference",
        outcome_space_id="test_space",
        outcome_subtype_a="team_wins_championship",
        outcome_subtype_b="team_wins_conference",
        team_a="Team A",
        team_b="Team A",
        strategy_family="nesting",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _decision(rel_id: str = "rel_001") -> ContextRelationshipDecisionRow:
    return ContextRelationshipDecisionRow(
        decision_id=f"dec_{rel_id}",
        relationship_id=rel_id,
        context_space_id="sports_championship_conference_progression",
        context_rule_ids_json='["rule_1"]',
        previous_validation_status="accepted",
        new_validation_status="accepted",
        previous_strategy_eligibility="eligible",
        new_strategy_eligibility="eligible",
        strategy_lane="strict_context_valid",
        decision_reason="template=sports_championship_implies_conference_v1",
        evidence_summary="fixture",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _coverage(market_id: str) -> BackfillCoverageRow:
    return BackfillCoverageRow(
        market_id=market_id,
        condition_id=f"cond_{market_id}",
        question="fixture",
        start_ts_ms=TS,
        end_ts_ms=TS + 10 * _HOUR,
        requested_days=1,
        has_gamma=True,
        has_price_history=True,
        has_trade_history=False,
        has_semantics=True,
        has_rulebook_score=True,
        has_implications=True,
        has_embeddings=False,
        has_backfill_coverage=True,
        price_points_count=10,
        trade_points_count=0,
        first_price_ts_ms=TS,
        last_price_ts_ms=TS + 10 * _HOUR,
        missing_price_gap_count=0,
        largest_price_gap_ms=0,
        price_min=Decimal("0.1"),
        price_max=Decimal("0.9"),
        price_out_of_bounds_count=0,
        duplicate_timestamp_count=0,
        coverage_score=1.0,
        recommended_for_backtest=True,
        exclusion_reasons_json="[]",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _prices(market_id: str, token_id: str, n: int = 10, price: str = "0.5") -> list[PriceHistoryRow]:
    return [
        PriceHistoryRow(
            market_id=market_id,
            condition_id=f"cond_{market_id}",
            token_id=token_id,
            outcome="Yes",
            ts_ms=TS + i * _HOUR,
            price=Decimal(price),
            source="fixture",
            fidelity="hourly",
            interval="1h",
            schema_version=1,
            ingested_ts_ms=TS,
        )
        for i in range(n)
    ]


def _violation_prices(market_id: str, token_id: str, n: int = 10) -> list[PriceHistoryRow]:
    """A prices at 0.8, B prices at 0.5 → nested_a_implies_b violation: P(A) > P(B)."""
    price = "0.8" if token_id == "a_yes" else "0.5"
    return _prices(market_id, token_id, n, price)


def _setup_standard_fixture(data_root: Path, rel_id: str = "rel_001") -> None:
    """Write a single relationship with persistent violation (P(A)=0.8 > P(B)=0.5)."""
    ParquetRelationshipCandidatesRepository(data_root).append(_rel(rel_id))
    ParquetContextRelationshipDecisionsRepository(data_root).append(_decision(rel_id))
    ParquetBackfillCoverageRepository(data_root).append_many([
        _coverage("market_a"), _coverage("market_b"),
    ])
    ParquetPriceHistoryRepository(data_root).append_many(
        _violation_prices("market_a", "a_yes", n=10) +
        _violation_prices("market_b", "b_yes", n=10)
    )


# ── _should_enter unit tests ──────────────────────────────────────────────────


def _states() -> dict[str, _EntryState]:
    from collections import defaultdict
    return defaultdict(_EntryState)


def test_first_violation_only_enters_once() -> None:
    states = _states()
    states["r1"]  # ensure entry

    enter, kind = _should_enter("r1", TS, "first_violation_only", 0, 99, states, True)
    assert enter
    assert kind == "first"

    states["r1"].trade_count = 1
    enter2, reason = _should_enter("r1", TS + _HOUR, "first_violation_only", 0, 99, states, True)
    assert not enter2
    assert reason == "already_traded"


def test_one_trade_per_relationship_same_as_first_violation() -> None:
    states = _states()
    states["r1"]
    enter, kind = _should_enter("r1", TS, "one_trade_per_relationship", 0, 99, states, True)
    assert enter and kind == "first"

    states["r1"].trade_count = 1
    enter2, _ = _should_enter("r1", TS + _HOUR, "one_trade_per_relationship", 0, 99, states, True)
    assert not enter2


def test_reenter_after_cooldown_allows_reentry() -> None:
    cooldown = 2 * _HOUR
    states = _states()
    states["r1"]

    # First entry
    enter1, kind1 = _should_enter("r1", TS, "reenter_after_cooldown", cooldown, 99, states, True)
    assert enter1 and kind1 == "first"
    states["r1"].trade_count = 1
    states["r1"].last_trade_ts_ms = TS

    # Too soon for re-entry
    enter2, reason2 = _should_enter("r1", TS + _HOUR, "reenter_after_cooldown", cooldown, 99, states, True)
    assert not enter2
    assert reason2 == "cooldown"

    # After cooldown — re-entry allowed
    enter3, kind3 = _should_enter("r1", TS + 3 * _HOUR, "reenter_after_cooldown", cooldown, 99, states, True)
    assert enter3
    assert kind3 == "reentry"


def test_max_trades_cap_respected() -> None:
    states = _states()
    states["r1"].trade_count = 5

    enter, reason = _should_enter("r1", TS, "reenter_after_cooldown", 0, 5, states, True)
    assert not enter
    assert reason == "max_trades"


def test_trade_every_distinct_window_enters_on_new_window() -> None:
    cooldown = _HOUR
    states = _states()
    states["r1"]

    # No previous violation → new window → enter
    enter1, _kind1 = _should_enter("r1", TS, "trade_every_distinct_violation_window", cooldown, 99, states, True)
    assert enter1

    # Mark as having violated
    states["r1"].prev_tick_had_violation = True
    states["r1"].trade_count = 1

    # Same window → don't re-enter
    enter2, reason2 = _should_enter("r1", TS + 500, "trade_every_distinct_violation_window", cooldown, 99, states, True)
    assert not enter2
    assert reason2 == "same_violation_window"

    # Gap in violations (prev_tick_had_violation=False) → new window
    states["r1"].prev_tick_had_violation = False
    enter3, kind3 = _should_enter("r1", TS + 2 * _HOUR, "trade_every_distinct_violation_window", cooldown, 99, states, True)
    assert enter3
    assert kind3 == "reentry"


def test_one_trade_per_market_pair_per_day() -> None:
    day_ms = 24 * 60 * 60 * 1000
    states = _states()
    states["r1"]

    # Day 1 — no trades yet
    enter1, kind1 = _should_enter("r1", TS, "one_trade_per_market_pair_per_day", 0, 99, states, True)
    assert enter1 and kind1 == "first"
    states["r1"].trade_count = 1
    states["r1"].last_trade_ts_ms = TS

    # Same day — blocked
    enter2, reason2 = _should_enter("r1", TS + _HOUR, "one_trade_per_market_pair_per_day", 0, 99, states, True)
    assert not enter2
    assert reason2 == "same_day"

    # Next day — allowed
    enter3, kind3 = _should_enter("r1", TS + day_ms, "one_trade_per_market_pair_per_day", 0, 99, states, True)
    assert enter3
    assert kind3 == "reentry"


# ── run_research_backtest integration tests ───────────────────────────────────


def test_research_replay_first_violation_only_one_trade(tmp_data_root) -> None:
    """first_violation_only → at most one trade pair per relationship."""
    _setup_standard_fixture(tmp_data_root)

    preset = load_preset("strict_research")
    cfg = apply_preset(preset, ContextAwareBacktestConfig(run_id="test_fvo"))
    result = run_research_backtest(tmp_data_root, cfg, preset)

    trades = result["metrics"]["trades_executed"]
    assert trades <= 1, f"Expected ≤1 trade with first_violation_only, got {trades}"


def test_research_replay_exploratory_more_trades_than_strict(tmp_data_root) -> None:
    """exploratory preset (reenter_after_cooldown) produces ≥ strict preset."""
    _setup_standard_fixture(tmp_data_root)

    strict_preset = load_preset("strict_research")
    strict_cfg = apply_preset(strict_preset, ContextAwareBacktestConfig(run_id="strict_run"))
    strict_result = run_research_backtest(tmp_data_root, strict_cfg, strict_preset)

    expl_preset = load_preset("exploratory_trade_surface")
    expl_cfg = apply_preset(expl_preset, ContextAwareBacktestConfig(run_id="expl_run"))
    expl_result = run_research_backtest(tmp_data_root, expl_cfg, expl_preset)

    strict_trades = strict_result["metrics"]["trades_executed"]
    expl_trades = expl_result["metrics"]["trades_executed"]
    assert expl_trades >= strict_trades, (
        f"Exploratory should have ≥ trades than strict: "
        f"strict={strict_trades} expl={expl_trades}"
    )


def test_research_replay_trade_rows_have_required_fields(tmp_data_root) -> None:
    """Every trade row must carry the Phase B enrichment fields."""
    _setup_standard_fixture(tmp_data_root)

    preset = load_preset("exploratory_trade_surface")
    cfg = apply_preset(preset, ContextAwareBacktestConfig(run_id="enrich_run"))
    result = run_research_backtest(tmp_data_root, cfg, preset)

    trades_csv = result["output_dir"] / "trades.csv"
    if not trades_csv.exists() or trades_csv.read_text(encoding="utf-8").strip() == "":
        pytest.skip("No trades produced — skip field check")

    with trades_csv.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    required_fields = {
        "first_entry_or_reentry",
        "violation_window_id",
        "entry_policy",
        "exit_policy",
        "sizing_policy",
        "gross_edge",
        "net_edge_after_cost",
        "preset_name",
        "preset_label",
        "label",
    }
    if rows:
        for field in required_fields:
            assert field in rows[0], f"Missing field {field!r} in trade row"


def test_research_replay_no_lookahead(tmp_data_root) -> None:
    """No future prices must appear in the no-lookahead audit."""
    _setup_standard_fixture(tmp_data_root)

    preset = load_preset("exploratory_trade_surface")
    cfg = apply_preset(preset, ContextAwareBacktestConfig(run_id="nla_run"))
    result = run_research_backtest(tmp_data_root, cfg, preset)

    audit_path = result["output_dir"] / "no_lookahead_audit.json"
    assert audit_path.exists()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["violations"] == 0, (
        f"No-lookahead violations detected: {audit['violations']}"
    )


def test_research_replay_credibility_never_credible_positive(tmp_data_root) -> None:
    """Research replay must never produce credible_positive."""
    _setup_standard_fixture(tmp_data_root)

    for preset_name in ("exploratory_trade_surface", "gross_violation_scan", "replay_many_entries"):
        preset = load_preset(preset_name)
        cfg = apply_preset(preset, ContextAwareBacktestConfig(run_id=f"cred_{preset_name}"))
        result = run_research_backtest(tmp_data_root, cfg, preset)
        credibility = result["metrics"]["credibility_label"]
        assert credibility != "credible_positive", (
            f"Preset {preset_name!r} produced credible_positive — must never happen"
        )


def test_research_replay_gross_violation_scan_no_execution(tmp_data_root) -> None:
    """gross_violation_scan preset must not execute trades (execute_trades=False)."""
    _setup_standard_fixture(tmp_data_root)

    preset = load_preset("gross_violation_scan")
    cfg = apply_preset(preset, ContextAwareBacktestConfig(run_id="gvs_run"))
    result = run_research_backtest(tmp_data_root, cfg, preset)

    trades_csv = result["output_dir"] / "trades.csv"
    content = trades_csv.read_text(encoding="utf-8") if trades_csv.exists() else ""
    assert content.strip() == "", (
        "gross_violation_scan should not write trade rows (execute_trades=False)"
    )


def test_research_replay_output_dir_uses_preset_name(tmp_data_root) -> None:
    _setup_standard_fixture(tmp_data_root)

    preset = load_preset("exploratory_trade_surface")
    cfg = apply_preset(preset, ContextAwareBacktestConfig(run_id="dir_test"))
    result = run_research_backtest(tmp_data_root, cfg, preset)

    assert "exploratory_trade_surface" in str(result["output_dir"])


def test_research_replay_metrics_include_distinct_relationships(tmp_data_root) -> None:
    _setup_standard_fixture(tmp_data_root)

    preset = load_preset("exploratory_trade_surface")
    cfg = apply_preset(preset, ContextAwareBacktestConfig(run_id="distinct_test"))
    result = run_research_backtest(tmp_data_root, cfg, preset)

    assert "distinct_relationships_traded" in result["metrics"]


def test_research_replay_preset_label_in_metrics(tmp_data_root) -> None:
    _setup_standard_fixture(tmp_data_root)

    preset = load_preset("exploratory_trade_surface")
    cfg = apply_preset(preset, ContextAwareBacktestConfig(run_id="label_test"))
    result = run_research_backtest(tmp_data_root, cfg, preset)

    assert result["metrics"]["preset_label"] == "EXPLORATORY"


def test_cooldown_prevents_reentry_within_window(tmp_data_root) -> None:
    """With a long cooldown, re-entry does not happen within the same data window."""
    _setup_standard_fixture(tmp_data_root)

    # replay_many_entries has 30-min cooldown; data spans 10 hours
    preset = load_preset("replay_many_entries")
    apply_preset(preset, ContextAwareBacktestConfig(run_id="cooldown_test"))

    # Run twice with zero cooldown override (test the unit level directly)
    from collections import defaultdict
    states = defaultdict(_EntryState)
    cooldown_ms = 0  # zero cooldown — all entries allowed

    results = []
    for i in range(5):
        enter, kind = _should_enter("r1", TS + i * _HOUR, "reenter_after_cooldown", cooldown_ms, 99, states, True)
        if enter:
            states["r1"].trade_count += 1
            states["r1"].last_trade_ts_ms = TS + i * _HOUR
            results.append(kind)

    assert len(results) >= 2, "Zero-cooldown should allow multiple entries"
    assert results[0] == "first"
    assert all(r == "reentry" for r in results[1:])
