# Master Opportunity Surface Report — phase_h_explore_20260516

> RESEARCH-ONLY — diagnostic / exploratory. Not trading advice.
> generated_at: `2026-05-16T12:04:06Z`
> preset: `EXPLORATORY_TRADE_SURFACE_AFTER_EXPANSION`

## Executive Summary

This report combines the Phase G statistics with a short narrative review. It is designed to answer three questions: what expanded, what looks risky, and what should be improved before any stronger claims are made.

| Metric | Value |
| --- | --- |
| Relationships loaded | 1945 |
| Price history present | 1945 |
| Aligned price series | 1945 |
| Gross violations | 6470 |
| Candidates accepted | 72 |
| Simulated trades executed | 72 |
| Distinct relationships traded | 4 |
| Distinct spaces traded | 0 |
| Suspicious rows | 12258 |
| Bundle diagnostic rows | 0 |
| Credibility | `exploratory_only_not_credible` |
| Simulated PnL, secondary | 4519.64931223282660940280616 |

## Main Achievements

- Captured 6470 gross opportunity signals and 72 simulated trades for this run.
- Produced 2 family rollup rows, so the surface is ranked by trade count and coverage before simulated PnL.
- Generated 12258 suspicious-match audit rows and 0 bundle diagnostic rows for manual review.
- Included research replay output from presets: `exploratory_trade_surface`.

## Main Issues

- The largest blocker is `max_trades` with 5784 rows.
- The most common suspicious flag is `competition_missing` with 12258 rows.

## Main Improvement Points

- Prioritise market-data coverage for the highest-count blockers before tuning strategy economics.
- Manually inspect `suspicious_matches.csv` and `suspicious_match_audit.md` before accepting newly expanded families.
- Strengthen deterministic evidence where rows show weak guard evidence, missing teams/candidates, or missing outcome spaces.
- Keep PnL secondary until strict and exploratory credibility labels improve beyond data-insufficient replay quality.

## Leading Family

| Field | Value |
| --- | --- |
| Strategy family | inverse |
| Relationship subtype | same_topic_no_trade |
| Gross violations | 5759 |
| Distinct relationships traded | 1 |

## Top Families By Trade Count

| Strategy family | Relationship subtype | Seen | Gross violations | Traded |
| --- | --- | ---: | ---: | ---: |
| inverse | same_topic_no_trade | 5987 | 5759 | 1 |
| nested_a_implies_b | championship_implies_conference | 483 | 97 | 3 |

## Top Blockers

| Blocker | Count |
| --- | ---: |
| max_trades | 5784 |

## Suspicious Match Flags

| Flag | Count | Interpretation |
| --- | ---: | --- |
| competition_missing | 12258 | Sports relationship lacks a competition/league field. |
| season_missing | 12258 | Sports/election relationship lacks an explicit season or year. |
| weak_guard_evidence | 12258 | Evidence JSON did not include strong guard results. |
| exploratory_only_approval | 12254 | Relationship came from an exploratory lane/preset. |

## File Guide

| File | Why it matters |
| --- | --- |
| `summary.md` | Compact headline counts with PnL caveated as secondary. |
| `master_report.md` | This narrative/statistical review. |
| `opportunity_surface.csv` | Every gross opportunity signal, including suspicious flags. |
| `trade_candidates.csv` | Signals that passed economic filters. |
| `accepted_simulated_trades.csv` | Simulated fills/legs. Research-only. |
| `blocked_opportunities.csv` | Rejections and blockers to improve next. |
| `expansion_family_summary.csv` | Family rollup ranked by coverage/trade count. |
| `suspicious_matches.csv` | Audit flags for deterministic/context quality review. |
| `suspicious_match_audit.csv` | Deterministic random spot-check sample by bucket. |
| `before_after_counts.csv` | One-row machine-readable count summary. |
