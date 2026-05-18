# Final strategy research report — 2026-05-17T10:39:46Z

> sweep_run_id: `expanded_real_v8`
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
| Spaces analysed | 263 |
| Accepted simulated trades | 72 |
| Distinct relationships traded | 4 |
| Simulated PnL (sum) | 5.98 USDC |
| Total deployed trade cost | 720.00 USDC |
| Total return on trade cost | 0.8306% |
| Median per-space trade return | 0.0000% |
| Grade A (passes per-trade economic gates) | 0 |
| Grade B (promising blocked) | 3 |
| Grade C (infrastructure blocked) | 5 |
| Grade D (strategically weak) | 13 |
| Grade E (invalid / audit risk) | 240 |
| Grade F (overfit / one-off) | 0 |
| Grade G (economically tiny signal) | 0 |


## 6. Best spaces by simulated PnL / median trade return

| space_id | grade | trades | distinct_rels | pnl / median_trade_return_pct | median_trade_return_pct |
| --- | --- | --- | --- | --- | --- |
| sports_championship_conference_progression | UNGRADED | 52 | 3 | 5.980000000000009 | 0.0015 |
| sports_ranking_finish_position | UNGRADED | 20 | 1 | 0.0 | 0.0 |
| 089caf0e270e445bb06e54a63b74917d | B_PROMISING_GATE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2020_us_presidential_election_candidate_winner | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2022_republicans_presidential_nomination | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2022_us_presidential_election_candidate_winner | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2025_us_presidential_election_candidate_winner | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2026_colombian_presidential_election_candidate_winner | E_INVALID_OR_AUDIT_RISK | 0 | 0 | 0.0 | 0.0 |
| 2026_colombian_presidential_election_first_round | E_INVALID_OR_AUDIT_RISK | 0 | 0 | 0.0 | 0.0 |
| 2026_korea_dp_win_the_2026_south_korean_local_elections_presidential_election_party_winner | E_INVALID_OR_AUDIT_RISK | 0 | 0 | 0.0 | 0.0 |


## 7. Best spaces by robust parameter performance

No optimisation results available.


## 8. Best spaces by trade count

| space_id | grade | trades | distinct_rels | pnl | median_trade_return_pct |
| --- | --- | --- | --- | --- | --- |
| sports_championship_conference_progression | UNGRADED | 52 | 3 | 5.980000000000009 | 0.0015 |
| sports_ranking_finish_position | UNGRADED | 20 | 1 | 0.0 | 0.0 |
| 089caf0e270e445bb06e54a63b74917d | B_PROMISING_GATE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2020_us_presidential_election_candidate_winner | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2022_republicans_presidential_nomination | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2022_us_presidential_election_candidate_winner | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2025_us_presidential_election_candidate_winner | C_INFRASTRUCTURE_BLOCKED | 0 | 0 | 0.0 | 0.0 |
| 2026_colombian_presidential_election_candidate_winner | E_INVALID_OR_AUDIT_RISK | 0 | 0 | 0.0 | 0.0 |
| 2026_colombian_presidential_election_first_round | E_INVALID_OR_AUDIT_RISK | 0 | 0 | 0.0 | 0.0 |
| 2026_korea_dp_win_the_2026_south_korean_local_elections_presidential_election_party_winner | E_INVALID_OR_AUDIT_RISK | 0 | 0 | 0.0 | 0.0 |


## 9. Spaces blocked by infrastructure

| space_id | blocker | secondary | action |
| --- | --- | --- | --- |
| 2020_us_presidential_election_candidate_winner |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |
| 2022_republicans_presidential_nomination |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |
| 2022_us_presidential_election_candidate_winner |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |
| 2025_us_presidential_election_candidate_winner |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |
| unknown_year_democrats_presidential_nomination |  |  | Backfill price history for markets in this space, then run alignment and a backtest. |


## 10. Spaces needing looser economic/replay settings

| space_id | gross_violations | blocker | action |
| --- | --- | --- | --- |
| 089caf0e270e445bb06e54a63b74917d | 58 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| 39f3e9a3a2595527a6e83fb7959cf843 | 155 | economic_or_replay | Run looser economic/replay preset (exploratory_trade_surface) on this space. |
| 76405aef13934b11bd2be96b93e36dc8 | 11 |  | Gross violations exist but no blocker identified — check alignment and run exploratory preset. |


## 11. Spaces that appear strategically dead

| space_id | rels | gross_violations |
| --- | --- | --- |
| 2026_local_government_elections_in_the_2026_taiwan_local_elections_presidential_election | 2 | 0 |
| 2026_local_government_elections_in_the_2026_taiwan_local_elections_presidential_election_candidate_winner | 2 | 0 |
| 2026_nhl_stanley_cup | 28 | 0 |
| 2026_us_presidential_election_candidate_winner | 272 | 0 |
| 2027_us_presidential_election_candidate_winner | 1260 | 0 |
| 2028_us_presidential_election | 72 | 0 |
| ligue_1_champion | 1 | 0 |
| nba_eastern_conference_winner | 6 | 0 |
| nba_finals | 28 | 0 |
| nba_western_conference_winner | 7 | 0 |
| next_actor_announced | 105 | 0 |
| premier_league_finish_position | 140 | 0 |
| unknown_year_us_presidential_election_candidate_winner | 81 | 0 |


## 12. Spaces rejected as invalid / audit-risk

| space_id | diagnostic_only | suspicious | action |
| --- | --- | --- | --- |
| 2026_colombian_presidential_election_candidate_winner | 5 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_colombian_presidential_election_first_round | 139 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_korea_dp_win_the_2026_south_korean_local_elections_presidential_election_party_winner | 3 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_will_aaron_ford_win_the_2026_nevada_governor_democratic_primary_election_presidential_election_candidate_winner | 1 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_will_adam_crum_win_the_2026_alaska_governor_election_presidential_election_candidate_winner | 48 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_will_adam_miller_win_the_2026_los_angeles_mayoral_election_presidential_election_candidate_winner | 35 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_will_afd_win_the_most_seats_in_the_2026_berlin_state_elections_presidential_election_candidate_winner | 6 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_will_ahn_cheol_soo_win_the_2026_gyeonggi_province_gubernatorial_election_presidential_election_candidate_winner | 21 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_will_ahn_cheol_soo_win_the_2026_seoul_mayoral_election_presidential_election_candidate_winner | 31 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_will_alex_padilla_win_the_california_governor_election_in_2026_presidential_election_candidate_winner | 21 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_will_andy_biggs_win_the_2026_arizona_governor_republican_primary_election_presidential_election_candidate_winner | 2 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_will_antonio_villaraigosa_win_the_california_governor_election_in_2026_presidential_election_candidate_winner | 19 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_will_arya_azma_win_the_2026_oklahoma_governor_democratic_primary_election_presidential_election_candidate_winner | 1 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_will_asaad_alnajjar_win_the_2026_los_angeles_mayoral_election_presidential_election_candidate_winner | 43 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |
| 2026_will_austin_beutner_win_the_2026_los_angeles_mayoral_election_presidential_election_candidate_winner | 40 | 0 | Repair taxonomy/templates — space is dominated by diagnostic-only relationships. |


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
| `economic_or_replay` | 318 |
| `infrastructure` | 128 |


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
| Spaces analysed | 263 |
| Relationships generated | unknown |
| Distinct relationships traded | 4 |
| Distinct spaces traded | 2 |
| Total return on trade cost | 0.8306% |
| Grade distribution | {'B_PROMISING_GATE_BLOCKED': 3, 'C_INFRASTRUCTURE_BLOCKED': 5, 'D_VALID_BUT_STRATEGICALLY_WEAK': 13, 'E_INVALID_OR_AUDIT_RISK': 240, 'UNGRADED': 2} |

