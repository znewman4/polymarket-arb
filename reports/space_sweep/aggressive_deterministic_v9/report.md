# Space sweep report — 2026-05-17T12:08:49Z

> RESEARCH-ONLY — diagnostic / exploratory.  Not trading advice.
> run_id: `aggressive_deterministic_v9`
> report_integrity: **ok**
> credibility: **exploratory_only_not_credible**

## Headline figures

| Metric | Value |
| --- | --- |
| Spaces analysed | 324 |
| Accepted simulated trades | 248 |
| Gross violations | 1129 |
| Net violations | 564 |
| Distinct relationships traded | 7 |
| Simulated PnL (sum across spaces) | 10.14 USDC |
| Total deployed trade cost | 992.00 USDC |
| Total return on trade cost | 1.0218% |
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
| `sports_championship_conference_progression` | `UNGRADED` | 87 | 3 | 3.18 | 0.2500% | economic_or_replay |
| `llm_hyp_540881_559651_temporal_ordering_pair` | `F_OVERFIT_ONE_OFF` | 50 | 1 | 2.64 | 1.0000% | economic_or_replay |
| `unknown_year_us_presidential_election_candidate_winner` | `F_OVERFIT_ONE_OFF` | 50 | 1 | 1.68 | 1.0000% | - |
| `sports_ranking_finish_position` | `UNGRADED` | 50 | 1 | 0.00 | 0.0000% | economic_or_replay |
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

- trade_id=2b82a273631946088e13ada118aa4f88 dropped: no strategy_family
- trade_id=68cbc4a5eb7d43fbb44196388aef0069 dropped: no strategy_family
- trade_id=73f7cfb3f8f846d4a0e4240e6e70247a dropped: no strategy_family
- trade_id=c457f3ea5e3a4b2887264191d48a40c2 dropped: no strategy_family
- trade_id=07c37939cb0b43b2979ee817210ffe57 dropped: no strategy_family
- trade_id=c11855d2832a49cfa9c76e7571e23814 dropped: no strategy_family
- trade_id=84c64814bf524d73b6d4774fdbaf4efa dropped: no strategy_family
- trade_id=94abd5b937894224a115803c6216bb85 dropped: no strategy_family
- trade_id=fa18a31a0cfb438c8fc6d3457e2f1121 dropped: no strategy_family
- trade_id=6649ba38e6544be7a3492af45e8f2a50 dropped: no strategy_family
