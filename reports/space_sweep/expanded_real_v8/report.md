# Space sweep report — 2026-05-17T10:39:25Z

> RESEARCH-ONLY — diagnostic / exploratory.  Not trading advice.
> run_id: `expanded_real_v8`
> report_integrity: **ok**
> credibility: **exploratory_only_not_credible**

## Headline figures

| Metric | Value |
| --- | --- |
| Spaces analysed | 263 |
| Accepted simulated trades | 72 |
| Gross violations | 924 |
| Net violations | 441 |
| Distinct relationships traded | 4 |
| Simulated PnL (sum across spaces) | 5.98 USDC |
| Total deployed trade cost | 720.00 USDC |
| Total return on trade cost | 0.8306% |
| Diagnostic-only relationships (excluded from totals) | 17518 |

## Grade distribution

| Grade | Count |
| --- | --- |
| `B_PROMISING_GATE_BLOCKED` | 3 |
| `C_INFRASTRUCTURE_BLOCKED` | 5 |
| `D_VALID_BUT_STRATEGICALLY_WEAK` | 13 |
| `E_INVALID_OR_AUDIT_RISK` | 240 |
| `UNGRADED` | 2 |

## Top spaces by accepted trade count

| space_id | grade | trades | distinct_rels | pnl | median_trade_return_pct | primary_blocker |
| --- | --- | --- | --- | --- | --- | --- |
| `sports_championship_conference_progression` | `UNGRADED` | 52 | 3 | 5.98 | 0.1500% | economic_or_replay |
| `sports_ranking_finish_position` | `UNGRADED` | 20 | 1 | 0.00 | 0.0000% | economic_or_replay |
| `089caf0e270e445bb06e54a63b74917d` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `2020_us_presidential_election_candidate_winner` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2022_republicans_presidential_nomination` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2022_us_presidential_election_candidate_winner` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2025_us_presidential_election_candidate_winner` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_colombian_presidential_election_candidate_winner` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_colombian_presidential_election_first_round` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_korea_dp_win_the_2026_south_korean_local_elections_presidential_election_party_winner` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_local_government_elections_in_the_2026_taiwan_local_elections_presidential_election` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_local_government_elections_in_the_2026_taiwan_local_elections_presidential_election_candidate_winner` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_nhl_stanley_cup` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_us_presidential_election_candidate_winner` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_will_aaron_ford_win_the_2026_nevada_governor_democratic_primary_election_presidential_election_candidate_winner` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | 0.0000% | - |

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

- trade_id=f07b2eec1b3c4ed6b65b41566c06e051 dropped: no strategy_family
- trade_id=ac81f58d6e584a848c3555ed2cd89bd9 dropped: no strategy_family
- trade_id=2b46dc2f4eee4f3c8429e4c3e9dbae90 dropped: no strategy_family
- trade_id=5094d6aa0c14448fb743c0e94a40f8d8 dropped: no strategy_family
- trade_id=3a1580419632459fb97cf08948e320cd dropped: no strategy_family
- trade_id=385f0ff207d64f738960d8f90ee6fb3f dropped: no strategy_family
- trade_id=c4d5b609e28742eb8c8b42cbe7d46f15 dropped: no strategy_family
- trade_id=e4a0ac7e6c134700b501af80e0eea181 dropped: no strategy_family
- trade_id=378d7e9be17e479a8a6104162c145f48 dropped: no strategy_family
- trade_id=bd0578d148d44beaa193351c65e7249c dropped: no strategy_family
