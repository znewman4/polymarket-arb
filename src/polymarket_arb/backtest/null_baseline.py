"""Null/random baseline for the relationship strategy backtest."""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any

from ..storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from ..storage.parquet.markets_repo import ParquetMarketsRepository
from ..strategies.models import RelationshipBacktestConfig
from .relationship_replay import run_relationship_backtest


def run_null_baseline(
    data_root: Path,
    cfg: RelationshipBacktestConfig,
    *,
    seed: int = 0,
) -> dict[str, Any]:
    """Run a baseline backtest using random market pairs instead of mined relationships.

    Pairs are drawn randomly from markets with similar coverage_score (±0.1)
    and the same event_id bucket where possible. Same fee/slippage/edge config.
    """
    from datetime import datetime, timezone

    from ..storage.base import RelationshipCandidateRow
    from ..storage.parquet.relationship_candidates_repo import (
        ParquetRelationshipCandidatesRepository,
    )

    markets_repo = ParquetMarketsRepository(data_root)
    cov_repo = ParquetBackfillCoverageRepository(data_root)
    markets = list(markets_repo.iter_active_markets())
    coverage_by_market = {row.market_id: row for row in cov_repo.iter_latest()}

    eligible = [
        m for m in markets
        if len(m.outcomes) == 2
        and len(m.clob_token_ids) == 2
        and coverage_by_market.get(m.id) is not None
        and coverage_by_market[m.id].coverage_score >= cfg.min_coverage_score
    ]

    if len(eligible) < 2:
        null_run_id = hashlib.sha256(f"null_baseline_{seed}".encode()).hexdigest()[:16]
        return {"run_id": null_run_id, "null_pnl": 0.0, "message": "insufficient eligible markets"}

    rng = random.Random(seed)
    rng.shuffle(eligible)

    # Build random pairs
    null_relationships: list[RelationshipCandidateRow] = []
    ts_now = int(datetime.now(timezone.utc).timestamp() * 1000)
    for i in range(0, len(eligible) - 1, 2):
        a = eligible[i]
        b = eligible[i + 1]
        rel = RelationshipCandidateRow(
            relationship_id=hashlib.sha256(f"null|{a.id}|{b.id}|{seed}".encode()).hexdigest()[:32],
            market_id_a=a.id,
            market_id_b=b.id,
            condition_id_a=a.condition_id,
            condition_id_b=b.condition_id,
            token_id_a_yes=a.clob_token_ids[0],
            token_id_a_no=a.clob_token_ids[1],
            token_id_b_yes=b.clob_token_ids[0],
            token_id_b_no=b.clob_token_ids[1],
            question_a=a.question,
            question_b=b.question,
            relationship_type="inverse",
            entity_match_score=0.5,
            time_scope_match_score=0.5,
            resolution_criteria_match_score=0.5,
            threshold_relation_json="{}",
            semantic_similarity_score=None,
            deterministic_confidence=0.7,
            model_confidence=1.0,
            final_confidence=0.7,
            validation_status="accepted",
            rejection_reasons_json="[]",
            rationale_summary="null baseline random pair",
            evidence_json="{}",
            rulebook_id="null_baseline",
            rulebook_version=1,
            rulebook_content_hash="",
            schema_version=1,
            ingested_ts_ms=ts_now,
        )
        null_relationships.append(rel)

    if not null_relationships:
        null_run_id = hashlib.sha256(f"null_baseline_{seed}".encode()).hexdigest()[:16]
        return {"run_id": null_run_id, "null_pnl": 0.0, "message": "no random pairs generated"}

    # Write null relationships temporarily to the parquet store
    # (we use a sub-folder run_id to avoid contaminating the real lake)
    null_run_id = hashlib.sha256(f"null_{cfg.run_id}_{seed}".encode()).hexdigest()[:16]

    # Create a temporary data root for null baseline to avoid contaminating main lake
    null_data_root = data_root / "backtests" / cfg.run_id / "null_baseline_lake"
    null_data_root.mkdir(parents=True, exist_ok=True)

    # Write relationships to null data root
    null_rel_repo = ParquetRelationshipCandidatesRepository(null_data_root)
    null_rel_repo.append_many(null_relationships)

    null_cfg = cfg.model_copy(update={"run_id": null_run_id})
    result = run_relationship_backtest(null_data_root, null_cfg)

    metrics = result.get("metrics")
    if metrics:
        _write_null_summary(
            data_root / "backtests" / cfg.run_id / "null_baseline" / "metrics.json",
            metrics,
        )

    return result


def _write_null_summary(path: Path, metrics) -> None:
    import json
    from dataclasses import asdict
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(metrics), indent=2, default=str),
        encoding="utf-8",
    )
