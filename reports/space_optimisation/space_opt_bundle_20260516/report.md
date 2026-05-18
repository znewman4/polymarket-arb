# Space optimisation report — 2026-05-16T14:42:53Z

> RESEARCH-ONLY — diagnostic / exploratory.  Not trading advice.
> run_id: `space_opt_bundle_20260516`

## Headline

| Metric | Value |
| --- | --- |
| Spaces optimised | 9 |
| Total parameter cells evaluated | 540 |
| Cells with positive simulated PnL after costs | 0 |
| Spaces skipped | 0 |

## Classification distribution

| Class | Count |
| --- | --- |
| `not_worth_tuning` | 9 |

## Top spaces by robustness score

| space_id | classification | robustness | positive_share | median_pnl | dominant_share |
| --- | --- | --- | --- | --- | --- |
| `2026_colombian_presidential_election_candidate_winner` | `not_worth_tuning` | 0.250 | 0.00 | 0.00 | 1.00 |
| `2026_colombian_presidential_election_first_round` | `not_worth_tuning` | 0.250 | 0.00 | 0.00 | 1.00 |
| `2026_nhl_stanley_cup` | `not_worth_tuning` | 0.250 | 0.00 | 0.00 | 1.00 |
| `2028_democrats_presidential_nomination` | `not_worth_tuning` | 0.250 | 0.00 | 0.00 | 1.00 |
| `2028_republicans_presidential_nomination` | `not_worth_tuning` | 0.250 | 0.00 | 0.00 | 1.00 |
| `2028_us_presidential_election_candidate_winner` | `not_worth_tuning` | 0.250 | 0.00 | 0.00 | 1.00 |
| `nba_finals` | `not_worth_tuning` | 0.250 | 0.00 | 0.00 | 1.00 |
| `premier_league_finish_position` | `not_worth_tuning` | 0.250 | 0.00 | 0.00 | 1.00 |
| `world_cup_champion` | `not_worth_tuning` | 0.250 | 0.00 | 0.00 | 1.00 |

## Best parameter sets per top space

| space_id | preset_id | slippage_bps | min_edge | min_conf | reentry | stake | pnl | win_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `2026_colombian_presidential_election_candidate_winner` | `e236372c5a39` | 50 | 0.01 | 0.35 | reenter_after_cooldown | 5.0 | 0.00 | 0.40 |
| `2026_colombian_presidential_election_first_round` | `e236372c5a39` | 50 | 0.01 | 0.35 | reenter_after_cooldown | 5.0 | 0.00 | 0.40 |
| `2026_nhl_stanley_cup` | `e236372c5a39` | 50 | 0.01 | 0.35 | reenter_after_cooldown | 5.0 | 0.00 | 0.40 |
| `2028_democrats_presidential_nomination` | `e236372c5a39` | 50 | 0.01 | 0.35 | reenter_after_cooldown | 5.0 | 0.00 | 0.40 |
| `2028_republicans_presidential_nomination` | `e236372c5a39` | 50 | 0.01 | 0.35 | reenter_after_cooldown | 5.0 | 0.00 | 0.40 |
| `2028_us_presidential_election_candidate_winner` | `e236372c5a39` | 50 | 0.01 | 0.35 | reenter_after_cooldown | 5.0 | 0.00 | 0.40 |
| `nba_finals` | `e236372c5a39` | 50 | 0.01 | 0.35 | reenter_after_cooldown | 5.0 | 0.00 | 0.40 |
| `premier_league_finish_position` | `e236372c5a39` | 50 | 0.01 | 0.35 | reenter_after_cooldown | 5.0 | 0.00 | 0.40 |
| `world_cup_champion` | `e236372c5a39` | 50 | 0.01 | 0.35 | reenter_after_cooldown | 5.0 | 0.00 | 0.40 |

## Output files

| File | Contents |
| --- | --- |
| `optimisation_grid_results.csv` | Every (space, parameter cell) result |
| `best_params_by_space.csv` | Best non-overfit parameter set per space |
| `robustness_summary.csv` | Per-space classification + robustness score |
| `sensitivity_tables/<space>.csv` | Full parameter sweep per space |
| `report.md` | This document |
