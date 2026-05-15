"""DuckDB dataset loaders over local recorded Parquet data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb


class DatasetMissingError(FileNotFoundError):
    """Raised when a backtest needs local recorded data that does not exist."""


def load_best_quotes(data_root: Path, start_ts_ms=None, end_ts_ms=None, market_ids=None) -> list[dict[str, Any]]:
    rows = _load(data_root, "best_quotes", "timestamp_ms", start_ts_ms, end_ts_ms)
    if market_ids:
        token_map = _token_market_map(data_root)
        wanted = set(market_ids)
        rows = [r for r in rows if token_map.get(str(r.get("token_id"))) in wanted]
    return rows


def load_orderbook_snapshots(data_root: Path, start_ts_ms=None, end_ts_ms=None, market_ids=None) -> list[dict[str, Any]]:
    rows = _load(data_root, "orderbook_snapshots", "timestamp_ms", start_ts_ms, end_ts_ms)
    if market_ids:
        token_map = _token_market_map(data_root)
        wanted = set(market_ids)
        rows = [r for r in rows if token_map.get(str(r.get("token_id"))) in wanted]
    return rows


def load_market_scores(data_root: Path, start_ts_ms=None, end_ts_ms=None, market_ids=None) -> list[dict[str, Any]]:
    rows = _load(data_root, "market_scores", "ingested_ts_ms", start_ts_ms, end_ts_ms)
    if market_ids:
        wanted = set(market_ids)
        rows = [r for r in rows if str(r.get("market_id")) in wanted]
    return rows


def load_markets_latest(data_root: Path) -> list[dict[str, Any]]:
    _require(data_root, "markets")
    glob = data_root / "normalised" / "markets" / "dt=*" / "*.parquet"
    sql = (
        "SELECT * EXCLUDE rn FROM (SELECT *, row_number() OVER "
        "(PARTITION BY id ORDER BY ingested_ts_ms DESC) rn "
        f"FROM read_parquet('{glob}', hive_partitioning=true)) WHERE rn=1"
    )
    return _query(sql)


def load_resolutions(data_root: Path) -> list[dict[str, Any]]:
    return []


def _load(data_root: Path, table: str, ts_col: str, start_ts_ms, end_ts_ms) -> list[dict[str, Any]]:
    _require(data_root, table)
    glob = data_root / "normalised" / table / "dt=*" / "*.parquet"
    where = []
    params = []
    if start_ts_ms is not None:
        where.append(f"{ts_col} >= ?")
        params.append(int(start_ts_ms))
    if end_ts_ms is not None:
        where.append(f"{ts_col} <= ?")
        params.append(int(end_ts_ms))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return _query(f"SELECT * FROM read_parquet('{glob}', hive_partitioning=true){clause}", params)


def _require(data_root: Path, table: str) -> None:
    if not list((data_root / "normalised" / table).glob("dt=*/*.parquet")):
        raise DatasetMissingError(
            f"no local {table} data found; run the recorder or relevant ingest command first"
        )


def _query(sql: str, params: list | None = None) -> list[dict[str, Any]]:
    con = duckdb.connect()
    try:
        cur = con.execute(sql, params or [])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    finally:
        con.close()


def _token_market_map(data_root: Path) -> dict[str, str]:
    try:
        markets = load_markets_latest(data_root)
    except DatasetMissingError:
        return {}
    out: dict[str, str] = {}
    for m in markets:
        for token in m.get("clob_token_ids") or []:
            out[str(token)] = str(m["id"])
    return out
