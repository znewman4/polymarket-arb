# Master Opportunity Surface Report — bundle_bt_v2

> RESEARCH-ONLY — diagnostic / exploratory. Not trading advice.
> generated_at: `2026-05-16T11:54:47Z`
> preset: `BUNDLE_BT_V2`

## Executive Summary

This report combines the Phase G statistics with a short narrative review. It is designed to answer three questions: what expanded, what looks risky, and what should be improved before any stronger claims are made.

| Metric | Value |
| --- | --- |
| Relationships loaded | 0 |
| Price history present | 0 |
| Aligned price series | 0 |
| Gross violations | 12930 |
| Candidates accepted | 0 |
| Simulated trades executed | 16 |
| Distinct relationships traded | 0 |
| Distinct spaces traded | 3 |
| Suspicious rows | 0 |
| Bundle diagnostic rows | 0 |
| Credibility | `data_insufficient` |
| Simulated PnL, secondary | not reported |

## Main Achievements

- Captured 12930 gross opportunity signals and 16 simulated trades for this run.
- Produced 0 family rollup rows, so the surface is ranked by trade count and coverage before simulated PnL.
- Generated 0 suspicious-match audit rows and 0 bundle diagnostic rows for manual review.

## Main Issues

- The largest blocker is `incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=1/?)` with 3321 rows.
- Credibility remains `data_insufficient`, which is expected for surface expansion but blocks viability claims.

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
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=1/?) | 3321 |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=7/?) | 3062 |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=8/?) | 2961 |
| net_edge_below_threshold | 1740 |
| already_open | 978 |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=19/?) | 548 |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=26/?) | 357 |

## Suspicious Match Flags

| Flag | Count | Interpretation |
| --- | ---: | --- |
| none | 0 | No suspicious rows were generated. |

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
