# Space sweep report — 2026-05-16T14:55:43Z

> RESEARCH-ONLY — diagnostic / exploratory.  Not trading advice.
> run_id: `phase_h_strict_20260516`
> report_integrity: **ok**
> credibility: **exploratory_only_not_credible**

## Headline figures

| Metric | Value |
| --- | --- |
| Spaces analysed | 22 |
| Accepted simulated trades | 4 |
| Gross violations | 2679 |
| Net violations | 2679 |
| Distinct relationships traded | 2 |
| Simulated PnL (sum across spaces) | 0.00 USDC |
| Diagnostic-only relationships (excluded from totals) | 4262 |

## Grade distribution

| Grade | Count |
| --- | --- |
| `D_VALID_BUT_STRATEGICALLY_WEAK` | 13 |
| `E_INVALID_OR_AUDIT_RISK` | 7 |
| `UNGRADED` | 2 |

## Top spaces by accepted trade count

| space_id | grade | trades | distinct_rels | pnl | primary_blocker |
| --- | --- | --- | --- | --- | --- |
| `sports_championship_conference_progression` | `UNGRADED` | 2 | 1 | 0.00 | economic_or_replay |
| `sports_ranking_finish_position` | `UNGRADED` | 2 | 1 | 0.00 | economic_or_replay |
| `2026_colombian_presidential_election_candidate_winner` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | - |
| `2026_colombian_presidential_election_first_round` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | - |
| `2026_nhl_stanley_cup` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | - |
| `2028_democrats_presidential_nomination` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | - |
| `2028_republicans_presidential_nomination` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | - |
| `2028_us_presidential_election` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | - |
| `2028_us_presidential_election_candidate_winner` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | - |
| `2028_us_presidential_election_party_winner` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | - |
| `electoral_mutual_exclusion` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | confidence_gate |
| `finish_position` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | - |
| `gta_vi_reference_clock` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | - |
| `june_2026_reference_clock` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | - |
| `nba_eastern_conference_winner` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | - |

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
