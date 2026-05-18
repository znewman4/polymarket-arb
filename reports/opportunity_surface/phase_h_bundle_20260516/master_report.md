# Master Opportunity Surface Report — phase_h_bundle_20260516

> RESEARCH-ONLY — diagnostic / exploratory. Not trading advice.
> generated_at: `2026-05-16T12:04:05Z`
> preset: `EXPLORATORY_BUNDLE_AFTER_EXPANSION`

## Executive Summary

This report combines the Phase G statistics with a short narrative review. It is designed to answer three questions: what expanded, what looks risky, and what should be improved before any stronger claims are made.

| Metric | Value |
| --- | --- |
| Relationships loaded | 0 |
| Price history present | 0 |
| Aligned price series | 0 |
| Gross violations | 12450 |
| Candidates accepted | 0 |
| Simulated trades executed | 1092 |
| Distinct relationships traded | 0 |
| Distinct spaces traded | 4 |
| Suspicious rows | 9883 |
| Bundle diagnostic rows | 12592 |
| Credibility | `inconclusive` |
| Simulated PnL, secondary | 36.98094513889345901137716 |

## Main Achievements

- Captured 12450 gross opportunity signals and 1092 simulated trades for this run.
- Produced 0 family rollup rows, so the surface is ranked by trade count and coverage before simulated PnL.
- Generated 9883 suspicious-match audit rows and 12592 bundle diagnostic rows for manual review.

## Main Issues

- The largest blocker is `incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=7/?)` with 3067 rows.
- The most common suspicious flag is `incomplete_yes_blocked` with 8791 rows.

## Main Improvement Points

- Prioritise market-data coverage for the highest-count blockers before tuning strategy economics.
- Manually inspect `suspicious_matches.csv` and `suspicious_match_audit.md` before accepting newly expanded families.
- Strengthen deterministic evidence where rows show weak guard evidence, missing teams/candidates, or missing outcome spaces.
- Keep PnL secondary until strict and exploratory credibility labels improve beyond data-insufficient replay quality.

## Top Families By Trade Count

| Strategy family | Relationship subtype | Seen | Gross violations | Traded |
| --- | --- | ---: | ---: | ---: |
| none | none | 0 | 0 | 0 |

## Top Blockers

| Blocker | Count |
| --- | ---: |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=7/?) | 3067 |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=8/?) | 2985 |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=2/?) | 2739 |
| net_edge_below_threshold | 2709 |

## Suspicious Match Flags

| Flag | Count | Interpretation |
| --- | ---: | --- |
| incomplete_yes_blocked | 8791 | Manual review recommended. |
| subset_no_incomplete_but_valid | 1092 | Manual review recommended. |

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
