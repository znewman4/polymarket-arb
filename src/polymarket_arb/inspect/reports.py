"""Read-only DuckDB-backed inspection reports."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

import duckdb

from ..storage.parquet.markets_repo import ParquetMarketsRepository
from .models import PipelineStage, TableStatus

TABLES: tuple[str, ...] = (
    "markets",
    "events",
    "market_semantics",
    "market_embeddings",
    "rulebook_evaluations",
    "market_implications",
    "orderbook_snapshots",
    "best_quotes",
    "market_scores",
    "risk_snapshots",
)


def _table_dir(data_root: Path, table: str) -> Path:
    return data_root / "normalised" / table


def _glob(data_root: Path, table: str) -> str:
    return str(_table_dir(data_root, table) / "dt=*" / "*.parquet")


def _files(data_root: Path, table: str) -> list[Path]:
    return sorted(_table_dir(data_root, table).glob("dt=*/*.parquet"))


def _query(data_root: Path, table: str, sql: str, params: list[Any] | None = None) -> list[dict]:
    if not _files(data_root, table):
        return []
    con = duckdb.connect()
    try:
        cur = con.execute(sql.format(glob=_glob(data_root, table)), params or [])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    finally:
        con.close()


def _scalar(data_root: Path, table: str, sql: str, default: Any = None) -> Any:
    rows = _query(data_root, table, sql)
    if not rows:
        return default
    return next(iter(rows[0].values()), default)


def table_report(data_root: Path) -> list[TableStatus]:
    out: list[TableStatus] = []
    for table in TABLES:
        files = _files(data_root, table)
        row_count = None
        latest = None
        if files:
            row_count = int(_scalar(
                data_root, table,
                "SELECT count(*) AS c FROM read_parquet('{glob}', hive_partitioning=true)",
                0,
            ))
            latest = _latest_ts(data_root, table)
        out.append(TableStatus(table, _table_dir(data_root, table), len(files), row_count, latest))
    return out


def counts_report(data_root: Path) -> dict[str, Any]:
    latest_market_sql = (
        "WITH latest AS ("
        " SELECT *, row_number() OVER (PARTITION BY id ORDER BY ingested_ts_ms DESC) AS rn"
        " FROM read_parquet('{glob}', hive_partitioning=true)"
        ") SELECT * FROM latest WHERE rn = 1"
    )
    markets = _query(data_root, "markets", latest_market_sql)
    market_ids = {m["id"] for m in markets}
    active = [
        m for m in markets
        if m.get("active") is True and m.get("closed") is False and m.get("archived") is not True
    ]
    sem_ids = set(_ids(data_root, "market_semantics", "source_market_id"))
    scored_ids = set(_ids(data_root, "rulebook_evaluations", "market_id"))
    impl_ids = set(_ids(data_root, "market_implications", "market_id"))
    score_ids = set(_ids(data_root, "market_scores", "market_id"))
    quote_tokens = set(_ids(data_root, "best_quotes", "token_id"))
    markets_with_quote = {
        m["id"] for m in markets
        if any(str(t) in quote_tokens for t in (m.get("clob_token_ids") or []))
    }
    rows_by_table = {s.name: s.row_count or 0 for s in table_report(data_root)}
    latest_by_table = {s.name: s.latest_ingested_ts_ms for s in table_report(data_root)}
    return {
        "total_markets": len(markets),
        "active_markets": len(active),
        "closed_markets": sum(1 for m in markets if m.get("closed") is True),
        "markets_missing_token_ids": sum(1 for m in markets if not m.get("clob_token_ids")),
        "markets_missing_end_dates": sum(1 for m in markets if m.get("end_date_ms") is None),
        "markets_with_malformed_outcomes": sum(
            1 for m in markets
            if len(m.get("outcomes") or []) != len(m.get("gamma_outcome_prices_snapshot") or [])
        ),
        "markets_with_no_semantics": len(market_ids - sem_ids),
        "markets_with_no_rulebook_score": len(market_ids - scored_ids),
        "markets_with_no_implications": len(market_ids - impl_ids),
        "markets_with_no_clob_quote": len(market_ids - markets_with_quote),
        "markets_with_no_market_score": len(market_ids - score_ids),
        "latest_ingestion_ts_ms_by_table": latest_by_table,
        "rows_by_table": rows_by_table,
    }


def market_report(data_root: Path, market_id: str) -> dict[str, Any]:
    market = ParquetMarketsRepository(data_root).get_market(market_id)
    if market is None:
        return {"market_id": market_id, "present": False}
    sem = _latest_by(data_root, "market_semantics", "source_market_id", market_id)
    eval_row = _latest_by(data_root, "rulebook_evaluations", "market_id", market_id)
    implications = _query(
        data_root, "market_implications",
        "SELECT * FROM read_parquet('{glob}', hive_partitioning=true) "
        "WHERE market_id = ? ORDER BY ingested_ts_ms DESC LIMIT 20",
        [market_id],
    )
    quotes = []
    for token in market.clob_token_ids:
        q = _latest_by(data_root, "best_quotes", "token_id", token)
        if q:
            quotes.append(q)
    score = _latest_by(data_root, "market_scores", "market_id", market_id)
    return {
        "market_id": market.id,
        "present": True,
        "gamma": {
            "condition_id": market.condition_id,
            "slug": market.slug,
            "question": market.question,
            "outcomes": market.outcomes,
            "token_ids": market.clob_token_ids,
            "active": market.active,
            "closed": market.closed,
            "archived": market.archived,
            "end_date_ms": market.end_date_ms,
            "text_hash": market.text_hash,
            "ingested_ts_ms": market.ingested_ts_ms,
        },
        "semantics": _sem_summary(sem),
        "rulebook_evaluation": _eval_summary(eval_row),
        "implications": {
            "count": len(implications),
            "types": sorted({str(i.get("implication_type")) for i in implications}),
            "latest_ingested_ts_ms": max((i.get("ingested_ts_ms") or 0 for i in implications), default=None),
        },
        "best_quotes": quotes,
        "market_score": score,
    }


def market_pipeline_report(data_root: Path, market_id: str) -> list[PipelineStage]:
    report = market_report(data_root, market_id)
    market_present = bool(report.get("present"))
    gamma_ts = report.get("gamma", {}).get("ingested_ts_ms") if market_present else None
    stages = [
        PipelineStage(
            "Gamma market",
            market_present,
            gamma_ts,
            {"question": report.get("gamma", {}).get("question")},
            "polymarket-arb gamma fetch-markets --limit 50" if not market_present else None,
        )
    ]
    sem = report.get("semantics") or {}
    stages.append(PipelineStage(
        "Semantic extraction",
        bool(sem),
        sem.get("ingested_ts_ms"),
        sem,
        "polymarket-arb nlp extract-market-semantics --limit 10" if not sem else None,
    ))
    ev = report.get("rulebook_evaluation") or {}
    stages.append(PipelineStage(
        "Rulebook evaluation",
        bool(ev),
        ev.get("evaluated_ts_ms"),
        ev,
        "polymarket-arb nlp score-semantics --limit 10" if not ev else None,
    ))
    impl = report.get("implications") or {}
    stages.append(PipelineStage(
        "Implications",
        bool(impl.get("count")),
        impl.get("latest_ingested_ts_ms"),
        impl,
        "polymarket-arb nlp extract-implications --limit 10" if not impl.get("count") else None,
    ))
    quotes = report.get("best_quotes") or []
    stages.append(PipelineStage(
        "CLOB quotes/orderbooks",
        bool(quotes),
        max((q.get("ingested_ts_ms") or 0 for q in quotes), default=None),
        {"quote_count": len(quotes)},
        f"polymarket-arb clob fetch-orderbook {market_id}" if not quotes else None,
    ))
    score = report.get("market_score") or {}
    stages.append(PipelineStage(
        "Fusion score",
        bool(score),
        score.get("ingested_ts_ms"),
        {
            "final_signal_score": score.get("final_signal_score"),
            "recommendation": score.get("recommendation"),
        },
        "polymarket-arb score score-markets --limit 10" if not score else None,
    ))
    return stages


def freshness_report(data_root: Path, *, stale_quote_ms: int = 60_000) -> dict[str, Any]:
    latest = {s.name: s.latest_ingested_ts_ms for s in table_report(data_root)}
    quotes = _query(
        data_root, "best_quotes",
        "SELECT * FROM read_parquet('{glob}', hive_partitioning=true)",
    )
    now = max((q.get("ingested_ts_ms") or 0 for q in quotes), default=0)
    ages = [max(0, now - int(q.get("timestamp_ms") or q.get("ingested_ts_ms") or now)) for q in quotes]
    markets = _query(
        data_root, "markets",
        "WITH latest AS (SELECT *, row_number() OVER (PARTITION BY id ORDER BY ingested_ts_ms DESC) AS rn "
        "FROM read_parquet('{glob}', hive_partitioning=true)) SELECT * FROM latest WHERE rn = 1",
    )
    sem = {r["source_market_id"]: r for r in _query(
        data_root, "market_semantics",
        "WITH latest AS (SELECT *, row_number() OVER (PARTITION BY source_market_id ORDER BY ingested_ts_ms DESC) AS rn "
        "FROM read_parquet('{glob}', hive_partitioning=true, union_by_name=true)) SELECT * FROM latest WHERE rn = 1",
    )}
    scores = {r["market_id"]: r for r in _query(
        data_root, "market_scores",
        "WITH latest AS (SELECT *, row_number() OVER (PARTITION BY market_id ORDER BY ingested_ts_ms DESC) AS rn "
        "FROM read_parquet('{glob}', hive_partitioning=true)) SELECT * FROM latest WHERE rn = 1",
    )}
    text_mismatches = sum(
        1 for m in markets
        if (s := sem.get(m["id"])) is not None and m.get("question") != s.get("question")
    )
    closed_since_score = sum(
        1 for m in markets
        if (m.get("closed") or m.get("resolved_at_ms")) and (sc := scores.get(m["id"])) is not None
        and int(m.get("ingested_ts_ms") or 0) > int(sc.get("ingested_ts_ms") or 0)
    )
    return {
        "latest_ingestion_ts_ms_by_table": latest,
        "quote_age_ms": _dist(ages),
        "stale_quotes": sum(1 for age in ages if age > stale_quote_ms),
        "text_hash_changed_after_semantics": text_mismatches,
        "markets_closed_or_resolved_since_last_score": closed_since_score,
    }


def score_distribution_report(data_root: Path, *, top_n: int = 10) -> dict[str, Any]:
    scores = _query(
        data_root, "market_scores",
        "WITH latest AS (SELECT *, row_number() OVER (PARTITION BY market_id ORDER BY ingested_ts_ms DESC) AS rn "
        "FROM read_parquet('{glob}', hive_partitioning=true)) SELECT * FROM latest WHERE rn = 1",
    )
    values = [float(s["final_signal_score"]) for s in scores if s.get("final_signal_score") is not None]
    markets = {m["id"]: m for m in _query(
        data_root, "markets",
        "WITH latest AS (SELECT *, row_number() OVER (PARTITION BY id ORDER BY ingested_ts_ms DESC) AS rn "
        "FROM read_parquet('{glob}', hive_partitioning=true)) SELECT * FROM latest WHERE rn = 1",
    )}
    buckets: dict[str, int] = {"ignore": 0, "watch": 0, "research": 0, "paper_signal_only": 0}
    for row in scores:
        rec = str(row.get("recommendation"))
        buckets[rec] = buckets.get(rec, 0) + 1
    top = sorted(scores, key=lambda r: float(r.get("final_signal_score") or 0), reverse=True)[:top_n]
    high_amb = sorted(scores, key=lambda r: float(r.get("ambiguity_score") or 0), reverse=True)[:top_n]
    low_liq = sorted(scores, key=lambda r: float(r.get("liquidity_score") or 0))[:top_n]
    return {
        "count": len(scores),
        "final_signal_score": _dist(values),
        "recommendation_counts": buckets,
        "top_scores": [_score_row(r, markets) for r in top],
        "highest_ambiguity": [_score_row(r, markets) for r in high_amb],
        "lowest_liquidity": [_score_row(r, markets) for r in low_liq],
        "stale_quote_affected_scores": sum(1 for r in scores if float(r.get("freshness_score") or 0) < 0.5),
    }


def _latest_ts(data_root: Path, table: str) -> int | None:
    rows = _query(
        data_root, table,
        "SELECT max(ingested_ts_ms) AS ts FROM read_parquet('{glob}', hive_partitioning=true)",
    )
    return None if not rows or rows[0]["ts"] is None else int(rows[0]["ts"])


def _ids(data_root: Path, table: str, col: str) -> list[str]:
    return [str(r[col]) for r in _query(
        data_root, table,
        f"SELECT DISTINCT {col} FROM read_parquet('{{glob}}', hive_partitioning=true)",
    ) if r.get(col) is not None]


def _latest_by(data_root: Path, table: str, col: str, value: str) -> dict[str, Any] | None:
    rows = _query(
        data_root, table,
        f"SELECT * FROM read_parquet('{{glob}}', hive_partitioning=true, union_by_name=true) "
        f"WHERE {col} = ? ORDER BY ingested_ts_ms DESC LIMIT 1",
        [value],
    )
    if not rows:
        return None
    rows[0].pop("dt", None)
    return rows[0]


def _sem_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "canonical_question": row.get("canonical_question"),
        "positive_resolution_condition": row.get("positive_resolution_condition"),
        "negative_resolution_condition": row.get("negative_resolution_condition"),
        "temporal_resolution": row.get("temporal_resolution"),
        "ambiguity_flags": row.get("ambiguity_flags") or [],
        "ambiguity_score": row.get("ambiguity_score"),
        "semantic_confidence": row.get("semantic_confidence"),
        "needs_manual_review": row.get("needs_manual_review"),
        "model_name": row.get("model_name"),
        "prompt_version": row.get("prompt_version"),
        "ingested_ts_ms": row.get("ingested_ts_ms"),
        "extraction_id": row.get("extraction_id"),
    }


def _eval_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "rulebook_id": row.get("rulebook_id"),
        "rulebook_version": row.get("rulebook_version"),
        "score": row.get("score"),
        "flags": row.get("flags") or [],
        "evaluated_ts_ms": row.get("evaluated_ts_ms"),
    }


def _dist(values: list[int] | list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "max": None, "mean": None, "median": None}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
    }


def _score_row(row: dict[str, Any], markets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    mid = str(row.get("market_id") or "")
    return {
        "market_id": mid,
        "question": (markets.get(mid) or {}).get("question"),
        "final_signal_score": row.get("final_signal_score"),
        "recommendation": row.get("recommendation"),
        "ambiguity_score": row.get("ambiguity_score"),
        "liquidity_score": row.get("liquidity_score"),
        "freshness_score": row.get("freshness_score"),
    }


def pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)
