# Space sweep report — 2026-05-17T12:09:00Z

> RESEARCH-ONLY — diagnostic / exploratory.  Not trading advice.
> run_id: `aggressive_ollama_v9`
> report_integrity: **ok**
> credibility: **exploratory_only_not_credible**

## Headline figures

| Metric | Value |
| --- | --- |
| Spaces analysed | 324 |
| Accepted simulated trades | 148 |
| Gross violations | 1129 |
| Net violations | 564 |
| Distinct relationships traded | 7 |
| Simulated PnL (sum across spaces) | 8.07 USDC |
| Total deployed trade cost | 592.00 USDC |
| Total return on trade cost | 1.3632% |
| Diagnostic-only relationships (excluded from totals) | 29867 |

## Grade distribution

| Grade | Count |
| --- | --- |
| `B_PROMISING_GATE_BLOCKED` | 2 |
| `C_INFRASTRUCTURE_BLOCKED` | 8 |
| `D_VALID_BUT_STRATEGICALLY_WEAK` | 66 |
| `F_OVERFIT_ONE_OFF` | 3 |
| `E_INVALID_OR_AUDIT_RISK` | 243 |
| `UNGRADED` | 2 |

## Top spaces by accepted trade count

| space_id | grade | trades | distinct_rels | pnl | median_trade_return_pct | primary_blocker |
| --- | --- | --- | --- | --- | --- | --- |
| `sports_championship_conference_progression` | `UNGRADED` | 62 | 3 | 2.91 | 0.1500% | economic_or_replay |
| `llm_hyp_540881_559651_temporal_ordering_pair` | `F_OVERFIT_ONE_OFF` | 25 | 1 | 1.50 | 1.5000% | economic_or_replay |
| `unknown_year_us_presidential_election_candidate_winner` | `F_OVERFIT_ONE_OFF` | 25 | 1 | 1.02 | 1.0000% | economic_or_replay |
| `sports_ranking_finish_position` | `UNGRADED` | 25 | 1 | 0.00 | 0.0000% | economic_or_replay |
| `2026_us_presidential_election_candidate_winner` | `F_OVERFIT_ONE_OFF` | 11 | 1 | 2.64 | 6.0000% | infrastructure |
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

- trade_id=80287e217d4f418686c58c374ea6f9b8 dropped: no strategy_family
- trade_id=07df1df2878b4a78a0eff99eb3e82b44 dropped: no strategy_family
- trade_id=df434e5f2e1c4c6594f72477060d34fb dropped: no strategy_family
- trade_id=73d2e8f0be1643878a90f00016247599 dropped: no strategy_family
- trade_id=5f34fd9542e149dcb31b8b9f87a1feca dropped: no strategy_family
- trade_id=7ecf6e5038b94d1ab78f393d767a58dc dropped: no strategy_family
- trade_id=e57ea684aa7044b8b68e28ddb9026a28 dropped: no strategy_family
- trade_id=fdf5851fce7e4f5eaa0571955de3aa82 dropped: no strategy_family
- trade_id=a52e990232414ba4920d26d8fe1bb468 dropped: no strategy_family
- trade_id=e600c28536a547bc8c0a6f79f9dcc2da dropped: no strategy_family
