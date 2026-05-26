# Task Change Log - 2026-05-26

This standalone file records the tasks completed today. The same material was
removed from `CLAUDE.md` so that file remains project/runtime context rather
than an implementation change log.

## Task 1 - Fix Compaction Lock Conflict

**File changed:** `deploy/docker-compose.yml`

- Moved `python -m polymarket_arb.cli maintenance compact-lake` to the first
  command inside each `recorder` loop iteration, before `fetch-markets` and
  snapshot writes begin.
- Changed `--older-than-days 1` to `--older-than-days 0`, allowing the
  current UTC day's parquet files to be compacted.
- Kept the existing fetch, snapshot, retention cleanup, and 300-second sleep
  cadence unchanged.

**Resulting recorder order:** compact lake, fetch markets, snapshot candidate
tokens, snapshot active markets, delete expired files, then sleep.

## Task 2 - Scope Bulk Orderbook Reads To Recent Partitions

**Files changed:** `src/polymarket_arb/storage/parquet/orderbook_repo.py`,
`tests/test_storage/test_orderbook_repo.py`

- Added `ParquetOrderbookRepository._glob_recent(days)` to generate a DuckDB
  list literal using only populated UTC date partitions in the requested
  recent window. It checks at most the requested date directories rather than
  scanning accumulated historical orderbook partitions.
- Changed `latest_books_bulk()` to read only the latest two UTC partitions
  (`days=2`) and to return an empty result without executing DuckDB when that
  window has no parquet files.
- Changed `_has_data()` to check only the current UTC partition through
  `_glob_recent(days=1)`.
- Preserved yesterday-only startup behavior: `latest_books_bulk()` can still
  return the most recent book before today's first recorder write.
- Moved the existing retry branch's local `time` import to module scope and
  split its semicolon statement so the edited repository module passes Ruff.
- Added a regression test proving that yesterday's snapshot is returned when
  today's partition is empty, and updated the old-partition test description
  to reflect the two-day read window.

**Verification at completion:**

- `python -m pytest tests/test_storage/test_orderbook_repo.py -q`: passed
  (`5 passed`).
- `python -m pytest tests/ -q`: passed (`825 passed`).
- `python -m ruff check src/polymarket_arb/storage/parquet/orderbook_repo.py tests/test_storage/test_orderbook_repo.py`:
  passed.
- `python -m ruff check src/ tests/`: failed on four unrelated existing
  findings in `tests/test_limitless/test_arb_scanner.py` and
  `tests/test_storage/test_schemas.py`; the former is addressed while
  implementing Task 3 below.

## Task 3 - Fix Arb Status Tolerance Hardcode

**Files changed:** `src/polymarket_arb/limitless/arb_scanner.py`,
`tests/test_limitless/test_arb_scanner.py`

**File reviewed, no edit required:** `src/polymarket_arb/cli/limitless.py`

- Removed `_arb_status(arb_gap, tolerance=0.02)` from `match_markets()`.
  Matches now carry their computed `arb_gap` with `status=""`, so matching
  does not classify results using a hidden fixed tolerance.
- Updated the `match_markets()` docstring to state that callers must run
  `compute_arb()` with their configured tolerance before display or
  execution.
- Confirmed the CLI scan path already performs the required classification:
  immediately after `match_markets(...)`, it calls
  `compute_arb(matches, tolerance=tolerance)`, using the value received from
  `--tolerance`. No CLI source edit was necessary.
- Updated the tolerance regression test to assert that matching initially
  leaves status unset, then verifies that `compute_arb()` classifies the same
  `0.04` gap as `ARB_OPPORTUNITY` with tolerance `0.02` and `EFFICIENT` with
  tolerance `0.05`.
- Cleaned existing Ruff findings in the touched Limitless test file by
  changing its docstring multiplication sign to ASCII `x` and removing two
  unused model imports.

**Verification after Task 3:**

- `python -m pytest tests/test_limitless/test_arb_scanner.py tests/test_storage/test_orderbook_repo.py -q`:
  passed (`20 passed`).
- `python -m pytest tests/ -q`: passed (`825 passed`).
- `python -m ruff check src/polymarket_arb/limitless/arb_scanner.py src/polymarket_arb/cli/limitless.py tests/test_limitless/test_arb_scanner.py src/polymarket_arb/storage/parquet/orderbook_repo.py tests/test_storage/test_orderbook_repo.py`:
  passed.
- `python -m ruff check src/ tests/`: fails on one remaining unrelated
  existing import-order finding in `tests/test_storage/test_schemas.py`,
  which was not changed by these tasks.

## Task 4 - Position Tracking Table

**Files changed:** `src/polymarket_arb/live/models.py`,
`src/polymarket_arb/live/order_client.py`,
`src/polymarket_arb/storage/parquet/schemas.py`,
`src/polymarket_arb/storage/views.py`,
`src/polymarket_arb/limitless/arb_scanner.py`

**File added:** `src/polymarket_arb/storage/parquet/positions_repo.py`

- Added the frozen `PositionRow` model with position identity, entry fields,
  source metadata, open/closed state fields, realised PnL fields, and schema
  version.
- Added a pinned `POSITIONS_SCHEMA_V1` and registered the `positions` parquet
  table in `ALL_SCHEMAS`.
- Added `positions_all` and `open_positions_latest` DuckDB view definitions,
  including latest-state selection by `position_id`, so a future closed
  record supersedes its open record.
- Added `ParquetPositionsRepository`, writing `PositionRow` records beneath
  `data/normalised/positions/dt=YYYY-MM-DD/*.parquet` and providing a recent
  iterator matching the existing audit repository style.
- Added optional `positions_repo` injection to `OrderClient.__init__`, with a
  parquet repository constructed from `settings.data_root` by default.
- Changed `OrderClient._record_and_return()` so an order first writes its
  `OrdersLogRow`; only when that write succeeds and the status is
  `paper_filled` does it append an open `PositionRow`.
- Generated each `position_id` as SHA-256 over the specified concatenation of
  `market_id`, `token_id`, `strategy_id`, and `open_ts_ms`.
- Preserved order-result behavior if either audit or position persistence
  fails: failures are logged and do not turn a simulated result into a failed
  order response.
- Changed the Polymarket leg note emitted by the Limitless executor from
  `gap=...` to `arb_gap=...`, so new Limitless positions carry the metadata
  required for locked-profit calculation.

## Task 5 - MTM PnL Calculation

**File changed:** `src/polymarket_arb/dashboard/queries.py`

- Added `DuckDBQueryService.open_positions_with_mtm()`.
- The query returns no rows if either the positions lake or orderbook lake has
  no data.
- It reads position records from the last 30 days and selects the latest open
  state per `position_id`.
- It reads orderbook snapshots from the latest day, selects the most recent
  two-sided book per token, and computes current midpoint as
  `(best_bid + best_ask) / 2`.
- It returns market, strategy, side, entry price, size, cost basis, current
  midpoint, MTM PnL, open timestamp, and notes for each open position.
- It computes MTM PnL as
  `(current_mid - CAST(entry_price AS DOUBLE)) * CAST(size AS DOUBLE)`.
- It computes `locked_profit` for `limitless_arb` positions by parsing
  `arb_gap=` from notes with the existing regular-expression convention and
  multiplying by position size.
- It uses a left join to retain open positions whose token has not yet
  received a usable two-sided current mark; those rows expose null midpoint
  and MTM values rather than disappearing.

## Task 6 - Positions Dashboard Page

**Files changed:** `src/polymarket_arb/dashboard/routes.py`,
`src/polymarket_arb/dashboard/templates/base.html`

**File added:** `src/polymarket_arb/dashboard/templates/positions.html`

- Added the `/positions` route, which loads marked open positions and
  calculates display totals for count, cost basis, MTM PnL, and Limitless
  locked profit.
- Added a Positions navigation link and active-page styling in the shared
  dashboard navigation.
- Extended the existing meta-refresh behavior so `/positions` refreshes every
  30 seconds, matching the overview refresh cadence.
- Added an open-positions page with summary cards, a full position table,
  positive/negative MTM color styling, Limitless locked-profit display, and a
  `No open positions.` state.

## Tasks 4-6 Test And Validation Changes

**Files changed:** `tests/test_live/test_order_client.py`,
`tests/test_dashboard/test_app.py`, `tests/test_storage/test_schemas.py`

**File added:** `tests/test_storage/test_positions_repo.py`

- Added repository round-trip coverage for appending and reading positions.
- Extended the paper-filled order-client test to verify deterministic
  `position_id`, position entry values, provenance, notes, and open status.
- Extended the no-book order-client test to verify that unfilled attempts do
  not create positions.
- Added `/positions` to empty-lake route rendering coverage.
- Added a seeded positions dashboard test validating midpoint `0.50`, MTM PnL
  `1.00`, locked profit `0.25`, page rendering, and 30-second refresh output.
- Reformatted an existing long import in `tests/test_storage/test_schemas.py`;
  this resolves the last pre-existing full-Ruff blocker encountered during
  the earlier task verification.

**Verification after Tasks 4-6:**

- `python -m pytest tests/test_storage/test_positions_repo.py tests/test_live/test_order_client.py tests/test_dashboard/test_app.py tests/test_storage/test_views_completeness.py tests/test_storage/test_orderbook_repo.py tests/test_limitless/test_arb_scanner.py -q`:
  passed (`40 passed`).
- `python -m pytest tests/ -q`: passed (`828 passed`).
- `python -m ruff check src/ tests/`: passed.
- `git diff --check`: passed.

## Task 7 - Fix PnL Chart To Show Expected Return

**Files changed:** `src/polymarket_arb/dashboard/queries.py`,
`src/polymarket_arb/dashboard/cache.py`,
`src/polymarket_arb/dashboard/routes.py`,
`src/polymarket_arb/dashboard/templates/overview.html`

**Supporting files changed:** `src/polymarket_arb/risk/models.py`,
`src/polymarket_arb/cli/live.py`, `src/polymarket_arb/live/order_client.py`

- Replaced `cumulative_pnl_by_hour()`, which previously plotted filled
  notional as though it were profit, with
  `cumulative_expected_return_by_hour()`.
- The new query reads filled paper orders over the same seven-day display
  window and calculates per-order expected PnL:
  - `relationship_*` strategies use `gross_edge` parsed from
    `detail_json`, multiplied by `notional_usdc`.
  - `limitless_arb` uses `arb_gap` parsed from `notes`, multiplied by
    `notional_usdc`.
  - Filled orders without one of those recognised edge sources contribute
    zero expected PnL while remaining in cost basis.
- The new query returns hourly cumulative expected PnL series data, a
  zero-reference series value, `total_expected_pnl`, `total_cost_basis`, and
  `expected_return_pct`.
- Changed the dashboard cache and overview route from `cumulative_pnl` to the
  new `expected_return` data contract.
- Updated the overview chart heading and series:
  - Green line: cumulative expected PnL in USDC.
  - Grey dashed line: break-even reference at zero.
- Added an Expected Return card showing
  `total_expected_pnl / total_cost_basis * 100`, plus its PnL and deployed
  cost-basis context.
- During implementation, verified that the existing live relationship order
  path did not yet write `gross_edge` into `detail_json`. To make the chart
  calculate real values for new relationship fills:
  - Added an optional `detail` metadata dictionary to `OrderIntent`.
  - Added `gross_edge` to both relationship-leg intents emitted by
    `cli/live.py`.
  - Merged intent metadata into the `OrdersLogRow.detail_json` written by
    `OrderClient`, alongside execution details such as book timestamp.

## Task 8 - Recorder Healthcheck Fix

**File changed:** `deploy/docker-compose.yml`

- Added a recorder-specific `healthcheck` override so the recorder no longer
  inherits the image-level live-agent/DuckDB healthcheck.
- Configured the requested timings: `interval: 120s`, `timeout: 10s`,
  `retries: 3`, and `start_period: 120s`.
- The check now verifies that the current UTC orderbook-snapshot partition
  contains at least one parquet output file, directly measuring whether the
  recorder has produced data today.
- Implemented the file check using `find ... -print -quit` rather than
  `test -f .../*.parquet`: the supplied wildcard form fails when more than one
  healthy parquet file exists in the partition.
- Escaped shell substitutions with `$$` so Docker Compose passes the
  `find`/`date -u` command substitutions through for execution in the
  container.

## Tasks 7-8 Test Changes

**Files changed:** `tests/test_dashboard/test_app.py`,
`tests/test_live/test_order_client.py`,
`tests/test_live/test_relationship_strategies.py`,
`tests/test_deploy/test_deploy_artifacts.py`

- Added dashboard coverage proving expected-return calculations across both
  supported sources: a relationship fill with `0.05 * 100` expected PnL and
  a Limitless fill with `0.02 * 50`, yielding `6.00` USDC expected PnL on
  `150.00` USDC cost basis and a rendered `+4.00%` Expected Return.
- Verified the overview renders the cumulative expected PnL chart and its
  break-even reference line.
- Extended OrderClient coverage to prove intent `gross_edge` metadata is
  persisted into `detail_json` for filled orders.
- Extended relationship strategy coverage to prove both generated legs carry
  the candidate `gross_edge` detail metadata.
- Extended deployment artifact coverage to verify the recorder's healthcheck
  uses orderbook output and UTC date selection rather than
  `agent-healthcheck`, with the configured health timing settings.

**Verification after Tasks 7-8:**

- `python -m pytest tests/test_dashboard/test_app.py tests/test_live/test_order_client.py tests/test_live/test_relationship_strategies.py tests/test_deploy/test_deploy_artifacts.py -q`:
  passed (`34 passed`).
- `python -m pytest tests/ -q`: passed (`829 passed`).
- `python -m ruff check src/ tests/`: passed.
- `git diff --check`: passed.

## Deployment Priority Order

1. Task 1 - compaction conflict fix; deploy immediately to address disk
   growth risk.
2. Task 8 - recorder healthcheck override; deploy alongside Task 1.
3. Task 2 - recent-partition orderbook reads; deploy separately to reduce
   agent read cost as the lake grows.
4. Task 3 - tolerance classification fix; deploy separately as a focused
   behavior correction.
5. Tasks 4, 5, and 6 - position storage, MTM query, and positions dashboard;
   deploy together after full test validation.
6. Task 7 - expected-return overview chart; deploy after position-related
   data collection is available.
