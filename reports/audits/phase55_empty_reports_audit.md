# Phase 5.5 Empty Reports Audit

Generated: 2026-05-11

## Summary

The generated Phase 5.5 reports were empty for two separate reasons:

1. The relationship miner was loading zero semantic rows because the existing `market_semantics` parquet files use an older schema that lacks `sufficient_conditions_for_no`. The loader treated that as a row-construction failure and silently dropped every semantics row.
2. After fixing that loader compatibility issue, the current local lake still has no accepted relationships and no historical backtest inputs. The relationship report now contains rejected candidates, but the strategy backtest remains data-insufficient.

## Current Report Artifacts

- Relationship report: `reports/relationship_candidates/latest/index.html`
- Relationship CSVs:
  - `reports/relationship_candidates/latest/relationships.csv`
  - `reports/relationship_candidates/latest/rejected_relationships.csv`
  - `reports/relationship_candidates/latest/accepted_relationships.csv`
  - `reports/relationship_candidates/latest/manual_review_relationships.csv`
- Strategy report: `reports/strategy_backtests/latest/index.html`
- Strategy metrics: `reports/strategy_backtests/latest/metrics.json`
- Raw strategy run: `data/backtests/phase55_cd_audit_20260511/relationship_strategy/`

## Data Lake Findings

Present normalised tables:

- `markets`
- `market_semantics`
- `market_embeddings`
- `market_implications`
- `market_scores`
- `best_quotes`
- `orderbook_snapshots`
- `events`
- `rulebook_evaluations`
- `backtest_metrics`
- `relationship_candidates`

Missing or not populated for this workflow:

- `price_history`
- `backfill_coverage`
- `strategy_candidates`
- `simulated_trades`

Latest market/semantic counts:

- Latest markets: 20
- Active markets selected by backfill universe: 14
- Active markets with semantic rows after loader fix: 10
- Active markets with `event_id`: 0
- Relationship candidate pairs generated after loader fix: 12

## Relationship Mining Outcome

Command run:

```bash
polymarket-arb relationships generate --limit 200
```

Result:

```text
considered=12 accepted=0 rejected=12 manual_review=0
by type: rejected=12
top rejections: entity_mismatch=12, time_scope_mismatch=4
```

Why this happened:

- No market rows have `event_id`, so event-based pair generation cannot help.
- Candidate generation falls back to entity overlap from semantics.
- The current market universe mostly contains broad same-topic markets:
  - GTA VI timing markets with different subjects.
  - NHL Stanley Cup winner markets with different teams.
- Validators correctly reject these because sharing a broad topic is not enough to imply a tradable relationship. The hard gate requires stronger entity overlap (`min_entity_match=0.50`), and all 12 candidates missed that gate.

Example rejection pattern:

```text
entity_overlap=0.100 < 0.5
entity_overlap=0.425 < 0.5
time_scope=0.400 < 0.5
```

## Strategy Backtest Outcome

Command run:

```bash
polymarket-arb strategy nesting-contradiction backtest \
  --starting-cash 10000 \
  --fee-bps 0 \
  --slippage-bps 50 \
  --min-net-edge 0.01 \
  --execution-model price_history_only \
  --run-id phase55_cd_audit_20260511
```

Result:

```text
trades_executed=0
net_pnl=0.00 USDC
total_return=0.00%
max_drawdown=0.0%
credibility=data_insufficient
relationships_considered=0
signals_generated=0
```

Why this happened:

- The strategy only reads accepted relationships via `iter_accepted()`.
- The current relationship lake has zero accepted relationships.
- The local lake also has no `price_history` table, so even accepted relationships would not have aligned historical price series to replay.
- The local lake also has no `backfill_coverage` table, so coverage-gated backtest eligibility cannot be established.

## Code Issues Found And Fixed

1. Fixed config path lookup in `src/polymarket_arb/relationships/__init__.py`.
   - Before: looked for `configs` above the repo root.
   - After: resolves `configs` from the repository root.

2. Fixed semantics loader compatibility in `src/polymarket_arb/relationships/__init__.py`.
   - Missing list fields from older parquet schemas now default to `[]`.
   - This changed semantics loaded from `0` to `10`.

3. Improved relationship report presentation in `src/polymarket_arb/reports/relationship_candidates_report.py`.
   - If there are no `same_topic_no_trade` examples, the report now shows top rejected candidates instead of leaving the rejected examples section blank.

Verification after fixes:

```text
tests/test_relationships + relationship report tests: 34 passed
ruff on touched relationship/report files: All checks passed
mypy on relationships package: Success
```

## What To Run To Populate Meaningful Reports

The reports are now wired correctly, but the lake needs a richer dataset.

Recommended sequence:

```bash
cd /home/znewman4/projects/polymarket-arb

# Ensure a larger market universe exists first.
polymarket-arb gamma fetch-markets --limit 200 --max-pages 1 --include-events

# Extract or refresh semantics for the active universe.
polymarket-arb backfill semantic-pipeline --limit 200 --allow-rerun-stale

# Backfill historical prices and compute coverage.
polymarket-arb backfill prices --days 180 --limit 200 --interval 1h
polymarket-arb backfill coverage --days 180 --limit 200

# Mine and report relationships.
polymarket-arb relationships generate --limit 200
polymarket-arb relationships report

# Only meaningful once accepted relationships and price history exist.
polymarket-arb strategy nesting-contradiction backtest \
  --starting-cash 10000 \
  --fee-bps 0 \
  --slippage-bps 50 \
  --min-net-edge 0.01 \
  --execution-model price_history_only
```

## Bottom Line

The report pipeline is no longer empty due to loader/report bugs. The relationship report now contains rejected candidate rows. The strategy report is still empty by design because this local lake currently has zero accepted relationships and no historical `price_history` or `backfill_coverage` data to replay.
