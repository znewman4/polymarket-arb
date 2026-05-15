# Classification And Backtest Audit - 2026-05-11

## Scope

Implemented the next strict repair-and-backtest phase for the local, read-only Polymarket semantic arbitrage research system. No live trading, wallet, authenticated order routing, private-key handling, signing, or real-funds logic was added.

## Key Implementation Changes

- Added deterministic strict taxonomy in `src/polymarket_arb/relationships/taxonomy.py`.
- Extended `RelationshipCandidateRow` and parquet schema with outcome subtype, entity type, stage, party/team/candidate, mixed-subtype reason, strategy family, and audit reason fields.
- Updated validators so broad relatedness cannot become pairwise contradiction/mutual exclusion unless same outcome space, same subtype, same stage/scope, and different option all pass.
- Added curated registry `configs/outcome_spaces/outcome_spaces_v1.yaml` and wired it into category bundle completeness.
- Changed targeted semantic queue default path to `data/backfill/targeted_semantics_queue_latest.csv`.
- Changed default NLP prompt version to `market_semantics_v2` and made `--allow-rerun-stale` rerun stale v1/missing-v2-structure rows.
- Added `--force` to semantic pipeline reruns.
- Added `polymarket-arb relationships classification-audit`.
- Added family-specific backtest CLIs:
  - `polymarket-arb strategy mutual-exclusion backtest`
  - `polymarket-arb strategy nesting backtest`
  - existing `polymarket-arb strategy category-bundle backtest`
- Added strict taxonomy regression tests for the known false-positive examples.

## Commands Run

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff check <changed files only>
.venv/bin/python -m mypy src
.venv/bin/python -m mypy src/polymarket_arb/relationships/taxonomy.py src/polymarket_arb/reports/classification_audit_report.py
.venv/bin/polymarket-arb backfill verify --limit 500
timeout 180s .venv/bin/polymarket-arb backfill semantic-pipeline --queue-csv data/backfill/targeted_semantics_queue_latest.csv --allow-rerun-stale --limit 500
timeout 300s .venv/bin/polymarket-arb relationships generate --limit 500
.venv/bin/polymarket-arb relationships report
.venv/bin/polymarket-arb relationships classification-audit
.venv/bin/polymarket-arb strategy mutual-exclusion backtest --starting-cash 10000 --slippage-bps 50 --run-id strict_family_20260511
.venv/bin/polymarket-arb strategy nesting backtest --starting-cash 10000 --slippage-bps 50 --run-id strict_family_20260511
.venv/bin/polymarket-arb strategy category-bundle backtest --starting-cash 10000 --slippage-bps 50 --run-id strict_family_20260511
.venv/bin/polymarket-arb strategy category-bundle report strict_family_20260511
.venv/bin/polymarket-arb strategy nesting-contradiction report strict_family_20260511
timeout 120s .venv/bin/polymarket-arb strategy nesting-contradiction null-baseline --reference-run-id strict_family_20260511
timeout 180s .venv/bin/polymarket-arb strategy nesting-contradiction sensitivity --grid slim --run-id strict_sensitivity_20260511
```

## Verification Results

- `pytest`: PASS, `352 passed`.
- Ruff full repo: FAIL, `87 errors`, apparently pre-existing style/lint issues outside this patch.
- Ruff changed files only: PASS.
- Mypy full repo: FAIL, `15 errors in 10 files`, apparently pre-existing typedness issues outside this patch.
- Mypy changed taxonomy/report files: PASS.
- Safety scan: no wallet/order/private-key/authenticated-trading strings found in the new relationship/report/strategy/backtest/backfill code beyond existing `<think>` stripping/assertion guards.

## Data Counts

- Markets rows: 530
- Market semantics rows: 988
- Price history rows: 191,405
- Backfill coverage rows: 721
- Relationship candidate rows: 9,729
- Latest classification audit rows: 5,122

## Pipeline Notes

- `backfill verify --limit 500` was stopped after more than 10 minutes at 99% CPU without output.
- Targeted v2 semantic rerun hit the 180-second timeout before completing. The queue and rerun logic are repaired, but this local run needs a longer supervised Ollama window.
- Relationship regeneration completed in 4.7s:
  - considered: 5,000
  - accepted in that bounded rerun: 0
  - rejected: 4,936
  - manual review: 64
  - top new rejection reason: `entity_mismatch`
  - strict demotions included `candidate_to_party_dependency=70`

## Classification Counts

Latest classification audit over current latest relationship rows:

- Total rows: 5,122
- Accepted strategy eligible: 92
- Mixed subtype: 82
- Manual review: 69
- Rejected: 4,957
- Same-reference-only: 7

Top relationship families:

- `unknown`: 2,672
- `mutual_exclusion`: 2,247
- `category`: 96
- `mixed_subtype`: 82
- `contradiction`: 12
- `same_reference_only`: 7
- `temporal`: 5
- `inverse`: 1

Top relationship subtypes:

- `same_topic_no_trade`: 2,615
- `candidate_wins_nomination`: 1,353
- `candidate_winner_same_election`: 671
- `next_actor_announced`: 105
- `first_round_winner`: 94
- `candidate_to_party_dependency`: 70
- `nomination_to_general_dependency`: 33
- `team_wins_championship`: 24
- `first_round_to_general_dependency`: 15

## Fixed Classification Examples

- LeBron wins 2028 US Presidential Election vs Democrats win 2028 US Presidential Election:
  - now classified as `candidate_to_party_dependency` / mixed subtype.
  - not pairwise mutual exclusion.
  - strategy ineligible by default.
- LeBron wins 2028 US Presidential Election vs JD Vance wins 2028 US Presidential Election:
  - same candidate-winner subtype and same general election stage.
  - pairwise mutual-exclusion strategy family.
- Democrats win 2028 US Presidential Election vs Republicans win 2028 US Presidential Election:
  - party-winner subtype.
  - inverse/party-pair strategy family, subject to registry/terms handling of other outcomes.
- Rubio Republican nomination vs Rubio general election:
  - `nomination_to_general_dependency`.
  - not contradiction.
  - strategy ineligible by default.
- Rihanna before GTA VI vs Bitcoin $1m before GTA VI:
  - `same_reference_clock_only`.
  - not tradable by default.

## Backtest Funnels

### Mutual Exclusion

- relationships loaded: 96
- strategy eligible relationships: 92
- relationships with price history: 92
- aligned price series: 92
- ticks evaluated: 228,067
- gross violations found: 0
- trades executed: 0
- no price violation count: 228,067
- no price violation pct: 100%
- net PnL: 0.00
- credibility label: `data_insufficient`

Blocker: no price violations after classification/coverage/alignment.

### Nesting

- relationships loaded: 0
- ticks evaluated: 0
- gross violations found: 0
- trades executed: 0
- net PnL: 0.00
- credibility label: `data_insufficient`

Blocker: no accepted strategy-eligible nesting relationships in the current latest relationship set.

### Category Bundle

- accepted relationships loaded: 96
- outcome spaces loaded: 3
- complete outcome spaces: 0
- analysis-only outcome spaces: 3
- aligned outcome spaces: 3
- ticks evaluated: 1,132
- rejected by incompleteness: 1,132
- trades executed: 0
- net PnL: 0.00
- credibility label: `data_insufficient`

Blocker: registry completeness gating. The observed bundles remain incomplete/unknown, so simulation is correctly blocked.

## Baseline And Sensitivity

- Null baseline for `strict_family_20260511`: PnL 0.00, trades 0.
- Slim sensitivity grid for `strict_sensitivity_20260511`: 16 cells, 0 positive PnL cells.

## Report Paths

- Relationship candidates: `reports/relationship_candidates/20260511_193415_801b1c/index.html`
- Classification audit: `reports/classification_audit/20260511_193415_a15be4/index.html`
- Strategy backtest report: `reports/strategy_backtests/strict_family_20260511/index.html`
- Category bundle report: `reports/category_bundles/strict_family_20260511/index.html`
- Mutual-exclusion artifacts: `data/backtests/strict_family_20260511/mutual_exclusion/`
- Nesting artifacts: `data/backtests/strict_family_20260511/nesting/`
- Category-bundle artifacts: `data/backtests/strict_family_20260511/category_bundle/`
- Null baseline: `data/backtests/strict_family_20260511/null_baseline/metrics.json`
- Sensitivity: `data/backtests/strict_sensitivity_20260511/sensitivity/grid_summary.csv`

## Conclusion

Credibility label: `data_insufficient`.

No alpha is claimed. The repaired classifier is stricter and corrected the known false-positive relationship families, but the current local lake still produces no simulated trades:

- pairwise mutual exclusion: no price violations across aligned ticks.
- nesting: no eligible relationships.
- category bundles: no complete registry-approved outcome spaces.

The next useful step is a longer targeted v2 semantic rerun so more rows have event atoms, propositions, outcome spaces, and terms metadata. After that, rerun relationship generation and the same classification audit/backtest funnel.

## Remaining Limitations

- The bounded v2 semantic rerun did not complete under the 180-second timeout.
- The full repo Ruff/Mypy checks still fail on older unrelated files; changed files pass their targeted checks.
- The strategy report command still primarily renders relationship-strategy artifacts and should be improved later into a truly combined family dashboard.
- Some latest accepted category relationships come from prior relationship IDs not touched by the bounded 5,000-candidate rerun; the classification audit reports current latest state, while the bounded rerun summary reports only that rerun's considered candidates.
