# Storage Layer

## Principles

1. **Strategies do not see Parquet paths or DuckDB.** They consume
   `*Repository` Protocols. This lets us swap to Postgres later by writing one
   alternate impl and changing one DI registration in `cli.py`.
2. **Append-only.** Every event-table write is a *new file*. We never edit
   existing parquet files. "Current state" is a DuckDB view (`*_latest`).
3. **Schemas are pinned in `storage/parquet/schemas.py`** with a
   `schema_version` column on every row. A schema bump is a code-reviewed PR
   that introduces a new version; old data remains queryable because every
   row carries its own version.
4. **Raw lake separate from normalised lake.** Every API response is dumped
   verbatim to `data/raw/...` *before* any parsing. If our Pydantic models
   miscategorise a field we can re-derive normalised tables.

## Layout

```
data/
├── raw/
│   ├── gamma/{markets,events}/YYYY-MM-DD/page_*.json
│   └── clob/{orderbooks,prices,tick_sizes,...}/YYYY-MM-DD/*.json
├── normalised/
│   ├── markets/dt=YYYY-MM-DD/part-*.parquet           (Phase 1)
│   ├── events/dt=YYYY-MM-DD/part-*.parquet            (Phase 1)
│   ├── orderbook_snapshots/dt=YYYY-MM-DD/part-*.parquet (Phase 2)
│   ├── best_quotes/dt=YYYY-MM-DD/part-*.parquet       (Phase 2)
│   ├── order_events/dt=YYYY-MM-DD/part-*.parquet      (Phase 7+, scaffolded now)
│   ├── fill_events/dt=YYYY-MM-DD/part-*.parquet
│   ├── position_snapshots/dt=YYYY-MM-DD/part-*.parquet
│   └── risk_snapshots/dt=YYYY-MM-DD/part-*.parquet    (Phase 0 — written now)
├── account/   (reserved — same shape, kept separate for future Postgres mirror)
└── derived/   (graph, constraints, opportunities — Phase 4+)
```

## DuckDB usage

`storage/duckdb_engine.py` exposes a per-process `:memory:` connection that
registers Parquet globs as views (`storage/views.py`):

```sql
CREATE VIEW markets_latest AS
SELECT * FROM (
    SELECT *, row_number() OVER (PARTITION BY id ORDER BY ingested_ts_ms DESC) AS rn
    FROM read_parquet('data/normalised/markets/**/*.parquet', hive_partitioning=true)
) WHERE rn = 1;
```

Repositories internally `con.execute("SELECT … FROM markets_latest …")`.
**Strategies never see DuckDB.** They call typed repository methods.

## Append-only writes do not go through DuckDB

We use `pyarrow` directly in `storage/parquet/account_events.py` etc. DuckDB
is read-only here — that prevents long-lived file handles and keeps writes
fully crash-safe (write-to-tmp + atomic rename per part-file).

## Postgres swap path

When we need multi-process live trading or sub-second reconciliation:

1. Add `storage/postgres/` with concrete impls of every `*Repository` Protocol.
2. Add Alembic migrations matching `storage/parquet/schemas.py` row-by-row.
3. Switch the DI registration in `cli.py` from parquet → postgres.
4. Schedule a separate ETL that exports postgres tables to parquet daily so
   the historical lake stays canonical for replay.

No strategy code changes.
