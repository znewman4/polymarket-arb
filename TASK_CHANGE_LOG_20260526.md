# Task Change Log - 2026-05-26

This document records the work completed for the live bot and dashboard update.
`CLAUDE.md` was read for operating context and was not edited.

## Deployment Group 1 - Data Quality Fixes

### Bug 1 - Persist relationship order market identity

Changed files:

- `src/polymarket_arb/risk/models.py`
  - Added `OrderIntent.market_id`, defaulting to `""` for backward compatibility.
- `src/polymarket_arb/cli/live.py`
  - Relationship leg A now carries `rel.market_id_a`; leg B carries `rel.market_id_b`.
  - Both legs now retain `gross_edge`, `relationship_id`, and `relationship_type`
    in `intent.detail`.
- `src/polymarket_arb/live/agent_loop.py`
  - `place_order()` now receives the intent strategy and market ID.
  - It also writes `source_relationship_id` and notes in the form
    `gross_edge=<value> rel_type=<value>`.
- `tests/test_live/test_relationship_strategies.py`
  - Added assertions for each emitted leg's market and relationship metadata.
- `tests/test_live/test_agent_loop.py`
  - Added regression checks that filled order rows retain market ID, relationship
    ID, and gross-edge notes through the loop-to-client boundary.

Result: new relationship-strategy `orders_log` rows can be attributed to their
market and source relationship, and contain edge text needed by dashboard estimates.

### Bug 3 - Apply configured Limitless tolerance

Changed files:

- `src/polymarket_arb/limitless/arb_scanner.py`
  - `match_markets()` now assigns `status="PENDING"` rather than classifying a
    gap before the configured tolerance is available.
- `src/polymarket_arb/limitless/models.py`
  - Clarified that `ArbMatch.status` is classified by `compute_arb()`.
- `tests/test_limitless/test_arb_scanner.py`
  - Updated the regression assertion for the pre-computation status.

Verified existing behavior:

- `src/polymarket_arb/cli/limitless.py` already calls
  `compute_arb(matches, tolerance=tolerance)` immediately after matching and
  before displaying or executing results, so no code edit was required there.

Verification after this group:

- `python -m pytest tests/ -q`: `829 passed`
- `python -m ruff check src/ tests/`: passed

## Deployment Group 2 - Recorder And Read-Path Fixes

### Bugs 4 and 5 - Compaction ordering and recorder health

Changed files:

- `deploy/docker-compose.yml`
  - The recorder compaction command now uses
    `--older-than-days 0 --min-files 5`.
  - Compaction remains at the start of the recorder loop, ahead of market
    fetching and snapshot writes.
- `tests/test_deploy/test_deploy_artifacts.py`
  - Added assertions that the compaction command uses the requested arguments
    and runs before `fetch-markets`.

Verified existing behavior:

- The recorder service already overrides the agent healthcheck with a direct
  current-day orderbook snapshot check using UTC date partition lookup. That
  implementation was retained because it checks the intended recorder output
  directly.

### Bug 6 - Limit agent orderbook scans to recent partitions

Changed files:

- `src/polymarket_arb/storage/parquet/orderbook_repo.py`
  - Added `_glob()` for the historical lake wildcard.
  - `_glob_recent(days=2)` now emits a single quoted DuckDB glob or a list
    literal for populated recent UTC partitions.
  - If no recent partition exists, it falls back to the full wildcard as
    specified, preserving access to the last available historical snapshot.
  - `_has_data()` now checks only today's partition without querying the full lake.
  - Empty fallback lakes now return `{}` without raising a DuckDB no-files error.
- `tests/test_storage/test_orderbook_repo.py`
  - Verified the previous-day recent read and the no-recent historical fallback.
  - Verified `_has_data()` stays false when only stale data is present.

Verification after this group:

- `python -m pytest tests/ -q`: `829 passed`
- `python -m ruff check src/ tests/`: passed

## Deployment Group 3 - Dashboard Notional Correction

### Bug 2 - Stop labelling deployed capital as PnL

Changed files:

- `src/polymarket_arb/dashboard/queries.py`
  - Replaced `cumulative_expected_return_by_hour()` with
    `cumulative_notional_by_hour()` for the overview capital-deployment chart.
  - Renamed tradebook aggregate output from `total_pnl` to `total_notional`.
  - Renamed tradebook row and CSV window output from `running_pnl` to
    `cumulative_notional`.
- `src/polymarket_arb/dashboard/cache.py`
  - Replaced the interim expected-return chart cache entry with
    `cumulative_notional`.
- `src/polymarket_arb/dashboard/routes.py`
  - Passes `cumulative_notional` to the overview template.
- `src/polymarket_arb/dashboard/templates/tradebook.html`
  - Renamed total and cumulative columns to explicitly say notional.
  - Renamed the single-trade card to `Largest single trade`.
- `src/polymarket_arb/dashboard/templates/overview.html`
  - The chart is now titled `Cumulative notional deployed (USDC)`.
  - Added the warning that deployed capital is not profit and true PnL
    requires resolution.
- `tests/test_dashboard/test_app.py`
  - Added checks for the corrected data alias and dashboard labels.

Verification after this group:

- `python -m pytest tests/ -q`: `829 passed`
- `python -m ruff check src/ tests/`: passed

## Deployment Group 4 - Position Tracking Contract

### Feature 1 - Open position records on paper fills

Changed files:

- `src/polymarket_arb/live/models.py`
  - Finalized `PositionRow` with `gross_edge`, `relationship_id`,
    `relationship_type`, and `ingested_ts_ms`.
  - Removed interim close-state fields from the write-time open-position row.
- `src/polymarket_arb/live/order_client.py`
  - A successful `paper_filled` row with a fill price writes an open position.
  - Position IDs now use the required first 16 hex characters of the SHA-256
    hash over market, token, strategy, and timestamp.
  - Position records receive the intent's gross-edge and relationship metadata.
- `src/polymarket_arb/storage/parquet/schemas.py`
  - Updated the `positions` parquet schema to the finalized `PositionRow` fields.
- `src/polymarket_arb/storage/parquet/positions_repo.py`
  - Reads position parts with `union_by_name=true` and supplies defaults for
    interim rows, so an existing pre-final-schema lake remains readable.
- `src/polymarket_arb/storage/views.py`
  - Updated `open_positions_latest` ordering and mixed-schema reading for the
    final ingestion timestamp contract.
- `tests/test_live/test_order_client.py`
  - Verifies position ID generation and stored edge/relationship metadata.
- `tests/test_storage/test_positions_repo.py`
  - Verifies persistence of the final row shape.

Verified existing behavior:

- `src/polymarket_arb/storage/parquet/positions_repo.py` already provided the
  append-only `normalised/positions/dt=YYYY-MM-DD` repository entry point and
  was refined for the final schema rather than recreated.

Verification after this group:

- `python -m pytest tests/ -q`: `829 passed`
- `python -m ruff check src/ tests/`: passed

## Deployment Group 5 - MTM Positions Dashboard

### Features 2 and 3 - MTM query and open positions page

Changed files:

- `src/polymarket_arb/dashboard/queries.py`
  - `open_positions_with_mtm()` now returns the full position metadata contract
    plus `current_mid`, `mtm_pnl`, and Limitless-only `locked_profit`.
  - Midpoint calculation uses the actual snapshot schema's best bid and ask
    list entries.
  - The positions read handles interim and final parquet column sets together.
- `src/polymarket_arb/dashboard/cache.py`
  - Added the `open_positions` cached query entry.
- `src/polymarket_arb/dashboard/routes.py`
  - `/positions` now renders cached position data and calculates the four
    specified summary values.
- `src/polymarket_arb/dashboard/templates/positions.html`
  - Added the required token column, finalized summary labels, MTM colors,
    and explanatory empty state.
- `tests/test_dashboard/test_app.py`
  - Verifies midpoint MTM, locked profit, cached page rendering, and token display.

Verified existing behavior:

- The `/positions` route, page template, base navigation link, and 30-second
  auto-refresh hook were already present and were aligned to the final fields
  and summary contract.

Verification after this group:

- `python -m pytest tests/ -q`: `829 passed`
- `python -m ruff check src/ tests/`: passed

## Deployment Group 6 - Expected PnL Overview Cards

### Feature 4 - Expected return from recorded gross edge

Changed files:

- `src/polymarket_arb/dashboard/queries.py`
  - Added `expected_pnl_stats()`, which parses `gross_edge=` from filled-order
    notes, computes expected PnL, tracks filled cost basis, and returns
    expected return percentage.
  - Malformed edge note values are ignored without breaking cache refresh.
- `src/polymarket_arb/dashboard/cache.py`
  - Added the `expected_pnl` cached statistic.
- `src/polymarket_arb/dashboard/routes.py`
  - Passes expected PnL summary values to the overview page.
- `src/polymarket_arb/dashboard/templates/overview.html`
  - Added `Expected PnL` and `Expected Return` cards with the deployed-capital
    sub-label and post-deploy metadata note.
- `tests/test_dashboard/test_app.py`
  - Verifies expected PnL and return calculations and rendered card text.

Verification after this group and final compatibility/test hardening:

- `python -m pytest tests/ -q`: `829 passed`
- `python -m ruff check src/ tests/`: passed
- `git diff --check`: passed

## Required Deployment Sequence

Deploy only after committing and pushing the verified changes, in this order:

1. Bug 1 and Bug 3: data-quality fixes.
2. Bugs 4, 5, and 6: recorder and agent-read-path fixes.
3. Bug 2: dashboard deployed-capital labelling fix.
4. Feature 1: position tracking storage.
5. Features 2 and 3: MTM query and positions page.
6. Feature 4: expected PnL overview cards.

Server deployment command for each deployed commit:

```bash
cd ~/polymarket-arb && git pull && sudo docker compose -f deploy/docker-compose.yml --env-file ~/polymarket-arb/.env up -d --build
```

Service verification command after each deployment:

```bash
sudo docker compose -f ~/polymarket-arb/deploy/docker-compose.yml --env-file ~/polymarket-arb/.env ps
```
