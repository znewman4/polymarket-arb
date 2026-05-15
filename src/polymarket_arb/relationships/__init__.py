"""Relationship mining package.

Generates, validates, and stores cross-market relationship candidates.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..backfill.market_backfill import select_universe
from ..semantics.rulebook import load_rulebook, rulebook_content_hash, rulebook_path
from ..semantics.rulebook_models import RelationshipRulebook
from ..settings import Settings
from ..storage.base import BackfillCoverageRow, MarketSemanticsRow
from ..storage.parquet.backfill_coverage_repo import ParquetBackfillCoverageRepository
from ..storage.parquet.relationship_candidates_repo import ParquetRelationshipCandidatesRepository
from .candidate_generation import CandidatePair, RelationshipMiningConfig, generate_candidate_pairs
from .models import RelationshipMiningResult
from .validators import validate_all_pairs


def _load_semantics(data_root: Path) -> dict[str, MarketSemanticsRow]:
    """Load latest semantics for all markets from the lake."""
    import duckdb
    glob = str(data_root / "normalised" / "market_semantics" / "dt=*" / "*.parquet")
    if not any((data_root / "normalised" / "market_semantics").glob("dt=*/*.parquet")):
        return {}
    con = duckdb.connect()
    try:
        sql = (
            "SELECT * EXCLUDE rn FROM ("
            "  SELECT *, row_number() OVER (PARTITION BY source_market_id "
            "    ORDER BY ingested_ts_ms DESC) AS rn"
            f"  FROM read_parquet('{glob}', hive_partitioning=true)"
            ") WHERE rn = 1"
        )
        cur = con.execute(sql)
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    finally:
        con.close()

    from dataclasses import fields

    from ..storage.base import MarketSemanticsRow as MSR
    field_names = {f.name for f in fields(MSR)}
    result = {}
    for r in rows:
        r.pop("dt", None)
        filtered = {k: v for k, v in r.items() if k in field_names}
        # Convert JSON-array strings back to lists where needed
        list_fields = (
            "subject_entities", "event_entities", "necessary_conditions_for_yes",
            "sufficient_conditions_for_yes", "necessary_conditions_for_no",
            "sufficient_conditions_for_no", "evidence_required", "ambiguity_flags",
        )
        for list_field in list_fields:
            if list_field in filtered:
                val = filtered[list_field]
                if isinstance(val, str):
                    try:
                        filtered[list_field] = json.loads(val)
                    except Exception:
                        filtered[list_field] = []
                elif val is None:
                    filtered[list_field] = []
            else:
                filtered[list_field] = []
        try:
            sem = MSR(**filtered)
            result[sem.source_market_id] = sem
        except Exception:
            pass
    return result


def _load_coverage(data_root: Path) -> dict[str, BackfillCoverageRow]:
    repo = ParquetBackfillCoverageRepository(data_root)
    return {row.market_id: row for row in repo.iter_latest()}


def generate_relationships(
    settings: Settings,
    *,
    rulebook_path_override: Path | None = None,
    use_embeddings: bool = False,
    market_limit: int | None = None,
) -> RelationshipMiningResult:
    """End-to-end: load markets + semantics + coverage, generate candidates, validate, persist."""
    from ..backfill.models import BackfillConfig
    t0 = time.monotonic()

    data_root = settings.data_root
    config_dir = Path(__file__).resolve().parents[3] / "configs"
    rb_path = rulebook_path_override or rulebook_path(config_dir, "relationship_v2.yaml")
    rb: RelationshipRulebook = load_rulebook(rb_path, kind="relationship")  # type: ignore[assignment]
    rb_hash = rulebook_content_hash(rb_path)

    cfg = BackfillConfig(market_limit=market_limit or 200)
    markets = select_universe(data_root, cfg)
    if market_limit:
        markets = markets[:market_limit]

    semantics_by_market = _load_semantics(data_root)
    coverage_by_market = _load_coverage(data_root)

    events_by_market: dict[str, str | None] = {m.id: m.event_id for m in markets}

    gen_cfg = RelationshipMiningConfig()

    pairs = list(generate_candidate_pairs(
        markets,
        semantics_by_market=semantics_by_market,
        events_by_market=events_by_market,
        embeddings_by_market=None,
        cfg=gen_cfg,
    ))

    rows = list(validate_all_pairs(pairs, semantics_by_market, coverage_by_market, rb, rb_hash))

    repo = ParquetRelationshipCandidatesRepository(data_root)
    repo.append_many(rows)

    result = RelationshipMiningResult()
    result.total_candidates_considered = len(rows)
    rejection_counts: dict[str, int] = {}
    for row in rows:
        result.by_type[row.relationship_type] = result.by_type.get(row.relationship_type, 0) + 1
        if row.validation_status == "accepted":
            result.accepted += 1
        elif row.validation_status == "rejected":
            result.rejected += 1
            reasons = json.loads(row.rejection_reasons_json) or []
            for r in reasons:
                code = r.get("code", "unknown")
                rejection_counts[code] = rejection_counts.get(code, 0) + 1
        else:
            result.needs_manual_review += 1

    result.top_rejection_reasons = dict(sorted(rejection_counts.items(), key=lambda x: -x[1])[:10])
    result.wall_clock_ms = int((time.monotonic() - t0) * 1000)
    return result


__all__ = [
    "CandidatePair",
    "RelationshipMiningConfig",
    "RelationshipMiningResult",
    "generate_candidate_pairs",
    "generate_relationships",
    "validate_all_pairs",
]
