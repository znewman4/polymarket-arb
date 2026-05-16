# polymarket-arb — project status

> Research-only simulation platform. No live trading, wallets, or order placement.
> 464 tests passing.

---

## What this project does

Discovers cross-market relationships on Polymarket, backtests whether those
relationships create pricing anomalies, and simulates whether a strategy could
exploit them. Everything is research-only; nothing touches real money.

---

## What we built (chronological)

### 1. Core data pipeline
- Gamma market ingestion → NLP semantics extraction (Ollama) → market scoring
- CLOB price-history backfill with retry/fallback logic
- All storage is append-only Parquet + DuckDB views

### 2. Relationship mining
- Candidate pair generation (event overlap, entity overlap, outcome-space clusters)
- Taxonomy classifier → labels pairs with family/subtype/outcome_subtype fields
- Rulebook validation → accepted / rejected / needs_manual_review
- Fixed NBA/EPL taxonomy: `team_wins_championship`, `team_wins_conference`,
  `team_exact_finish_position`, `team_top_n_finish` correctly extracted

### 3. Context decision engine
- 8 context spaces in `configs/context_spaces/context_spaces_v1.yaml`
- 7 approved manual rules in `configs/context_spaces/manual_rules_v1.yaml`
- `apply_context_decisions()` routes every relationship to a strategy lane:
  `strict_context_valid` → `reviewed_context_valid` → `exploratory_*` → `research_only`
- **Bug fixed:** previously skipped ~5,000 unmapped relationships; now all 5,214
  get a decision written

### 4. Deterministic template registry
- `configs/deterministic_templates/templates_v1.yaml` — 8 templates approved at
  template level (not pair-by-pair):

  | Template | Domain | Strategy |
  |---|---|---|
  | sports_championship_implies_conference | sports nesting | narrow→broad |
  | sports_winner_implies_top_n | sports nesting | narrow→broad |
  | ranking_exact_finish_implies_top_n | sports ranking | narrow→broad |
  | ranking_exact_positions_mutually_exclusive | sports ranking | mutual exclusion |
  | sports_winner_exclusive_with_exact_finish | sports ranking | mutual exclusion |
  | sports_title_winners_mutually_exclusive | sports (World Cup, NHL, etc.) | mutual exclusion |
  | electoral_same_primary_mutual_exclusion | elections | mutual exclusion |
  | electoral_general_election_candidates | elections | mutual exclusion |
  | electoral_first_round_candidates | elections | mutual exclusion |
  | threshold_higher_implies_lower | price thresholds | narrow→broad |
  | date_earlier_implies_later | temporal | narrow→broad |

- Templates add `same_outcome_space_required` and `different_teams_required`
  match conditions. Audit CSV written on every `apply-context` run.

### 5. Data chain fixes
All 6 layers of the data chain were diagnosed and fixed:

| Layer | Fix |
|---|---|
| Context decisions | All rels get a decision (not just 7 hardcoded subtypes) |
| Token backfill | `backfill relationship-prices` — collects tokens from relationship pairs |
| Coverage scores | `diagnostic coverage-debug` diagnoses the 0.50–0.60 NLP-missing cluster |
| Funnel visibility | `relationships funnel-report` — one CSV row per relationship, all layers |
| Strategy filtering | `min_combined_prob_for_pairwise` blocks long-shot pairs (P(A)+P(B)<0.04) |
| Diagnostic CLI | `strategy context-aware backtest --diagnostic-*` flags |

### 6. Pairwise backtest (context-aware)
- `run_context_aware_backtest()` — review-lane filtered, no-lookahead, cost model
- `run_diagnostic_backtest()` — bypass flags for data pipeline validation
- **Current result (run: reviewed_templates_v3):**
  - 163 → **1,729** relationships reach `strict_context_valid`
  - 2 relationships trade: OKC Thunder NBA Finals→Conference, Man City EPL→finishes 2nd
  - Net PnL: **+$1,577 USDC** | Credibility: **`data_insufficient`** (< 30 trades)
  - Null baseline: $0 | Sensitivity: 24 cells, 7 positive

### 7. N-way bundle scanner
- Groups template-approved mutual exclusion pairs by `outcome_space_id`
- Scans whether sum of YES prices across a full outcome space creates arbitrage
- **Completeness gate:** `buy_all_yes` (underround) blocked on incomplete bundles;
  `buy_all_no` (overround) valid on any subset
- **Current result (run: bundle_bt_v2):**
  - 9 outcome spaces scanned (World Cup 39-team, elections 18–36 candidates, etc.)
  - 10,249 incomplete `buy_all_yes` signals correctly blocked
  - 3 genuine `buy_all_no` overround bundles executed:
    Colombian first round, NHL Stanley Cup, NBA Finals
  - Net PnL: **+$210 USDC** | Credibility: **`data_insufficient`** (< 30 trades)

---

## Current state

```
strict_context_valid relationships:  1,729
research_only:                        3,439
analysis_only (intentionally blocked):   46
Tests passing:                           464
```

**Primary finding:** The data pipeline works. The absence of more trades is
strategic, not a data problem — multi-entrant mutual exclusions (World Cup,
elections) need the category-bundle scanner, not pairwise overround detection.

---

## Key CLI commands

```bash
# Regenerate context decisions with templates
polymarket-arb relationships apply-context --all --keep-reviewed \
  --template-registry configs/deterministic_templates/templates_v1.yaml

# Pairwise backtest (reviewed, strict lane)
polymarket-arb strategy context-aware backtest \
  --min-relationship-confidence 0.35 --min-combined-prob 0.40 \
  --lane all_context_research --run-id <id>

# N-way bundle scan + backtest
polymarket-arb strategy template-bundle scan --slippage-bps 50
polymarket-arb strategy template-bundle backtest --starting-cash 10000

# Diagnostics
polymarket-arb diagnostic coverage-audit
polymarket-arb diagnostic experiment --starting-cash 10000
polymarket-arb relationships funnel-report
```

---

## What's still blocked / next steps

1. **More nesting/ranking relationships** — miner currently emits mostly
   `mutually_exclusive_category` pairs. Nesting (NBA, EPL) works; need more
   markets where same-team championship→conference pairs exist.

2. **NLP pipeline for relationship markets** — coverage scores stuck at 0.50–0.60
   because semantics/rulebook/implications were never run for these markets.
   Fix: `backfill targeted-semantic-queue && backfill semantic-pipeline`.

3. **`known_total_candidates` in metadata** — needed to unlock `buy_all_yes`
   bundle opportunities. Set for World Cup (32), NHL (32), NBA (30) in
   `configs/outcome_spaces/outcome_spaces_v1.yaml`.

4. **30-trade threshold** — credibility stays `data_insufficient` until we have
   30+ distinct trade pairs. Need more relationship types mined with violations.
