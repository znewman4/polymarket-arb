# Category Bundle Phase Audit

Generated: 2026-05-11

## What Changed

- Pairwise `nesting-contradiction` backtests now write `funnel_audit.json` and render the funnel in the HTML report.
- Category bundle analysis is separate from pairwise strategy logic:
  - `category_outcome_spaces` groups accepted mutually-exclusive category relationships.
  - `category_bundle_scanner` scores N-way YES/NO baskets.
  - `strategy category-bundle scan|backtest|report` writes independent artifacts and reports.
- Completeness is conservative. Unknown or incomplete outcome spaces are report-only and are not simulated by default.
- `backfill targeted-semantic-queue` writes a focused CSV for terms-aware semantic reruns.
- `backfill semantic-pipeline --queue-csv ...` can restrict extraction to that targeted queue.

## Latest Local Run

Pairwise run:

- Run ID: `codex_pairwise_funnel_20260511`
- Report: `reports/strategy_backtests/codex_pairwise_funnel_20260511/index.html`
- Accepted relationships loaded: 96
- Strategy-eligible accepted relationships: 92
- Relationships with price history: 92
- Relationships with aligned price series: 92
- Ticks evaluated: 228,067
- Gross violations found: 0
- Trades executed: 0
- Credibility: `data_insufficient`
- Main rejection/audit count: `no_price_violation=228067`

Category bundle run:

- Run ID: `codex_category_bundle_20260511`
- Report: `reports/category_bundles/codex_category_bundle_20260511/index.html`
- Outcome spaces scanned: 3
- Complete outcome spaces: 0
- Analysis-only outcome spaces: 3
- Ticks evaluated: 1,132
- Trades executed: 0
- Credibility: `data_insufficient`
- Main rejection/audit count: `incomplete_or_unknown_outcome_space=1132`

Observed category spaces:

- `2028_democratic_presidential_nomination`: 19 candidates, unknown configured total, analysis-only.
- `2028_republican_presidential_nomination`: 26 candidates, unknown configured total, analysis-only.
- `2028_us_presidential_election`: 38 candidates, unknown configured total, analysis-only.

## Data Counts

- Latest markets: 501
- Active markets: 501
- Latest semantic rows: 491
- `event_atoms_json` populated: 0
- `proposition_json` populated: 0
- `outcome_space_json` populated: 0
- Price history rows: 191,405
- Latest backfill coverage rows: 493
- Latest relationship candidates: 5,122
- Latest accepted relationships: 96
- Latest strategy-eligible relationship rows: 2,242
- Persisted strategy candidates: 0
- Persisted simulated trades: 0
- Targeted semantics queue rows: 152

## Interpretation

Price history is now present and usable for the current relationship universe. The pairwise strategy did not trade because it found no price violations, not because price alignment failed. Category bundles did not trade because the currently observed groups are not configured as complete/exhaustive outcome spaces; this is intentional under the conservative completeness policy.

The targeted semantic queue is ready at `data/backfill/targeted_semantics_queue_latest.csv`, but terms-aware v2 fields are still unpopulated in the current lake. Temporal dependency strategies should remain analysis-only or weak until that queue is rerun with the v2 prompt and the resulting fields populate.
