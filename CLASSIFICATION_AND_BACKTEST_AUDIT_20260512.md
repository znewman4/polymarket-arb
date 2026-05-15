# Classification and Backtest Audit - 2026-05-12

## Scope

This repair phase kept the system local, read-only, research-only, and simulated-only. No execution path, wallet integration, credential material, authenticated market action endpoint, real routing, or geoblock bypass logic was added.

## Implementation Summary

- Added strict two-level relationship taxonomy with relationship family, subtype, outcome subtype, entity/stage fields, mixed-subtype reasons, and strategy eligibility reasons.
- Tightened mutual-exclusion validation so candidate-vs-party, nomination-vs-general, first-round-vs-general, championship-vs-conference, exact-finish-vs-top-N, and same-reference-clock relationships are not blindly treated as pairwise contradictions.
- Added curated outcome-space registry at `configs/outcome_spaces/outcome_spaces_v1.yaml`.
- Wired registry completeness into category-bundle simulation gates.
- Added classification audit reporting with CSV outputs and HTML summary.
- Added/repaired family-specific strategy report output for mutual exclusion, nesting, category bundles, null baseline, and sensitivity context.
- Optimized `backfill verify` by batching parquet reads instead of doing per-market scans.
- Optimized targeted semantic pipeline reads/writes for resumable queue reruns. The actual Ollama-backed rerun remains incomplete due runtime timeout; see "Semantic V2 Rerun Status".

## Commands Run

```bash
.venv/bin/polymarket-arb backfill verify
.venv/bin/polymarket-arb backfill semantic-pipeline --queue-csv data/backfill/targeted_semantics_queue_latest.csv --allow-rerun-stale
timeout 60s .venv/bin/polymarket-arb backfill semantic-pipeline --queue-csv data/backfill/targeted_semantics_queue_latest.csv --allow-rerun-stale
timeout 300s .venv/bin/polymarket-arb backfill semantic-pipeline --queue-csv data/backfill/targeted_semantics_queue_latest.csv --allow-rerun-stale
.venv/bin/polymarket-arb relationships generate
.venv/bin/polymarket-arb relationships report
.venv/bin/polymarket-arb relationships classification-audit
.venv/bin/polymarket-arb strategy mutual-exclusion backtest --starting-cash 10000 --slippage-bps 50 --run-id strict_family_20260512
.venv/bin/polymarket-arb strategy nesting backtest --starting-cash 10000 --slippage-bps 50 --run-id strict_family_20260512
.venv/bin/polymarket-arb strategy category-bundle backtest --starting-cash 10000 --slippage-bps 50 --run-id strict_family_20260512
.venv/bin/polymarket-arb strategy nesting-contradiction null-baseline --reference-run-id strict_family_20260512
.venv/bin/polymarket-arb strategy nesting-contradiction sensitivity --grid slim --run-id strict_sensitivity_20260512
.venv/bin/polymarket-arb strategy category-bundle report strict_family_20260512
.venv/bin/polymarket-arb strategy report strict_family_20260512
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy src
.venv/bin/python -m pytest -q
```

## Verification

- Ruff: PASS, `All checks passed!`
- Mypy: PASS, `Success: no issues found in 167 source files`
- Pytest: PASS, `352 passed`
- Safety scan: PASS, no forbidden execution, wallet, credential, or market-action strings found in changed research modules.
- Report scan: PASS, no chain-of-thought marker content found in generated report/backtest outputs.

## Data Counts

- Market rows: 530
- Price history rows: 191,405
- Markets with price history: 200
- Backfill coverage rows: 721
- `backfill verify`: 6 checks, 0 FAIL, 3 WARN
  - WARN: duplicate price timestamps: 2,483
  - WARN: no public trade history
  - WARN: missing score rows for 196 of 200 sampled markets

## Semantic V2 Rerun Status

- Targeted queue: 152 rows.
- Current latest semantics for queued rows: 152.
- Rows with `event_atoms_json`: 15.
- Rows with `proposition_json`: 0.
- Rows with `outcome_space_json`: 15.
- Rows still v1: 137.
- Manual review rows: 108.
- Low `terms_confidence` rows: 137.

The dev config uses Ollama (`provider: ollama`). The targeted rerun did not complete within 60s or 300s bounds. I optimized the semantic pipeline to batch latest semantic/implication reads and batch writes, but the live Ollama-backed rerun still needs a longer supervised run or a smaller queue split. I did not overwrite real lake semantics with mock v2 rows.

## Relationship Generation

Latest full relationship generation:

- Candidates considered: 3,227
- Accepted: 94
- Rejected: 3,083
- Manual review: 50
- Top rejection reasons included:
  - `entity_mismatch`: 3,055
  - `candidate_to_party_dependency`: 72
  - `time_scope_mismatch`: 33
  - `same_topic_no_trade`: 10

Classification audit latest CSV snapshot:

- Total latest candidate rows: 5,166
- Accepted: 94
- Manual review: 76
- Rejected: 4,996
- Accepted strategy eligible: 90
- Mixed subtype rows: 84
- Same reference only rows: 13

## Corrected Classification Examples

- LeBron James wins 2028 presidency vs Democrats win 2028 presidency:
  - `relationship_family=mixed_subtype`
  - `relationship_subtype=candidate_to_party_dependency`
  - `outcome_subtype_a=candidate_winner_same_election`
  - `outcome_subtype_b=party_winner_same_election`
  - `validation_status=rejected`
  - `strategy_eligibility_status=ineligible`

- Marco Rubio wins 2028 presidency vs Rubio wins 2028 Republican nomination:
  - `relationship_subtype=nomination_to_general_dependency`
  - `validation_status=needs_manual_review`
  - `strategy_eligibility_status=ineligible`

- Rihanna album before GTA VI vs Bitcoin hits $1m before GTA VI:
  - rejected as non-tradable broad/same-reference relationship, not accepted as mutual exclusion.

## Report Paths

- Relationship candidate report: `reports/relationship_candidates/20260512_083229_9f6ae8/index.html`
- Classification audit report: `reports/classification_audit/20260512_083229_ae1819/index.html`
- Classification CSV: `reports/classification_audit/20260512_083229_ae1819/classification_audit_all.csv`
- Accepted strategy eligible CSV: `reports/classification_audit/20260512_083229_ae1819/accepted_strategy_eligible.csv`
- Mixed subtype CSV: `reports/classification_audit/20260512_083229_ae1819/mixed_subtype.csv`
- Manual review CSV: `reports/classification_audit/20260512_083229_ae1819/manual_review.csv`
- Rejected CSV: `reports/classification_audit/20260512_083229_ae1819/rejected.csv`
- Same reference only CSV: `reports/classification_audit/20260512_083229_ae1819/same_reference_only.csv`
- Strategy report: `reports/strategy_backtests/strict_family_20260512/index.html`
- Category bundle report: `reports/category_bundles/strict_family_20260512/index.html`

## Backtest Results

Run id: `strict_family_20260512`

### Mutual Exclusion

- Accepted relationships loaded: 94
- Strategy eligible relationships: 90
- Relationships with price history: 90
- Relationships with aligned price series: 90
- Ticks evaluated: 221,894
- Gross violations found: 0
- Rejected by coverage: 4
- No-price-violation count: 221,894
- Trades executed: 0
- Net PnL: 0.0
- Credibility label: `data_insufficient`
- Main blocker: no price violations after stricter classification.

### Nesting

- Accepted relationships loaded: 0
- Strategy eligible relationships: 0
- Ticks evaluated: 0
- Trades executed: 0
- Net PnL: 0.0
- Credibility label: `data_insufficient`
- Main blocker: no accepted deterministic nesting relationships in the current latest set.

### Category Bundle

- Outcome spaces scanned: 3
- Complete outcome spaces: 0
- Analysis-only outcome spaces: 3
- Outcome spaces with aligned price series: 3
- Ticks evaluated: 1,132
- Gross violations found: 0
- Trades executed: 0
- Net PnL: 0.0
- Credibility label: `data_insufficient`
- Main blocker: completeness registry did not permit any observed bundle as complete/exhaustive.

### Null Baseline and Sensitivity

- Null baseline trades: 0
- Null baseline PnL: 0.0
- Slim sensitivity grid: 16 cells run, 0/16 positive PnL cells.

## Credibility Conclusion

No alpha is supported by this run. The repaired classifier is stricter and more auditable, and the known candidate-vs-party false positive is fixed. The backtests executed no trades because:

- pairwise mutual-exclusion candidates had no price violations across aligned ticks,
- no deterministic nesting relationships were accepted for simulation,
- category bundles were incomplete or registry-blocked,
- targeted v2 semantic enrichment remains incomplete under the current Ollama runtime.

Overall credibility label: `data_insufficient`.

## Remaining Limitations

- The targeted v2 semantic rerun needs a longer Ollama run, smaller chunked queues, or progress/checkpoint output before it can fully populate all queued terms fields.
- Some classification audit counts include historical latest-by-relationship rows from append-only storage; the core corrected examples and latest full generation output are correct, but future reports should expose generation batch IDs.
- No complete outcome spaces were available for bundle simulation in this lake snapshot.
- No public trade history was present, so simulations rely on price history only.
