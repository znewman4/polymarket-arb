"""Tests for the template bundle grouper and scanner."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_arb.backtest.template_bundle_replay import (
    _template_rel_ids,
    run_template_bundle_scan,
)
from polymarket_arb.storage.base import (
    ContextRelationshipDecisionRow,
    PriceHistoryRow,
    RelationshipCandidateRow,
)
from polymarket_arb.storage.parquet.context_relationship_decisions_repo import (
    ParquetContextRelationshipDecisionsRepository,
)
from polymarket_arb.storage.parquet.price_history_repo import ParquetPriceHistoryRepository
from polymarket_arb.storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)
from polymarket_arb.strategies.category_outcome_spaces import group_template_mutual_exclusion_spaces
from polymarket_arb.strategies.models import CategoryBundleBacktestConfig

TS = int(datetime.now(timezone.utc).timestamp() * 1000)


def _rel(
    rel_id: str,
    market_a: str,
    market_b: str,
    candidate_a: str,
    candidate_b: str,
    outcome_space_id: str = "test_tournament",
    tok_a_yes: str = "ta_yes",
    tok_a_no: str = "ta_no",
    tok_b_yes: str = "tb_yes",
    tok_b_no: str = "tb_no",
    team_a: str | None = None,
    team_b: str | None = None,
) -> RelationshipCandidateRow:
    return RelationshipCandidateRow(
        relationship_id=rel_id,
        market_id_a=market_a,
        market_id_b=market_b,
        condition_id_a=None,
        condition_id_b=None,
        token_id_a_yes=tok_a_yes,
        token_id_a_no=tok_a_no,
        token_id_b_yes=tok_b_yes,
        token_id_b_no=tok_b_no,
        question_a=f"Will {candidate_a} win?",
        question_b=f"Will {candidate_b} win?",
        relationship_type="rejected",
        entity_match_score=0.9,
        time_scope_match_score=0.9,
        resolution_criteria_match_score=0.9,
        threshold_relation_json="{}",
        semantic_similarity_score=None,
        deterministic_confidence=0.8,
        model_confidence=0.8,
        final_confidence=0.8,
        validation_status="rejected",
        rejection_reasons_json="[]",
        rationale_summary="test",
        evidence_json="{}",
        rulebook_id="v2",
        rulebook_version=2,
        rulebook_content_hash="hash",
        candidate_a=candidate_a,
        candidate_b=candidate_b,
        team_a=team_a,
        team_b=team_b,
        outcome_space_id=outcome_space_id,
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _decision(
    rel_id: str,
    lane: str = "strict_context_valid",
    context_space_id: str = "sports_title_competition_mutual_exclusion",
    template_id: str = "sports_title_winners_mutually_exclusive_v1",
) -> ContextRelationshipDecisionRow:
    return ContextRelationshipDecisionRow(
        decision_id=f"dec_{rel_id}",
        relationship_id=rel_id,
        context_space_id=context_space_id,
        context_rule_ids_json="[]",
        previous_validation_status="rejected",
        new_validation_status="accepted",
        previous_strategy_eligibility="ineligible",
        new_strategy_eligibility="eligible",
        strategy_lane=lane,
        decision_reason=f"world context reviewed; market terms incomplete; template={template_id}",
        evidence_summary="test",
        schema_version=1,
        ingested_ts_ms=TS,
    )


def _price(token_id: str, market_id: str, price: str) -> PriceHistoryRow:
    return PriceHistoryRow(
        market_id=market_id,
        condition_id=f"cond_{market_id}",
        token_id=token_id,
        outcome="Yes",
        ts_ms=TS,
        price=Decimal(price),
        source="test",
        fidelity="hourly",
        interval="1h",
        schema_version=1,
        ingested_ts_ms=TS,
    )


class TestGroupTemplateMutualExclusionSpaces:
    def test_groups_by_outcome_space_id(self) -> None:
        rels = [
            _rel("r1", "ma", "mb", "Brazil", "Netherlands", "world_cup_champion",
                 "ta", "ta_no", "tb", "tb_no", team_a="Brazil", team_b="Netherlands"),
            _rel("r2", "ma", "mc", "Brazil", "Argentina", "world_cup_champion",
                 "ta", "ta_no", "tc", "tc_no", team_a="Brazil", team_b="Argentina"),
        ]
        spaces = group_template_mutual_exclusion_spaces(
            rels, template_rel_ids={"r1", "r2"}
        )
        assert len(spaces) == 1
        space = spaces[0]
        assert space.outcome_space_id == "world_cup_champion"
        # 3 unique teams: Brazil, Netherlands, Argentina
        assert len(space.candidates) == 3
        assert space.registry_status == "template_auto_applied"
        assert space.allow_bundle_backtest is True

    def test_excludes_empty_outcome_space_id(self) -> None:
        rels = [
            _rel("r1", "ma", "mb", "A", "B", "", "ta", "ta_no", "tb", "tb_no"),
        ]
        spaces = group_template_mutual_exclusion_spaces(rels, template_rel_ids={"r1"})
        assert len(spaces) == 0

    def test_excludes_same_topic_space_id(self) -> None:
        rels = [
            _rel("r1", "ma", "mb", "A", "B", "same_topic_no_trade"),
        ]
        spaces = group_template_mutual_exclusion_spaces(rels, template_rel_ids={"r1"})
        assert len(spaces) == 0

    def test_only_includes_template_rel_ids(self) -> None:
        rels = [
            _rel("r1", "ma", "mb", "Brazil", "Netherlands", "world_cup_champion"),
            _rel("r2", "ma", "mc", "Brazil", "Argentina", "world_cup_champion"),
        ]
        # Only r1 in template set
        spaces = group_template_mutual_exclusion_spaces(rels, template_rel_ids={"r1"})
        assert len(spaces) == 1
        assert len(spaces[0].candidates) == 2  # Brazil + Netherlands only

    def test_multiple_spaces_from_different_outcome_space_ids(self) -> None:
        rels = [
            _rel("r1", "ma", "mb", "Brazil", "Netherlands", "world_cup_champion",
                 "ta", "ta_no", "tb", "tb_no"),
            _rel("r2", "mc", "md", "Trump", "Biden", "2028_us_presidential_election",
                 "tc", "tc_no", "td", "td_no"),
        ]
        spaces = group_template_mutual_exclusion_spaces(rels, template_rel_ids={"r1", "r2"})
        assert len(spaces) == 2
        space_ids = {s.outcome_space_id for s in spaces}
        assert "world_cup_champion" in space_ids
        assert "2028_us_presidential_election" in space_ids

    def test_deduplicates_candidates_within_space(self) -> None:
        """Same market appearing in multiple pairs should only count once."""
        rels = [
            _rel("r1", "ma", "mb", "Brazil", "Netherlands", "world_cup_champion",
                 "ta", "ta_no", "tb", "tb_no", team_a="Brazil", team_b="Netherlands"),
            _rel("r2", "ma", "mc", "Brazil", "France", "world_cup_champion",
                 "ta", "ta_no", "tc", "tc_no", team_a="Brazil", team_b="France"),
        ]
        spaces = group_template_mutual_exclusion_spaces(rels, template_rel_ids={"r1", "r2"})
        assert len(spaces) == 1
        candidates = {c.candidate for c in spaces[0].candidates}
        assert "Brazil" in candidates
        assert "Netherlands" in candidates
        assert "France" in candidates
        assert len(spaces[0].candidates) == 3  # not 4

    def test_requires_token_ids(self) -> None:
        """Candidates without YES/NO token IDs are skipped."""
        rels = [
            _rel("r1", "ma", "mb", "Brazil", "Netherlands", "world_cup_champion",
                 tok_a_yes="", tok_a_no=""),  # empty tokens → skipped
        ]
        spaces = group_template_mutual_exclusion_spaces(rels, template_rel_ids={"r1"})
        # At most 1 candidate (market_b still has tokens)
        if spaces:
            assert all(c.yes_token_id for c in spaces[0].candidates)


class TestTemplateBundleScan:
    def _seed(self, tmp_data_root: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        dec_repo = ParquetContextRelationshipDecisionsRepository(tmp_data_root)
        price_repo = ParquetPriceHistoryRepository(tmp_data_root)

        # 3 teams in "test_tournament" outcome space
        rel_repo.append_many([
            _rel("r1", "ma", "mb", "Alpha", "Beta", "test_tournament", "ta", "ta_no", "tb", "tb_no"),
            _rel("r2", "ma", "mc", "Alpha", "Gamma", "test_tournament", "ta", "ta_no", "tc", "tc_no"),
        ])
        dec_repo.append_many([
            _decision("r1", context_space_id="sports_title_competition_mutual_exclusion"),
            _decision("r2", context_space_id="sports_title_competition_mutual_exclusion"),
        ])
        # Price history: Alpha 0.35, Beta 0.30, Gamma 0.25 → sum = 0.90 < 1.0 → underround!
        price_repo.append_many([
            _price("ta", "ma", "0.35"),
            _price("ta_no", "ma", "0.65"),
            _price("tb", "mb", "0.30"),
            _price("tb_no", "mb", "0.70"),
            _price("tc", "mc", "0.25"),
            _price("tc_no", "mc", "0.75"),
        ])

    def test_scan_detects_underround(self, tmp_data_root: Path) -> None:
        self._seed(tmp_data_root)
        cfg = CategoryBundleBacktestConfig(
            run_id="test_scan",
            min_net_edge=Decimal("0.01"),
            fee_bps=Decimal("0"),
            slippage_bps=Decimal("0"),
        )
        result = run_template_bundle_scan(tmp_data_root, cfg)
        rows = result["scan_rows"]
        assert len(rows) == 1
        row = rows[0]
        assert row["outcome_space_id"] == "test_tournament"
        assert row["candidate_count_observed"] == 3
        # sum_yes = 0.35 + 0.30 + 0.25 = 0.90 → buy_all_yes opportunity
        assert Decimal(row["sum_yes_prices"]) == Decimal("0.90")
        assert row["best_executable_basket"] == "buy_all_yes"
        assert Decimal(row["gross_edge"]) > Decimal("0")
        assert row["label"] is not None  # always labelled

    def test_scan_no_spaces_when_no_template_decisions(self, tmp_data_root: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        rel_repo.append(
            _rel("r1", "ma", "mb", "Alpha", "Beta", "test_tournament", "ta", "ta_no", "tb", "tb_no")
        )
        # No decisions → no template_rel_ids
        cfg = CategoryBundleBacktestConfig(run_id="no_dec", min_net_edge=Decimal("0.01"))
        result = run_template_bundle_scan(tmp_data_root, cfg)
        assert len(result["scan_rows"]) == 0

    def test_scan_missing_price_history(self, tmp_data_root: Path) -> None:
        rel_repo = ParquetRelationshipCandidatesRepository(tmp_data_root)
        dec_repo = ParquetContextRelationshipDecisionsRepository(tmp_data_root)
        rel_repo.append(
            _rel("r1", "ma", "mb", "A", "B", "no_prices_space", "ta", "ta_no", "tb", "tb_no")
        )
        dec_repo.append(_decision("r1", context_space_id="sports_title_competition_mutual_exclusion"))
        # No price data → sum_yes=None → rejection_reason="missing_price_history"
        cfg = CategoryBundleBacktestConfig(run_id="no_prices", min_net_edge=Decimal("0.01"))
        result = run_template_bundle_scan(tmp_data_root, cfg)
        assert len(result["scan_rows"]) == 1
        assert result["scan_rows"][0]["rejection_reason"] == "missing_price_history"

    def test_metrics_always_research_only(self, tmp_data_root: Path) -> None:
        self._seed(tmp_data_root)
        cfg = CategoryBundleBacktestConfig(run_id="research_label", min_net_edge=Decimal("0.99"))
        result = run_template_bundle_scan(tmp_data_root, cfg)
        m = result["metrics"]
        assert "RESEARCH-ONLY" in m["label"]
        assert "auto_applied_pending_human_review" in m["label"]

    def test_output_files_written(self, tmp_data_root: Path) -> None:
        self._seed(tmp_data_root)
        cfg = CategoryBundleBacktestConfig(run_id="files_test", min_net_edge=Decimal("0.01"))
        result = run_template_bundle_scan(tmp_data_root, cfg)
        out = result["output_dir"]
        assert (out / "metrics.json").exists()
        assert (out / "bundle_scan.csv").exists()
        assert (out / "bundle_scan.md").exists()


class TestTemplateBundleRelIds:
    def test_only_reviewed_lane_with_template_marker(self, tmp_data_root: Path) -> None:
        dec_repo = ParquetContextRelationshipDecisionsRepository(tmp_data_root)
        dec_repo.append_many([
            _decision("rel_strict", "strict_context_valid",
                      "sports_title_competition_mutual_exclusion",
                      "sports_title_winners_mutually_exclusive_v1"),
            # research_only → excluded
            ContextRelationshipDecisionRow(
                decision_id="dec_research",
                relationship_id="rel_research",
                context_space_id="sports_title_competition_mutual_exclusion",
                context_rule_ids_json="[]",
                previous_validation_status="rejected",
                new_validation_status="rejected",
                previous_strategy_eligibility="ineligible",
                new_strategy_eligibility="ineligible",
                strategy_lane="research_only",
                decision_reason="template=sports_title_winners_mutually_exclusive_v1",
                evidence_summary="",
                schema_version=1,
                ingested_ts_ms=TS,
            ),
            # strict but wrong context space → excluded
            _decision("rel_wrong_space", "strict_context_valid", "nba_championship_conference_progression"),
        ])
        ids = _template_rel_ids(tmp_data_root)
        assert "rel_strict" in ids
        assert "rel_research" not in ids
        assert "rel_wrong_space" not in ids
