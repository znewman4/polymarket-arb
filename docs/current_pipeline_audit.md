# Current Pipeline Audit - 2026-05-11

Research-only run. No wallet, signing, order placement, authenticated CLOB trading,
or geoblock bypass code was added or used.

## What Changed

- Fixed CLOB price-history backfill compatibility:
  - `POST /batch-prices-history` now uses the current documented body shape:
    `{"markets": [...], "interval": "...", "start_ts": ..., "end_ts": ...}`.
  - Batch responses shaped as `{"history": {"token_id": [...]}}` are normalized
    into per-token payloads.
  - Long fine-grained requests that CLOB rejects with
    `invalid filters: 'startTs' and 'endTs' interval is too long` now retry with
    `interval=max` and `fidelity=720`.
  - 400 logging includes endpoint, token id, interval, fidelity, start/end
    timestamps, HTTP status, and a short response-body excerpt.
- Added deterministic old-row compatibility for relationships:
  - Category winner outcome spaces can be inferred from questions such as
    `Will the Carolina Hurricanes win the 2026 NHL Stanley Cup?`.
  - Simple `before`/`after` propositions can be inferred from question text for
    same-reference temporal grouping.
  - Exact same outcome-space plus different candidates is treated as terms
    compatible for category winner relationships.
  - Strategy eligibility now marks missing backfill coverage as ineligible.

## Verification

- `pytest`: 330 passed.
- `ruff`: not run; active Python environment has no `ruff` module installed.
- `mypy`: not run; active Python environment has no `mypy` module installed.
- `polymarket-arb backfill verify`: 0 FAIL, 3 WARN.
  - WARN: CLOB price history includes duplicate timestamps that parser dedupes.
  - WARN: no trade-history data; endpoint is best-effort.
  - WARN: semantic coverage is partial for the selected 200-market universe.

## Data Counts

- Latest markets: 501
- Active markets: 501
- Latest semantics rows: 142
- Latest semantics v2 field coverage:
  - `event_atoms_json`: 0
  - `proposition_json`: 0
  - `outcome_space_json`: 0
  - `terms_confidence`: 132
- Price history rows: 191,405
- Price history tokens: 400
- Price history markets: 200
- Latest backfill coverage rows: 493
- Recommended for backtest: 119
- Latest relationship candidates: 2,420
- Accepted relationships: 96
- Accepted and strategy-eligible relationships: 92
- Accepted mutually-exclusive category relationships: 96
- Temporal relationships generated: 5
- Strategy candidates table: not present
- Simulated trades table: not present
- Latest backtest run: `14873820501a4ffe99e86689e90b7802`
- Trades executed: 0
- Net PnL: 0.00 USDC
- Credibility label: `data_insufficient`

## Reports

- Historical dataset report:
  `/home/znewman4/projects/polymarket-arb/reports/historical_dataset/20260511_161907_505425/index.html`
- Semantic quality report:
  `/home/znewman4/projects/polymarket-arb/reports/semantic_quality/20260511_161907_0b7be1/index.html`
- Relationship candidate report:
  `/home/znewman4/projects/polymarket-arb/reports/relationship_candidates/20260511_161828_fcd6fa/index.html`
- Strategy backtest report:
  `/home/znewman4/projects/polymarket-arb/reports/strategy_backtests/14873820501a4ffe99e86689e90b7802/index.html`

## Interpretation

Price history now works for the selected universe. The previous zero-row result
was caused by two API compatibility issues: the stale batch request body and
requesting a 180-day `1h` window, which the live CLOB endpoint rejects. Token IDs
from Gamma `clobTokenIds` are valid CLOB asset IDs.

Mutually-exclusive category relationships are now accepted for deterministic
winner-category markets, including old semantic rows without `outcome_space_json`.
Temporal same-reference relationships are generated from old rows, but remain
manual-review/ineligible because the stored semantics still lack explicit v2
event atoms and propositions.

The strategy still has no demonstrated credibility. The latest backtest generated
signals from accepted relationships but executed 0 trades, so the honest label is
`data_insufficient`.
