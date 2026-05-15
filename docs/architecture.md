# Architecture (Phase 0)

```
                 ┌─────────────────────────────────────────────┐
   CLI ──▶  cli.py ──▶  settings.py  ──▶  logging_setup.py    │
                 │                                             │
                 ├──▶ compliance.geo_check  (egress IP+country)│
                 ├──▶ monitoring.kill_switch (file/signal)     │
                 ├──▶ http.client (async, retry, rate-limited) │
                 ├──▶ storage.* (Parquet + DuckDB)             │
                 └──▶ risk.preflight  ──▶ checks/*.py          │
                                            └──▶ writes RiskSnapshot
```

Phase 0 wires every module together but only `healthcheck` exercises the full
chain. Phase 1 adds `ingest/gamma.py` and concrete `MarketsRepository` and
`EventsRepository` impls. Phase 2 adds `ingest/clob_rest.py` and the
`OrderbookRepository` impl plus pure `basket_pricing.py`.

## Data flow (Phases 1–2 preview, not yet implemented)

```
Public Polymarket APIs
    │ (httpx async, retry, rate-limited)
    ▼
ingest/gamma.py, ingest/clob_rest.py
    │ writes raw payload first (storage.parquet.raw_writer)
    │ then validates with Pydantic (parsing/*)
    ▼
storage interface (MarketsRepository / OrderbookRepository / …)
    │ chooses the parquet impl behind the Protocol
    ▼
data/normalised/<table>/dt=YYYY-MM-DD/part-*.parquet  (append-only)
    ▲
    │ DuckDB views (storage.views) read everything as logical tables
    │
Strategies / CLI queries (Phase 4+) — never touch parquet paths directly
```

## Append-only event tables

`order_events`, `fill_events`, `position_snapshots`, `risk_snapshots` follow
the same pattern: every change is a new row keyed by `event_id` (uuid7). The
"current state" is a DuckDB view (`risk_snapshots_latest`,
`positions_latest`) using `qualify row_number() over (... order by ts desc) = 1`.

This means crash-safety is trivial (we never overwrite) and a future Postgres
swap only changes which `*Repository` Protocol impl is registered in
`cli.py`'s DI container.
