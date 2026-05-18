# Final strategy research report — 2026-05-16T21:37:54Z

> sweep_run_id: `expanded_real_v5`
> optimisation_run_id: `none`

## 1. Research-only disclaimer

**RESEARCH-ONLY simulation platform.**  No live trading, no wallets, no order placement, no on-chain transactions.  All numbers below are simulated/backtested.  Credibility ranges over `data_insufficient` / `exploratory_only_not_credible` / `report_invalid_for_strategy_conclusions`.  Never use as trading advice.


## 2. What was implemented

This run produced two new typed reporting pipelines:

1. **Space sweep** — aggregates an existing backtest run by outcome/context/bundle
   space, enforces typed row contracts (no diagnostic-only subtypes in trade totals),
   and grades every space into A/B/C/D/E/F/G, where Grade G means
   structurally clean but economically tiny on per-trade returns.
2. **Per-space optimisation** — runs a parameter grid per top space and ranks
   by a robustness score that combines positive-share, median PnL, drawdown,
   slippage sensitivity, and dominant-trade share.

All emissions are research-only and never claim live viability.


## 3. What changed from the previous Phase H report

* The new pipeline uses **typed Pydantic row contracts** that reject
  diagnostic-only subtypes (e.g. `same_topic_no_trade`) from ever entering
  trade or PnL totals.
* Reports are keyed by **space_id** with the precedence `outcome_space > context_space > bundle_space > synthetic`.
* Every accepted trade carries explicit `relationship_id`, `space_id`, and
  `strategy_family`.  Trades without attribution are dropped at the contract
  boundary and recorded in `integrity_notes`.
* Bundle diagnostics are now a separate typed stream (`BundleDiagnosticRow`)
  loaded into the space report.


## 4. Report integrity status

* report_integrity: **ok**
* credibility: **exploratory_only_not_credible**


## 5. Overall backtest summary

| Metric | Value |
| --- | --- |
| Spaces analysed | 48 |
| Accepted simulated trades | 52 |
| Distinct relationships traded | 3 |
| Simulated PnL (sum) | 5.98 USDC |
| Total deployed trade cost | 520.00 USDC |
| Total return on trade cost | 1.1500% |
| Median per-space trade return | 0.0000% |
| Grade A (passes per-trade economic gates) | 0 |
| Grade B (promising blocked) | 16 |
| Grade C (infrastructure blocked) | 9 |
| Grade D (strategically weak) | 9 |
| Grade E (invalid / audit risk) | 13 |
| Grade F (overfit / one-off) | 0 |
| Grade G (economically tiny signal) | 0 |


## 6. Best spaces by simulated PnL / median trade return

| space_id | grade | trades | distinct_rels | pnl / median_trade_return_pct | median_trade_return_pct |
| --- | --- | --- | --- | --- | --- |
| sports_championship_conference_progression | UNGRADED | 52 | 3 | 5.980000000000009 | 0.0015 |
| 13129551f78a304d4f7815a9f0b6b9a7 | B_PROMISING_GATE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 1da024481b7bf08570081b1a4c3ce2ce | B_PROMISING_GATE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 1f8505d4254d1cdc5bd0b53416eb6b54 | B_PROMISING_GATE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2020_us_presidential_election_candidate_winner | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2022_republicans_presidential_nomination | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2022_us_presidential_election_candidate_winner | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2025_us_presidential_election_candidate_winner | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2026_colombian_presidential_election_candidate_winner | E_INVALID_OR_AUDIT_RISK | 0 | 0 | 0.0 | 0.0 |
| 2026_colombian_presidential_election_first_round | E_INVALID_OR_AUDIT_RISK | 0 | 0 | 0.0 | 0.0 |


## 7. Best spaces by robust parameter performance

No optimisation results available.


## 8. Best spaces by trade count

| space_id | grade | trades | distinct_rels | pnl | median_trade_return_pct |
| --- | --- | --- | --- | --- | --- |
| sports_championship_conference_progression | UNGRADED | 52 | 3 | 5.980000000000009 | 0.0015 |
| 13129551f78a304d4f7815a9f0b6b9a7 | B_PROMISING_GATE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 1da024481b7bf08570081b1a4c3ce2ce | B_PROMISING_GATE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 1f8505d4254d1cdc5bd0b53416eb6b54 | B_PROMISING_GATE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2020_us_presidential_election_candidate_winner | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2022_republicans_presidential_nomination | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2022_us_presidential_election_candidate_winner | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2025_us_presidential_election_candidate_winner | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2026_colombian_presidential_election_candidate_winner | E_INVALID_OR_AUDIT_RISK | 0 | 0 | 0.0 | 0.0 |
| 2026_colombian_presidential_election_first_round | E_INVALID_OR_AUDIT_RISK | 0 | 0 | 0.0 | 0.0 |


## 9. Spaces blocked by infrastructure

| space_id | blocker | secondary | action |
| --- | --- | --- | --- |
| 2020_us_presidential_election_candidate_winner |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |
| 2022_republicans_presidential_nomination |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |
| 2022_us_presidential_election_candidate_winner |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |
| 2025_us_presidential_election_candidate_winner |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |
| 2026_local_government_elections_in_the_2026_taiwan_local_elections_presidential_election_candidate_winner |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |
| 2026_us_presidential_election_candidate_winner |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |
| 2027_us_presidential_election_candidate_winner |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |
| unknown_year_democrats_presidential_nomination |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |
| unknown_year_us_presidential_election_candidate_winner |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |


## 10. Spaces needing looser economic/replay settings

| space_id | gross_violations | blocker | action |
| --- | --- | --- | --- |
| 13129551f78a304d4f7815a9f0b6b9a7 | 2220 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| 1da024481b7bf08570081b1a4c3ce2ce | 36 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| 1f8505d4254d1cdc5bd0b53416eb6b54 | 35 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| 32fca2d972de651ddcf98b020db4d8f2 | 4496 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| 3496b04d1407bfa617b8d7e95b3dfc9a | 6022 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| 4aa9b9577769abc6b2617e899bc95949 | 6742 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| 59ee0871ba2cbc59237b9ef1654b92a2 | 1763 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| 6cd2ec28195e6eb6140ed2f2640eb1c2 | 35 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| 70bf08c03f7d8e51cf06690a14175fea | 119 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| 795fdadddff035e1369f5bcb901896cc | 35 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| 89457fa3a87ec777f61927d07717179e | 83 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| 8b3a4e3056a18f0839340670a6910cbc | 107 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| a8492ca9ed1f953a40a72715c44b57da | 72 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| b63564c4676697cd8b4529bfb38adaf5 | 47 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| b7ae8c187818a9adc502bec58d5cd39a | 72 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |


## 11. Spaces that appear strategically dead

| space_id | rels | gross_violations |
| --- | --- | --- |
| 2026_nhl_stanley_cup | 13 | 0 |
| 2028_us_presidential_election | 72 | 0 |
| 2028_us_presidential_election_party_winner | 1 | 0 |
| ligue_1_champion | 1 | 0 |
| nba_eastern_conference_winner | 3 | 0 |
| nba_finals | 18 | 0 |
| nba_western_conference_winner | 6 | 0 |
| next_actor_announced | 105 | 0 |
| premier_league_finish_position | 131 | 0 |


## 12. Spaces rejected as invalid / audit-risk

| space_id | diagnostic_only | suspicious | action |
| --- | --- | --- | --- |
| 2026_colombian_presidential_election_candidate_winner | 5 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_colombian_presidential_election_first_round | 139 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2028_democrats_presidential_nomination | 266 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2028_republicans_presidential_nomination | 2 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2028_us_presidential_election_candidate_winner | 23 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| champions_league_champion | 2 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| finish_position | 58 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| gta_vi_reference_clock | 28 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| june_2026_reference_clock | 1 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| premier_league_champion | 1 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| same_reference_clock_before_gta_vi | 2 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| unattributed_no_space | 957 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| world_cup_champion | 56 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |


## 13. Per-space optimisation findings

No optimisation run linked.


## 14. Parameter sensitivity findings

No spaces showed strong parameter sensitivity.


## 15. Main sources of simulated PnL (by strategy family)

| Strategy family | PnL |
| --- | --- |
| `nesting` | 5.98 |


## 16. Main blockers

| Blocker category | Count |
| --- | --- |
| `economic_or_replay` | 21850 |
| `infrastructure` | 887 |


## 17. Overfit warnings

No spaces flagged as overfit.


## 18. Credibility status

* Sweep credibility: `exploratory_only_not_credible`
* Optimisation credibility: `exploratory_only_not_credible` (always)
* This refactor expands trade-surface analysis; it does not unlock live
  trading or upgrade credibility to `credible_positive`.


## 19. Recommended next work

Priority order (highest first):

1. **Grade C → fix infrastructure**: backfill missing price history, set
   `known_total_candidates`, run alignment for the infrastructure-blocked
   spaces.
2. **Grade B → tune economic/replay**: rerun those spaces with looser
   presets (`exploratory_trade_surface` or `gross_violation_scan`) and
   feed the resulting leaderboard back into the optimiser.
3. **Grade A → optimise only after economic gates pass**: require
   `median_trade_return_pct >= 0.01`, `total_return_pct >= 0.005`, and
   at least five independent violation windows before tuning further.
4. **Grade F**: leave alone until more independent evidence appears.
5. **Grade G**: structurally robust but economically tiny; bundle with
   other promising spaces or skip.
6. **Grade E**: repair taxonomy/templates; do not optimise.


## 20. Structurally robust but economically tiny — bundle or skip

These spaces have positive simulated PnL and pass the structural checks, but fail at least one per-trade economic gate. Treat them as bundle ingredients at most, not standalone optimisation targets.

None.


## 21. Expanded-universe comparison snapshot

| Metric | Value |
| --- | --- |
| Markets discovered | unknown |
| Spaces analysed | 48 |
| Relationships generated | unknown |
| Distinct relationships traded | 3 |
| Distinct spaces traded | 1 |
| Total return on trade cost | 1.1500% |
| Grade distribution | {'B_PROMISING_GATE_BLOCKED': 16, 'C_INFRASTRUCTURE_BLOCKED': 9, 'D_VALID_BUT_STRATEGICALLY_WEAK': 9, 'E_INVALID_OR_AUDIT_RISK': 13, 'UNGRADED': 1} |

