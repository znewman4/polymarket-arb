# Space sweep report — 2026-05-17T12:08:43Z

> RESEARCH-ONLY — diagnostic / exploratory.  Not trading advice.
> run_id: `aggressive_baseline_v9`
> report_integrity: **ok**
> credibility: **exploratory_only_not_credible**

## Headline figures

| Metric | Value |
| --- | --- |
| Spaces analysed | 324 |
| Accepted simulated trades | 123 |
| Gross violations | 1093 |
| Net violations | 551 |
| Distinct relationships traded | 7 |
| Simulated PnL (sum across spaces) | 17.18 USDC |
| Total deployed trade cost | 1230.00 USDC |
| Total return on trade cost | 1.3967% |
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
| `sports_championship_conference_progression` | `UNGRADED` | 52 | 3 | 5.98 | 0.1500% | economic_or_replay |
| `llm_hyp_540881_559651_temporal_ordering_pair` | `F_OVERFIT_ONE_OFF` | 20 | 1 | 2.60 | 0.5000% | economic_or_replay |
| `unknown_year_us_presidential_election_candidate_winner` | `F_OVERFIT_ONE_OFF` | 20 | 1 | 2.00 | 1.0000% | economic_or_replay |
| `sports_ranking_finish_position` | `UNGRADED` | 20 | 1 | 0.00 | 0.0000% | economic_or_replay |
| `2026_us_presidential_election_candidate_winner` | `F_OVERFIT_ONE_OFF` | 11 | 1 | 6.60 | 6.0000% | infrastructure |
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

- trade_id=f1d28ea077524fee907cc129cb334de9 dropped: no strategy_family
- trade_id=739288573cbd48c2a235a9141e5c2631 dropped: no strategy_family
- trade_id=99f11774539c4718a393cbf347e9a125 dropped: no strategy_family
- trade_id=b630d0277e5d44f19c0190a6c7ad33a4 dropped: no strategy_family
- trade_id=be681d3d185d4d7987e55f30b28dfce0 dropped: no strategy_family
- trade_id=26fb656ed43f44b0bb7a8894aa558a74 dropped: no strategy_family
- trade_id=62de5b47befc46a798cd09594a92ffa0 dropped: no strategy_family
- trade_id=a583e2a48bf446a1b72a52b23d21f972 dropped: no strategy_family
- trade_id=7e965d45006a4a5bbb2bd44787e93603 dropped: no strategy_family
- trade_id=bf5be493b40c466ca4c79b1d2d7f9a16 dropped: no strategy_family
