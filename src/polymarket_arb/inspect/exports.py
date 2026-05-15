"""CSV exports for manual semantic review."""

from __future__ import annotations

import csv
import random
from pathlib import Path
from typing import Any

import duckdb


def export_semantics_review(
    data_root: Path,
    out: Path,
    *,
    sample: int = 100,
    only_review_needed: bool = False,
    sort: str = "ambiguity_score_desc",
) -> int:
    rows = _rows(data_root)
    if only_review_needed:
        rows = [r for r in rows if r.get("needs_manual_review")]
    if sort == "ambiguity_score_desc":
        rows.sort(key=lambda r: float(r.get("ambiguity_score") or -1), reverse=True)
    elif sort == "newest":
        rows.sort(key=lambda r: int(r.get("sem_ingested_ts_ms") or 0), reverse=True)
    elif sort == "random":
        random.Random(1337).shuffle(rows)
    rows = rows[:sample]
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "market_id",
        "condition_id",
        "question",
        "canonical_question",
        "positive_resolution_condition",
        "negative_resolution_condition",
        "temporal_phrase",
        "temporal_resolution",
        "exact_deadline_ms",
        "ambiguity_flags",
        "ambiguity_score",
        "semantic_confidence",
        "needs_manual_review",
        "explanation_summary",
        "flag_rationales_json",
        "uncertainty_notes_json",
        "rule_curation_notes_json",
        "latest_market_score",
        "recommendation",
    ]
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c) for c in cols})
    return len(rows)


def _rows(data_root: Path) -> list[dict[str, Any]]:
    sem_glob = data_root / "normalised" / "market_semantics" / "dt=*" / "*.parquet"
    if not list((data_root / "normalised" / "market_semantics").glob("dt=*/*.parquet")):
        return []
    con = duckdb.connect()
    try:
        markets_glob = data_root / "normalised" / "markets" / "dt=*" / "*.parquet"
        scores_glob = data_root / "normalised" / "market_scores" / "dt=*" / "*.parquet"
        has_markets = bool(list((data_root / "normalised" / "markets").glob("dt=*/*.parquet")))
        has_scores = bool(list((data_root / "normalised" / "market_scores").glob("dt=*/*.parquet")))
        market_cte = (
            f"markets AS (SELECT * EXCLUDE rn FROM (SELECT *, row_number() OVER "
            f"(PARTITION BY id ORDER BY ingested_ts_ms DESC) rn FROM "
            f"read_parquet('{markets_glob}', hive_partitioning=true)) WHERE rn=1),"
            if has_markets else
            "markets AS (SELECT NULL::VARCHAR id, NULL::VARCHAR condition_id, NULL::VARCHAR question),"
        )
        score_cte = (
            f"scores AS (SELECT * EXCLUDE rn FROM (SELECT *, row_number() OVER "
            f"(PARTITION BY market_id ORDER BY ingested_ts_ms DESC) rn FROM "
            f"read_parquet('{scores_glob}', hive_partitioning=true)) WHERE rn=1),"
            if has_scores else
            "scores AS (SELECT NULL::VARCHAR market_id, NULL::DOUBLE final_signal_score, NULL::VARCHAR recommendation),"
        )
        cur = con.execute(
            "WITH "
            "sem AS (SELECT * EXCLUDE rn FROM (SELECT *, row_number() OVER "
            " (PARTITION BY source_market_id ORDER BY ingested_ts_ms DESC) rn "
            f" FROM read_parquet('{sem_glob}', hive_partitioning=true, union_by_name=true)) WHERE rn=1),"
            + market_cte + score_cte[:-1] +
            " SELECT sem.source_market_id AS market_id, "
            " coalesce(markets.condition_id, sem.source_condition_id) AS condition_id, "
            " coalesce(markets.question, sem.question) AS question, "
            " sem.canonical_question, sem.positive_resolution_condition, "
            " sem.negative_resolution_condition, sem.temporal_phrase, sem.temporal_resolution, "
            " sem.exact_deadline_ms, sem.ambiguity_flags, sem.ambiguity_score, "
            " sem.semantic_confidence, sem.needs_manual_review, sem.explanation_summary, "
            " sem.flag_rationales_json, sem.uncertainty_notes_json, sem.rule_curation_notes_json, "
            " scores.final_signal_score AS latest_market_score, scores.recommendation, "
            " sem.ingested_ts_ms AS sem_ingested_ts_ms "
            " FROM sem LEFT JOIN markets ON markets.id = sem.source_market_id "
            " LEFT JOIN scores ON scores.market_id = sem.source_market_id"
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    finally:
        con.close()
