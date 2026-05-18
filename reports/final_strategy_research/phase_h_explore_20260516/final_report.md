# Final strategy research report — 2026-05-16T14:56:00Z

> sweep_run_id: `phase_h_explore_20260516`
> optimisation_run_id: `space_opt_explore_20260516`

## 1. Research-only disclaimer

**RESEARCH-ONLY simulation platform.**  No live trading, no wallets, no order placement, no on-chain transactions.  All numbers below are simulated/backtested.  Credibility ranges over `data_insufficient` / `exploratory_only_not_credible` / `report_invalid_for_strategy_conclusions`.  Never use as trading advice.


## 2. What was implemented

This run produced two new typed reporting pipelines:

1. **Space sweep** — aggregates an existing backtest run by outcome/context/bundle
   space, enforces typed row contracts (no diagnostic-only subtypes in trade totals),
   and grades every space into A/B/C/D/E/F.
2. **Per-space optimisation** — runs a parameter grid per top space and ranks
   by a robustness score that combines positive-share, median PnL, drawdown,
   slippage sensitivity, and dominant-trade share.

All emissions are research-only and never claim live viability.


## 3. What changed from the previous Phase H report

* The new pipeline uses **typed Pydantic row contracts** that reject
  diagnostic-only subtypes (e.g. `same_topic_no_trade`) from ever entering
  trade or PnL totals.
* Reports are keyed by **space_id** with the precedence `outcome_space › context_space › bundle_space › synthetic`.
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
| Spaces analysed | 21 |
| Accepted simulated trades | 72 |
| Distinct relationships traded | 4 |
| Simulated PnL (sum) | 55.73 USDC |
| Grade A (profitable robust) | 1 |
| Grade B (promising blocked) | 0 |
| Grade C (infrastructure blocked) | 0 |
| Grade D (strategically weak) | 13 |
| Grade E (invalid / audit risk) | 6 |
| Grade F (overfit / one-off) | 1 |


## 6. Best spaces by simulated PnL

| space_id | grade | trades | distinct_rels | pnl |
| --- | --- | --- | --- | --- |
| sports_ranking_finish_position | F_OVERFIT_ONE_OFF | 20 | 1 | 49.75000000000003 |
| sports_championship_conference_progression | A_PROFITABLE_ROBUST_CANDIDATE | 52 | 3 | 5.9799999999999995 |
| 2026_colombian_presidential_election_candidate_winner | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2026_colombian_presidential_election_first_round | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2026_nhl_stanley_cup | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2028_democrats_presidential_nomination | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2028_republicans_presidential_nomination | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2028_us_presidential_election | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2028_us_presidential_election_candidate_winner | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2028_us_presidential_election_party_winner | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |


## 7. Best spaces by robust parameter performance

| space_id | class | robustness | positive_share | median_pnl |
| --- | --- | --- | --- | --- |
| sports_championship_conference_progression | robust_candidate | 0.8808 | 1.0 | 4.8303 |


## 8. Best spaces by trade count

| space_id | grade | trades | distinct_rels | pnl |
| --- | --- | --- | --- | --- |
| sports_championship_conference_progression | A_PROFITABLE_ROBUST_CANDIDATE | 52 | 3 | 5.9799999999999995 |
| sports_ranking_finish_position | F_OVERFIT_ONE_OFF | 20 | 1 | 49.75000000000003 |
| 2026_colombian_presidential_election_candidate_winner | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2026_colombian_presidential_election_first_round | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2026_nhl_stanley_cup | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2028_democrats_presidential_nomination | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2028_republicans_presidential_nomination | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2028_us_presidential_election | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2028_us_presidential_election_candidate_winner | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |
| 2028_us_presidential_election_party_winner | D_VALID_BUT_STRATEGICALLY_WEAK | 0 | 0 | 0.0 |


## 9. Spaces blocked by infrastructure

None.


## 10. Spaces needing looser economic/replay settings

None.


## 11. Spaces that appear strategically dead

| space_id | rels | gross_violations |
| --- | --- | --- |
| 2026_colombian_presidential_election_candidate_winner | 158 | 0 |
| 2026_colombian_presidential_election_first_round | 275 | 0 |
| 2026_nhl_stanley_cup | 13 | 0 |
| 2028_democrats_presidential_nomination | 1212 | 0 |
| 2028_republicans_presidential_nomination | 594 | 0 |
| 2028_us_presidential_election | 72 | 0 |
| 2028_us_presidential_election_candidate_winner | 653 | 0 |
| 2028_us_presidential_election_party_winner | 1 | 0 |
| nba_finals | 18 | 0 |
| nba_western_conference_winner | 6 | 0 |
| next_actor_announced | 105 | 0 |
| premier_league_finish_position | 133 | 0 |
| world_cup_champion | 741 | 0 |


## 12. Spaces rejected as invalid / audit-risk

| space_id | diagnostic_only | suspicious | action |
| --- | --- | --- | --- |
| finish_position | 35 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| gta_vi_reference_clock | 28 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| june_2026_reference_clock | 1 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| nba_eastern_conference_winner | 7 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| same_reference_clock_before_gta_vi | 2 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| unattributed_no_space | 963 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |


## 13. Per-space optimisation findings

| space_id | robustness | pnl | slippage_bps | min_edge | reentry |
| --- | --- | --- | --- | --- | --- |
| sports_championship_conference_progression | 0.8808 | 5.3521 | 0 | 0.0 | first_violation_only |


## 14. Parameter sensitivity findings

No spaces showed strong parameter sensitivity.


## 15. Main sources of simulated PnL (by strategy family)

| Strategy family | PnL |
| --- | --- |
| `nesting` | 55.73 |


## 16. Main blockers

| Blocker category | Count |
| --- | --- |
| `economic_or_replay` | 5784 |


## 17. Overfit warnings

| space_id | trades | dominant_rel_share | pnl |
| --- | --- | --- | --- |
| sports_ranking_finish_position | 20 | 1.0 | 49.75000000000003 |


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
3. **Grade A → robust optimisation**: keep optimising parameters on the
   highest-robustness spaces only; do NOT chase max PnL — only median PnL
   with low slippage sensitivity.
4. **Grade F**: leave alone until more independent evidence appears.
5. **Grade E**: repair taxonomy/templates; do not optimise.

