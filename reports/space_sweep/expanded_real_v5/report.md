# Space sweep report — 2026-05-16T21:37:23Z

> RESEARCH-ONLY — diagnostic / exploratory.  Not trading advice.
> run_id: `expanded_real_v5`
> report_integrity: **ok**
> credibility: **exploratory_only_not_credible**

## Headline figures

| Metric | Value |
| --- | --- |
| Spaces analysed | 48 |
| Accepted simulated trades | 52 |
| Gross violations | 23541 |
| Net violations | 22222 |
| Distinct relationships traded | 3 |
| Simulated PnL (sum across spaces) | 5.98 USDC |
| Total deployed trade cost | 520.00 USDC |
| Total return on trade cost | 1.1500% |
| Diagnostic-only relationships (excluded from totals) | 23467 |

## Grade distribution

| Grade | Count |
| --- | --- |
| `B_PROMISING_GATE_BLOCKED` | 16 |
| `C_INFRASTRUCTURE_BLOCKED` | 9 |
| `D_VALID_BUT_STRATEGICALLY_WEAK` | 9 |
| `E_INVALID_OR_AUDIT_RISK` | 13 |
| `UNGRADED` | 1 |

## Top spaces by accepted trade count

| space_id | grade | trades | distinct_rels | pnl | median_trade_return_pct | primary_blocker |
| --- | --- | --- | --- | --- | --- | --- |
| `sports_championship_conference_progression` | `UNGRADED` | 52 | 3 | 5.98 | 0.1500% | economic_or_replay |
| `13129551f78a304d4f7815a9f0b6b9a7` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `1da024481b7bf08570081b1a4c3ce2ce` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `1f8505d4254d1cdc5bd0b53416eb6b54` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `2020_us_presidential_election_candidate_winner` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2022_republicans_presidential_nomination` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2022_us_presidential_election_candidate_winner` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2025_us_presidential_election_candidate_winner` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_colombian_presidential_election_candidate_winner` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_colombian_presidential_election_first_round` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_local_government_elections_in_the_2026_taiwan_local_elections_presidential_election_candidate_winner` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_nhl_stanley_cup` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | 0.0000% | - |
| `2026_us_presidential_election_candidate_winner` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2027_us_presidential_election_candidate_winner` | `C_INFRASTRUCTURE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | - |
| `2028_democrats_presidential_nomination` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | 0.0000% | - |

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

- trade_id=0ce6d47a34994f44835429300f3768cb dropped: no strategy_family
- trade_id=c8fdfcdce6e14dfa9ee00bf0936a0e8c dropped: no strategy_family
- trade_id=96d060ab67594ca095b7fc6630c89d94 dropped: no strategy_family
- trade_id=54b06c43fed34c87976310cd5eb09b07 dropped: no strategy_family
- trade_id=e3e7ec35617f4f1c97f540cd5d8d275f dropped: no strategy_family
- trade_id=4c315a69969a411bb22ee99688996e17 dropped: no strategy_family
- trade_id=546c8218100c4f91b1246acfa0798a38 dropped: no strategy_family
- trade_id=5eb484807f4c4a20ab86dc9b7809e09a dropped: no strategy_family
- trade_id=7b66527b167d4189a20defa43ac1088c dropped: no strategy_family
- trade_id=d9cc4eb3e0a546afadd3d9f93dff3143 dropped: no strategy_family
