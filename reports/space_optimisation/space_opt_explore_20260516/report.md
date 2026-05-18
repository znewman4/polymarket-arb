# Space optimisation report — 2026-05-16T14:55:59Z

> RESEARCH-ONLY — diagnostic / exploratory.  Not trading advice.
> run_id: `space_opt_explore_20260516`

## Headline

| Metric | Value |
| --- | --- |
| Spaces optimised | 1 |
| Total parameter cells evaluated | 60 |
| Cells with positive simulated PnL after costs | 60 |
| Spaces skipped | 0 |

## Classification distribution

| Class | Count |
| --- | --- |
| `robust_candidate` | 1 |

## Top spaces by robustness score

| space_id | classification | robustness | positive_share | median_pnl | dominant_share |
| --- | --- | --- | --- | --- | --- |
| `sports_championship_conference_progression` | `robust_candidate` | 0.881 | 1.00 | 4.83 | 0.50 |

## Best parameter sets per top space

| space_id | preset_id | slippage_bps | min_edge | min_conf | reentry | stake | pnl | win_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sports_championship_conference_progression` | `25490484c32b` | 0 | 0.0 | 0.35 | first_violation_only | 25.0 | 5.35 | 0.55 |

## Output files

| File | Contents |
| --- | --- |
| `optimisation_grid_results.csv` | Every (space, parameter cell) result |
| `best_params_by_space.csv` | Best non-overfit parameter set per space |
| `robustness_summary.csv` | Per-space classification + robustness score |
| `sensitivity_tables/<space>.csv` | Full parameter sweep per space |
| `report.md` | This document |
