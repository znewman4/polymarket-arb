# Space sweep report — 2026-05-17T12:08:55Z

> RESEARCH-ONLY — diagnostic / exploratory.  Not trading advice.
> run_id: `aggressive_ultra_loose_v9`
> report_integrity: **ok**
> credibility: **exploratory_only_not_credible**

## Headline figures

| Metric | Value |
| --- | --- |
| Spaces analysed | 324 |
| Accepted simulated trades | 36 |
| Gross violations | 1215 |
| Net violations | 1215 |
| Distinct relationships traded | 8 |
| Simulated PnL (sum across spaces) | 0.81 USDC |
| Total deployed trade cost | 72.00 USDC |
| Total return on trade cost | 1.1306% |
| Diagnostic-only relationships (excluded from totals) | 29867 |

## Grade distribution

| Grade | Count |
| --- | --- |
| `B_PROMISING_GATE_BLOCKED` | 2 |
| `C_INFRASTRUCTURE_BLOCKED` | 8 |
| `D_VALID_BUT_STRATEGICALLY_WEAK` | 66 |
| `F_OVERFIT_ONE_OFF` | 4 |
| `G_ECONOMICALLY_TINY_SIGNAL` | 1 |
| `E_INVALID_OR_AUDIT_RISK` | 243 |

## Top spaces by accepted trade count

| space_id | grade | trades | distinct_rels | pnl | median_trade_return_pct | primary_blocker |
| --- | --- | --- | --- | --- | --- | --- |
| `sports_championship_conference_progression` | `G_ECONOMICALLY_TINY_SIGNAL` | 19 | 4 | 0.26 | 0.2500% | replay_policy |
| `unknown_year_us_presidential_election_candidate_winner` | `F_OVERFIT_ONE_OFF` | 8 | 1 | 0.22 | 1.2500% | replay_policy |
| `sports_ranking_finish_position` | `F_OVERFIT_ONE_OFF` | 5 | 1 | 0.06 | 0.5500% | replay_policy |
| `llm_hyp_540881_559651_temporal_ordering_pair` | `F_OVERFIT_ONE_OFF` | 3 | 1 | 0.13 | 1.5000% | replay_policy |
| `2026_us_presidential_election_candidate_winner` | `F_OVERFIT_ONE_OFF` | 1 | 1 | 0.14 | 7.0000% | infrastructure |
| `2020_us_presidential_election_candidate_winner` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2022_republicans_presidential_nomination` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2022_us_presidential_election_candidate_winner` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2025_us_presidential_election_candidate_winner` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | infrastructure |
| `2026_colombian_presidential_election_candidate_winner` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_colombian_presidential_election_first_round` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_korea_dp_win_the_2026_south_korean_local_elections_presidential_election_party_winner` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_local_government_elections_in_the_2026_taiwan_local_elections_presidential_election` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_local_government_elections_in_the_2026_taiwan_local_elections_presidential_election_candidate_winner` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_nhl_stanley_cup` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | 0.0000% | - |

## Files in this report

| File | Contents |
| --- | --- |
| `space_leaderboard.csv` | Per-space summary, sortable |
| `space_blockers.csv` | Blocker categories per space |
| `space_strategy_summary.csv` | (space, strategy_family) rollup |
| `accepted_trades_by_space.csv` | All accepted trades with full attribution |
| `blocked_opportunities_by_space.csv` | All blocked candidates with blocker reason |
| `bundle_diagnostics_by_space.csv` | Per-bundle scan diagnostics |
| `space_examples.md` | Sample trades per space |
| `space_grades.md` | Spaces grouped by grade |
| `report.md` | This document |

## Integrity notes (first 10)

- trade_id=809017d10706411c82dd51229f0ebe6d dropped: no strategy_family
- trade_id=721032e7a8984e7abcad1ca4e76c04ef dropped: no strategy_family
- trade_id=b219b6fca35f4e8e84401c7519e5cb03 dropped: no strategy_family
- trade_id=a3c4f355a336448c839e1e0bdc1e3629 dropped: no strategy_family
- trade_id=7dabaf6225254f0ba04e622ddb65b51f dropped: no strategy_family
- trade_id=bbca7d3c66264a47b8ac357712d60b92 dropped: no strategy_family
- trade_id=2e547ed7a2df43ac93defca31ee9380f dropped: no strategy_family
- trade_id=260746468526418ea01029c57357bbee dropped: no strategy_family
- trade_id=1aeaf0b97dec4700a7870c5629dab263 dropped: no strategy_family
- trade_id=3825445af5094ccd8a195a3e14cf2366 dropped: no strategy_family
