# Context Rules and Backtest Audit - 2026-05-12

## Verdict

Final credibility label: `data_insufficient`.

No simulated trades executed. The blocker is classification/context gating, not price alignment or costs:

- Manual context rules were imported and validated, but all 7 remain `pending` because the exported review CSV was not changed to `approved` or `rejected` before import.
- Strict eligibility requires both reviewed world-context rules and reviewed Polymarket market-terms rules.
- The current context decisions produced 0 strict relationships and 0 reviewed relationships.
- The only context-aware lane with relationships loaded was exploratory, with 3 relationships, all ineligible.
- No eligible relationships reached price-history alignment, so no ticks were evaluated and no price violations could be tested.

This is the intended conservative outcome for an unreviewed/manual-first context slice.

## Implementation Summary

Added Phase 5.6 evidence-backed context infrastructure:

- Context DTOs, Parquet schemas, repositories, DuckDB views, and latest/audit views.
- Curated context registry at `configs/context_spaces/context_spaces_v1.yaml`.
- Manual context rules at `configs/context_spaces/manual_rules_v1.yaml`.
- New `polymarket_arb.context` package for registry parsing, manual import/export, review import, evidence helpers, validation, and relationship decisions.
- Context-aware relationship decision CLI under `polymarket-arb relationships apply-context`.
- Context rules report, context classification audit, and context strategy report.
- Context-aware backtest lanes with per-lane artifacts, funnel audit, no-lookahead audit, null baseline, sensitivity grid, and concentration output.
- CLI commands under `polymarket-arb context ...`, `polymarket-arb strategy context-aware ...`, and `polymarket-arb pipeline context-run-all`.
- Tests covering storage, registry, manual review workflow, fixture extraction/fetch safety, context decision gates, lane replay, reports, and expanded safety scans.

No live trading, wallet, private-key, real order routing, or authenticated trading endpoints were added. Live fetching remains optional and was not used in this run.

## Commands Run

- `.venv/bin/polymarket-arb backfill verify`
- `.venv/bin/polymarket-arb context registry audit`
- `.venv/bin/polymarket-arb context manual-rules import configs/context_spaces/manual_rules_v1.yaml`
- `.venv/bin/polymarket-arb context review export --output data/context/manual_review_queue.csv`
- `.venv/bin/polymarket-arb context review import data/context/manual_review_queue.csv`
- `.venv/bin/polymarket-arb context validate-rules`
- `.venv/bin/polymarket-arb context report`
- `.venv/bin/polymarket-arb relationships generate`
- `.venv/bin/polymarket-arb relationships apply-context`
- `.venv/bin/polymarket-arb relationships context-audit`
- `.venv/bin/polymarket-arb relationships report`
- `.venv/bin/polymarket-arb strategy context-aware backtest --starting-cash 10000 --lane strict_context_valid --run-id context_phase56`
- `.venv/bin/polymarket-arb strategy context-aware backtest --starting-cash 10000 --lane reviewed_context_valid --run-id context_phase56`
- `.venv/bin/polymarket-arb strategy context-aware backtest --starting-cash 10000 --lane exploratory_context_unreviewed --run-id context_phase56`
- `.venv/bin/polymarket-arb strategy context-aware null-baseline --run-id context_phase56`
- `.venv/bin/polymarket-arb strategy context-aware sensitivity --grid full --run-id context_phase56`
- `.venv/bin/polymarket-arb strategy context-aware report context_phase56`
- `.venv/bin/python -m ruff check src tests`
- `.venv/bin/python -m mypy src`
- `.venv/bin/python -m pytest -q`

## Verification

- Ruff: passed.
- Mypy: passed, no issues in 186 source files.
- Pytest: 366 passed.
- Safety scans: passed through the full test suite, including the new `context/`, CLI, backtest, report, relationship, and strategy coverage.
- Chain-of-thought marker persistence: safety/report tests passed.

## Lake Verification

`backfill verify`:

- Markets table: pass, 200 total, 0 missing token ids.
- Price history: warn, 186,496 rows, 0 out-of-bounds, 2,483 duplicate timestamps.
- Trade history: warn, no trade history.
- Semantics coverage: warn, 200 markets, 0 missing semantics, 196 missing scores, 0 missing implications.
- Backfill coverage: pass, 493 rows, 119 recommended for backtest, 289 low coverage markets.

## Context Registry and Rules

Registry audit:

- Context spaces: 7.
- Completeness classes: `partition_subset=2`, `open_world=3`, `known_complete=1`, `known_partial=1`.

Manual rules:

- Imported sources: 7.
- Imported documents: 7.
- Imported rules: 7.
- Review export: 7 pending rows to `data/context/manual_review_queue.csv`.
- Review import: 0 rule versions, because no row was changed to `approved` or `rejected`.
- Rule validation: 7 total, 0 invalid.

Latest rule status:

- `pending`: 7.
- `approved`: 0.
- `rejected`: 0.

Rule types:

- `championship_implies_conference`: 1.
- `market_terms_same_league_scope`: 1.
- `exact_finish_implies_top_n`: 1.
- `exact_finish_positions_mutually_exclusive`: 1.
- `market_terms_exact_finish_scope`: 1.
- `shared_reference_not_tradeable`: 1.
- `nomination_general_not_deterministic`: 1.

## Relationship and Context Decisions

Relationship generation:

- Candidates considered: 3,216.
- Accepted: 152.
- Rejected: 3,014.
- Manual review: 50.
- By type: `contradiction=5`, `inverse=3`, `mutually_exclusive_category=162`, `rejected=3035`, `same_reference_clock=8`, `same_topic_no_trade=3`.
- Top rejections: `entity_mismatch=2988`, `candidate_to_party_dependency=72`, `time_scope_mismatch=31`, `same_topic_no_trade=10`, `album_release_before_reference_vs_movie_release_before_reference=8`.

Context application:

- Relationships loaded: 5,214.
- Decisions written: 136.
- Upgraded: 0.
- Context missing: 87.
- Analysis-only: 46.

Latest decision counts:

- `research_only`: 87.
- `analysis_only`: 46.
- `exploratory_context_unreviewed`: 3.
- `strict_context_valid`: 0.
- `reviewed_context_valid`: 0.
- All 136 decisions are strategy-ineligible.

## Backtest Funnel

Run id: `context_phase56`.

Strict lane:

- Relationships loaded: 0.
- Strategy eligible: 0.
- Ticks evaluated: 0.
- Trades executed: 0.
- Net PnL: `0.0`.
- Credibility: `data_insufficient`.

Reviewed lane:

- Relationships loaded: 0.
- Strategy eligible: 0.
- Ticks evaluated: 0.
- Trades executed: 0.
- Net PnL: `0.0`.
- Credibility: `data_insufficient`.

Exploratory lane:

- Relationships loaded: 3.
- Strategy eligible: 0.
- Ticks evaluated: 0.
- Trades executed: 0.
- Net PnL: `0.0`.
- Credibility: `data_insufficient`.

No-lookahead audit:

- Rows checked: 0.
- Violations: 0.

Null baseline:

- Trades executed: 0.
- Net PnL: `0`.
- Credibility: `data_insufficient`.

Sensitivity:

- Full grid cells: 192.
- Positive cells: 0.

Concentration:

- No trades, so no market or context-space concentration.

Holdout:

- No eligible strict/reviewed trades existed, so holdout profitability could not be evaluated.
- No positive conclusion is possible.

## Report Paths

- Context rules report: `reports/context_rules/latest/index.html`.
- Context classification audit: `reports/context_classification_audit/latest/index.html`.
- Relationship report: `reports/relationship_candidates/latest/index.html`.
- Context strategy report: `reports/context_strategy_backtests/context_phase56/index.html`.

Backtest artifacts:

- `data/backtests/context_phase56/context_aware/strict_context_valid/metrics.json`.
- `data/backtests/context_phase56/context_aware/reviewed_context_valid/metrics.json`.
- `data/backtests/context_phase56/context_aware/exploratory_context_unreviewed/metrics.json`.
- `data/backtests/context_phase56/context_aware/null_baseline/metrics.json`.
- `data/backtests/context_phase56/context_aware/sensitivity/sensitivity_grid.csv`.

Report exports:

- `reports/context_strategy_backtests/context_phase56/strict_trades.csv`.
- `reports/context_strategy_backtests/context_phase56/reviewed_trades.csv`.
- `reports/context_strategy_backtests/context_phase56/exploratory_trades.csv`.
- `reports/context_strategy_backtests/context_phase56/analysis_only_relationships.csv`.
- `reports/context_strategy_backtests/context_phase56/rejected_candidates.csv`.
- `reports/context_strategy_backtests/context_phase56/metrics.json`.
- `reports/context_strategy_backtests/context_phase56/sensitivity_grid.csv`.
- `reports/context_strategy_backtests/context_phase56/concentration.json`.
- `reports/context_strategy_backtests/context_phase56/no_lookahead_audit.json`.

## Remaining Limitations

- Manual rules need human review approval before they can support reviewed or strict lanes.
- Strict lane also needs complete Polymarket market-terms rules for both markets in a relationship; the current curated terms set is intentionally incomplete.
- Live public evidence fetching was not enabled in this slice.
- Trade history remains unavailable in the current lake.
- No context-backed relationship reached price alignment, so profitability, holdout robustness, and concentration robustness remain untested rather than negative.

## Next Review Gate

Stop for human review before moving any context rule into strict or reviewed lanes. The next productive step is to review `data/context/manual_review_queue.csv`, approve or reject specific rules, add missing market-terms rules from Polymarket resolution criteria, then rerun context application and the same `context_phase56` backtest sequence.
