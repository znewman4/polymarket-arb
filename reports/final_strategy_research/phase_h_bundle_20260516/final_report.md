# Final strategy research report — 2026-05-16T14:42:55Z

> sweep_run_id: `phase_h_bundle_20260516`
> optimisation_run_id: `space_opt_bundle_20260516`

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
| Spaces analysed | 9 |
| Accepted simulated trades | 0 |
| Distinct relationships traded | 0 |
| Simulated PnL (sum) | 0.00 USDC |
| Grade A (profitable robust) | 0 |
| Grade B (promising blocked) | 0 |
| Grade C (infrastructure blocked) | 0 |
| Grade D (strategically weak) | 0 |
| Grade E (invalid / audit risk) | 0 |
| Grade F (overfit / one-off) | 0 |


## 6. Best spaces by simulated PnL

| space_id | grade | trades | distinct_rels | pnl |
| --- | --- | --- | --- | --- |
| 2026_colombian_presidential_election_candidate_winner | UNGRADED | 0 | 0 | 0.0 |
| 2026_colombian_presidential_election_first_round | UNGRADED | 0 | 0 | 0.0 |
| 2026_nhl_stanley_cup | UNGRADED | 0 | 0 | 0.0 |
| 2028_democrats_presidential_nomination | UNGRADED | 0 | 0 | 0.0 |
| 2028_republicans_presidential_nomination | UNGRADED | 0 | 0 | 0.0 |
| 2028_us_presidential_election_candidate_winner | UNGRADED | 0 | 0 | 0.0 |
| nba_finals | UNGRADED | 0 | 0 | 0.0 |
| premier_league_finish_position | UNGRADED | 0 | 0 | 0.0 |
| world_cup_champion | UNGRADED | 0 | 0 | 0.0 |


## 7. Best spaces by robust parameter performance

| space_id | class | robustness | positive_share | median_pnl |
| --- | --- | --- | --- | --- |
| 2026_colombian_presidential_election_candidate_winner | not_worth_tuning | 0.25 | 0.0 | 0.0 |
| 2026_colombian_presidential_election_first_round | not_worth_tuning | 0.25 | 0.0 | 0.0 |
| 2026_nhl_stanley_cup | not_worth_tuning | 0.25 | 0.0 | 0.0 |
| 2028_democrats_presidential_nomination | not_worth_tuning | 0.25 | 0.0 | 0.0 |
| 2028_republicans_presidential_nomination | not_worth_tuning | 0.25 | 0.0 | 0.0 |
| 2028_us_presidential_election_candidate_winner | not_worth_tuning | 0.25 | 0.0 | 0.0 |
| nba_finals | not_worth_tuning | 0.25 | 0.0 | 0.0 |
| premier_league_finish_position | not_worth_tuning | 0.25 | 0.0 | 0.0 |
| world_cup_champion | not_worth_tuning | 0.25 | 0.0 | 0.0 |


## 8. Best spaces by trade count

| space_id | grade | trades | distinct_rels | pnl |
| --- | --- | --- | --- | --- |
| 2026_colombian_presidential_election_candidate_winner | UNGRADED | 0 | 0 | 0.0 |
| 2026_colombian_presidential_election_first_round | UNGRADED | 0 | 0 | 0.0 |
| 2026_nhl_stanley_cup | UNGRADED | 0 | 0 | 0.0 |
| 2028_democrats_presidential_nomination | UNGRADED | 0 | 0 | 0.0 |
| 2028_republicans_presidential_nomination | UNGRADED | 0 | 0 | 0.0 |
| 2028_us_presidential_election_candidate_winner | UNGRADED | 0 | 0 | 0.0 |
| nba_finals | UNGRADED | 0 | 0 | 0.0 |
| premier_league_finish_position | UNGRADED | 0 | 0 | 0.0 |
| world_cup_champion | UNGRADED | 0 | 0 | 0.0 |


## 9. Spaces blocked by infrastructure

None.


## 10. Spaces needing looser economic/replay settings

None.


## 11. Spaces that appear strategically dead

None.


## 12. Spaces rejected as invalid / audit-risk

None.


## 13. Per-space optimisation findings

| space_id | robustness | pnl | slippage_bps | min_edge | reentry |
| --- | --- | --- | --- | --- | --- |
| 2026_colombian_presidential_election_candidate_winner | 0.25 | 0.0 | 50 | 0.01 | reenter_after_cooldown |
| 2026_colombian_presidential_election_first_round | 0.25 | 0.0 | 50 | 0.01 | reenter_after_cooldown |
| 2026_nhl_stanley_cup | 0.25 | 0.0 | 50 | 0.01 | reenter_after_cooldown |
| 2028_democrats_presidential_nomination | 0.25 | 0.0 | 50 | 0.01 | reenter_after_cooldown |
| 2028_republicans_presidential_nomination | 0.25 | 0.0 | 50 | 0.01 | reenter_after_cooldown |
| 2028_us_presidential_election_candidate_winner | 0.25 | 0.0 | 50 | 0.01 | reenter_after_cooldown |
| nba_finals | 0.25 | 0.0 | 50 | 0.01 | reenter_after_cooldown |
| premier_league_finish_position | 0.25 | 0.0 | 50 | 0.01 | reenter_after_cooldown |
| world_cup_champion | 0.25 | 0.0 | 50 | 0.01 | reenter_after_cooldown |


## 14. Parameter sensitivity findings

No spaces showed strong parameter sensitivity.


## 15. Main sources of simulated PnL

No accepted trades observed.


## 16. Main blockers

No blockers recorded.


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
3. **Grade A → robust optimisation**: keep optimising parameters on the
   highest-robustness spaces only; do NOT chase max PnL — only median PnL
   with low slippage sensitivity.
4. **Grade F**: leave alone until more independent evidence appears.
5. **Grade E**: repair taxonomy/templates; do not optimise.

