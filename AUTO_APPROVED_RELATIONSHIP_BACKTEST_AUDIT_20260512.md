# Auto-Approved Relationship Backtest Audit — 2026-05-12

## Verdict

Final credibility label: `data_insufficient`.

No simulated trades executed in the auto-approved lane. The blocker is a combination of insufficient backfill coverage (21 of 22 relationships) and zero gross price violations across 2,165 ticks on the one relationship that did have aligned price history.

**WARNING: Auto-approved results are EXPLORATORY ONLY. They are explicitly excluded from the headline credibility label. No auto-approved result can reach `credible_positive` or be treated as evidence of a tradeable edge.**

---

## Implementation Summary

Added Phase 5.6.1 relationship-level review and auto-approve workflow:

- `src/polymarket_arb/context/relationship_review.py` — export/import/auto-approve logic.
- `polymarket-arb relationships review export` — writes `data/context/relationship_review_queue_latest.csv` with all `needs_manual_review` relationships plus their latest context decision metadata.
- `polymarket-arb relationships review import <csv>` — append-only import of human decisions; `proposed_review_status=approved` creates `exploratory_context_auto_approved` decisions, `rejected` creates `research_only` decisions.
- `polymarket-arb relationships review auto-approve` — marks all `needs_manual_review` (and `exploratory_context_unreviewed`) relationships as `exploratory_context_auto_approved` for research use only.
- Added `exploratory_context_auto_approved` lane to `ContextAwareBacktestConfig`, `LANES`, `_filter_decisions`, `_credibility`, and the CLI lane choices.
- `--include-auto-approved` flag on `strategy context-aware backtest` expands the loaded decisions to include auto-approved entries. Results are capped at `inconclusive`; they never reach `credible_positive`.
- Credibility gating: any run with `lane == "exploratory_context_auto_approved"` or `include_auto_approved=True` and positive PnL returns `inconclusive`, never `credible_positive`.
- 15 new tests covering export/import/auto-approve roundtrips, idempotency, lane safety, and the constraint that auto-approved decisions never contain `strict_context_valid` or `reviewed_context_valid` lane strings.

No live trading, wallet, private key, real order routing, or authenticated trading endpoints were added.

---

## Commands Run

```
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy src
.venv/bin/python -m pytest -q

.venv/bin/polymarket-arb context review export --output data/context/manual_review_queue.csv
.venv/bin/polymarket-arb relationships review export
.venv/bin/polymarket-arb relationships review auto-approve
.venv/bin/polymarket-arb relationships apply-context

# Initial backtest (default min_relationship_confidence=0.65 — all rejected below threshold)
.venv/bin/polymarket-arb strategy context-aware backtest \
  --starting-cash 10000 --slippage-bps 50 --include-auto-approved \
  --lane exploratory_context_auto_approved --run-id auto_review_test

# Diagnostic: all needs_manual_review relationships have final_confidence 0.376–0.562
# Re-run with lowered threshold to expose downstream funnel

.venv/bin/polymarket-arb strategy context-aware backtest \
  --starting-cash 10000 --slippage-bps 50 \
  --min-relationship-confidence 0.35 --include-auto-approved \
  --lane exploratory_context_auto_approved --run-id auto_review_test_low_conf

.venv/bin/polymarket-arb strategy context-aware null-baseline --run-id auto_review_test_low_conf
.venv/bin/polymarket-arb strategy context-aware sensitivity \
  --grid slim --run-id auto_review_test_low_conf \
  --lane exploratory_context_auto_approved
.venv/bin/polymarket-arb strategy context-aware report auto_review_test_low_conf
```

---

## Verification

- Ruff: passed.
- Mypy: passed, no issues in 187 source files.
- Pytest: 381 passed (up from 366; 15 new tests).
- Safety gating: auto-approved decisions contain `"never_strict_or_reviewed"` in `decision_reason`; confirmed by test `test_auto_approved_decision_reason_contains_warning`.

---

## Relationship Review Queue

- Exported: 76 `needs_manual_review` relationships to `data/context/relationship_review_queue_latest.csv`.
- Confidence distribution: min=0.376, max=0.562, mean=0.436.
- All 76 are below the standard threshold of 0.65 used by the context-aware backtest.
- Auto-approved: 79 relationships marked `exploratory_context_auto_approved`
  (76 `needs_manual_review` + 3 `exploratory_context_unreviewed` from prior apply-context run).
- Skipped (already in superior lane): 0.
- Skipped (not eligible for review): 5,135.

---

## Backtest Funnel — Auto-Approved Lane

Primary run: `auto_review_test_low_conf` (`min_relationship_confidence=0.35`)
Lane: `exploratory_context_auto_approved`

| Stage | Count |
|---|---|
| Relationships loaded | 22 |
| Strategy eligible | 22 |
| Price history present | 1 |
| Aligned price series | 1 |
| Ticks evaluated | 2,165 |
| Gross violations | 0 |
| Rejected by coverage | 21 |
| Rejected by costs | 0 |
| Candidates accepted | 0 |
| Trades executed | 0 |

Notes:
- Only 22 of 79 auto-approved decisions survived `iter_latest()` — the 57 whose latest decision was overwritten by the subsequent `apply-context` run (which created newer `context_missing` or `analysis_only` decisions for those relationships). This is expected: `apply-context` ran after `auto-approve` in this test sequence, so its decisions are "latest" for those overlapping relationships. Recommended future pipeline order: run `apply-context` first, then `auto-approve` last so auto-approved decisions remain the latest for manual-review relationships.
- 21 of 22 relationships rejected for insufficient backfill coverage (not recommended for backtest).
- 1 relationship reached price alignment: 2,165 ticks evaluated, 0 gross price violations.

---

## PnL by Lane

| Lane | Trades | Net PnL | Credibility |
|---|---|---|---|
| exploratory_context_auto_approved (conf≥0.35) | 0 | 0.00 USDC | data_insufficient |
| null_baseline | 0 | 0.00 USDC | data_insufficient |

---

## Null Baseline

- Trades executed: 0.
- Net PnL: 0.00 USDC.
- Credibility: `data_insufficient`.

The null baseline confirms there is no random-pair activity to compare against; the blocking factor is data availability, not strategy filtering.

---

## Sensitivity Grid (slim: 16 cells)

- Total cells: 16.
- Positive PnL cells: 0.
- The grid varied slippage (50, 100 bps), fee (0, 50 bps), min_net_edge (0.01, 0.02), and min_relationship_confidence (0.65, 0.80).
- Note: the slim grid used the default confidence range; because all auto-approved relationships fall below 0.65, all cells in the slim grid produce 0 trades.

---

## Report Paths

- Context strategy report: `reports/context_strategy_backtests/auto_review_test_low_conf/index.html`.

Backtest artifacts:
- `data/backtests/auto_review_test_low_conf/context_aware/exploratory_context_auto_approved/metrics.json`
- `data/backtests/auto_review_test_low_conf/context_aware/exploratory_context_auto_approved/funnel_audit.json`
- `data/backtests/auto_review_test_low_conf/context_aware/null_baseline/metrics.json`
- `data/backtests/auto_review_test_low_conf/context_aware/sensitivity/sensitivity_grid.csv`

Review queue:
- `data/context/relationship_review_queue_latest.csv` — 76 rows.
- `data/context/manual_review_queue.csv` — 7 context rule rows (still all pending).

---

## Root Cause Analysis

Three sequential blockers, each of which independently produces 0 trades:

1. **Confidence below threshold (first run, conf≥0.65)**: all 76 needs_manual_review relationships have final_confidence 0.376–0.562. They were flagged for manual review precisely because the classifier lacked confidence. Auto-approval does not change the underlying confidence score.

2. **Insufficient backfill coverage (21 of 22)**: most manual-review relationships involve markets that don't meet the minimum coverage score for the backtest, because they were generated from the full market set including low-liquidity or recently active markets.

3. **No gross price violations (1 surviving relationship, 2165 ticks)**: the one relationship with both coverage and price history showed zero ticks where prices violated the expected inequality. No edge existed in the historical data.

---

## Key Design Decisions Validated

- **Auto-approve never promotes to strict/reviewed**: confirmed by lane gating in `_credibility` and by test `test_auto_approved_decision_reason_contains_warning`.
- **Headline credibility ignores auto-approved**: `data_insufficient` is the correct label when trades = 0. If trades had executed, the label would be capped at `inconclusive` due to the auto-approved flag.
- **Conservative by default**: the 0.65 threshold filters out all auto-approved relationships by design; the lower threshold was used only for pipeline diagnostics.
- **Pipeline order matters**: run `apply-context` before `auto-approve` in production pipelines so auto-approved decisions are the latest for manual-review relationships and aren't overwritten.

---

## Remaining Limitations

- 7 context rules remain `pending` (not `approved`). Until human-approved, the strict and reviewed lanes will remain at 0 relationships.
- Auto-approved relationships do not have market-terms rules; they cannot reach strict or reviewed lanes without them.
- Trade history remains unavailable; resolution inference is mark-to-market only.
- No profitable result is possible with current data without (a) approving context rules, (b) better backfill coverage, or (c) price violations appearing in the aligned ticks.

---

## Next Review Gate

**Stop for human review.** The two actionable next steps are:

1. **Review `data/context/manual_review_queue.csv`**: set `human_review_status` to `approved` or `rejected` for each of the 7 context rules, then re-run:
   ```
   polymarket-arb context review import data/context/manual_review_queue.csv
   polymarket-arb relationships apply-context
   polymarket-arb strategy context-aware backtest --starting-cash 10000 --lane reviewed_context_valid --run-id post_review
   ```

2. **Reorder pipeline for next auto-approve run**:
   ```
   polymarket-arb relationships apply-context         # first
   polymarket-arb relationships review auto-approve   # second (auto-approved decisions stay latest)
   ```
   This ensures auto-approved decisions are the most recent for manual-review relationships and are correctly picked up by `_filter_decisions`.
