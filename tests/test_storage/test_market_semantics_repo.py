from __future__ import annotations

import time

from polymarket_arb.storage.base import MarketSemanticsRow
from polymarket_arb.storage.parquet.market_semantics_repo import (
    ParquetMarketSemanticsRepository,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _row(market_id: str = "m1", *, conf: float = 0.7,
         needs_review: bool = False) -> MarketSemanticsRow:
    return MarketSemanticsRow(
        source_market_id=market_id, source_condition_id="0xc",
        question="q?", canonical_question="q?",
        market_type="binary", subject_entities=[], event_entities=[],
        temporal_phrase=None, temporal_phrase_normalized=None,
        temporal_resolution="vague", exact_deadline_ms=None,
        date_constraints_json="{}", jurisdiction=None,
        positive_resolution_condition="y", negative_resolution_condition="n",
        necessary_conditions_for_yes=[], sufficient_conditions_for_yes=[],
        necessary_conditions_for_no=[], sufficient_conditions_for_no=[], evidence_required=[],
        ambiguity_flags=[], ambiguity_score=None,
        semantic_confidence=conf, needs_manual_review=needs_review,
        explanation_summary=None, flag_rationales_json=None,
        uncertainty_notes_json=None, rule_curation_notes_json=None,
        raw_response_hash="abc" * 21 + "a",
        model_name="mock", prompt_version="market_semantics_v1",
        rulebook_id=None, rulebook_version=None,
        extraction_id="ext_" + market_id,
        schema_version=1, ingested_ts_ms=_now_ms(),
    )


def test_upsert_then_get_latest(tmp_data_root):
    repo = ParquetMarketSemanticsRepository(tmp_data_root, row_group_size=4)
    assert repo.upsert_many([_row("m1"), _row("m2")]) == 2
    out = repo.get_latest("m1")
    assert out is not None and out.source_market_id == "m1"


def test_get_latest_returns_newest_after_two_writes(tmp_data_root):
    repo = ParquetMarketSemanticsRepository(tmp_data_root, row_group_size=4)
    repo.upsert(_row("m1", conf=0.3))
    repo.upsert(_row("m1", conf=0.9))
    out = repo.get_latest("m1")
    assert out is not None
    assert out.semantic_confidence == 0.9


def test_needs_review_filters(tmp_data_root):
    repo = ParquetMarketSemanticsRepository(tmp_data_root, row_group_size=4)
    repo.upsert_many([
        _row("m1", needs_review=False),
        _row("m2", needs_review=True),
        _row("m3", needs_review=True),
    ])
    flagged = repo.needs_review(limit=10)
    ids = sorted(r.source_market_id for r in flagged)
    assert ids == ["m2", "m3"]


def test_iter_latest_can_filter_unscored(tmp_data_root):
    repo = ParquetMarketSemanticsRepository(tmp_data_root, row_group_size=4)
    unscored = _row("m1", conf=0.6)
    scored = _row("m2", conf=0.6)
    scored = scored.__class__(**{**scored.__dict__, "ambiguity_score": 0.1})
    repo.upsert_many([unscored, scored])

    assert {r.source_market_id for r in repo.iter_latest(limit=10)} == {"m1", "m2"}
    assert [r.source_market_id for r in repo.iter_latest(limit=10, unscored_only=True)] == [
        "m1"
    ]


def test_empty_lake(tmp_data_root):
    repo = ParquetMarketSemanticsRepository(tmp_data_root)
    assert repo.get_latest("anything") is None
    assert repo.needs_review() == []
    assert repo.iter_latest() == []
    assert repo.latest_count() == 0
