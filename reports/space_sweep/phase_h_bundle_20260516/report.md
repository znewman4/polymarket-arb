# Space sweep report — 2026-05-16T14:55:41Z

> RESEARCH-ONLY — diagnostic / exploratory.  Not trading advice.
> run_id: `phase_h_bundle_20260516`
> report_integrity: **ok**
> credibility: **exploratory_only_not_credible**

## Headline figures

| Metric | Value |
| --- | --- |
| Spaces analysed | 19 |
| Accepted simulated trades | 0 |
| Gross violations | 12450 |
| Net violations | 9812 |
| Distinct relationships traded | 0 |
| Simulated PnL (sum across spaces) | 0.00 USDC |
| Diagnostic-only relationships (excluded from totals) | 1595 |

## Grade distribution

| Grade | Count |
| --- | --- |
| `B_PROMISING_GATE_BLOCKED` | 9 |
| `D_VALID_BUT_STRATEGICALLY_WEAK` | 4 |
| `E_INVALID_OR_AUDIT_RISK` | 6 |

## Top spaces by accepted trade count

| space_id | grade | trades | distinct_rels | pnl | primary_blocker |
| --- | --- | --- | --- | --- | --- |
| `2026_colombian_presidential_election_candidate_winner` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | - |
| `2026_colombian_presidential_election_first_round` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | - |
| `2026_nhl_stanley_cup` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | - |
| `2028_democrats_presidential_nomination` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | - |
| `2028_republicans_presidential_nomination` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | - |
| `2028_us_presidential_election` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | - |
| `2028_us_presidential_election_candidate_winner` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | - |
| `2028_us_presidential_election_party_winner` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | - |
| `finish_position` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | - |
| `gta_vi_reference_clock` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | - |
| `june_2026_reference_clock` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | - |
| `nba_eastern_conference_winner` | `E_INVALID_OR_AUDIT_RISK` | 0 | 0 | 0.00 | - |
| `nba_finals` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | - |
| `nba_western_conference_winner` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | - |
| `next_actor_announced` | `D_VALID_BUT_STRATEGICALLY_WEAK` | 0 | 0 | 0.00 | - |

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
