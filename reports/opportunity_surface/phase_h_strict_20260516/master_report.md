# Master Opportunity Surface Report — phase_h_strict_20260516

> RESEARCH-ONLY — diagnostic / exploratory. Not trading advice.
> generated_at: `2026-05-16T12:04:05Z`
> preset: `STRICT_RESEARCH`

## Executive Summary

This report combines the Phase G statistics with a short narrative review. It is designed to answer three questions: what expanded, what looks risky, and what should be improved before any stronger claims are made.

| Metric | Value |
| --- | --- |
| Relationships loaded | 1945 |
| Price history present | 917 |
| Aligned price series | 617 |
| Gross violations | 2679 |
| Candidates accepted | 4 |
| Simulated trades executed | 4 |
| Distinct relationships traded | 2 |
| Distinct spaces traded | 0 |
| Suspicious rows | 4011 |
| Bundle diagnostic rows | 0 |
| Credibility | `data_insufficient` |
| Simulated PnL, secondary | 1577.35437712721701238182776 |

## Main Achievements

- Captured 2679 gross opportunity signals and 4 simulated trades for this run.
- Produced 2 family rollup rows, so the surface is ranked by trade count and coverage before simulated PnL.
- Generated 4011 suspicious-match audit rows and 0 bundle diagnostic rows for manual review.

## Main Issues

- The largest blocker is `market_exposure_limit` with 2675 rows.
- The most common suspicious flag is `season_missing` with 4011 rows.
- Credibility remains `data_insufficient`, which is expected for surface expansion but blocks viability claims.

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
| Gross violations | 2668 |
| Distinct relationships traded | 1 |

## Top Families By Trade Count

| Strategy family | Relationship subtype | Seen | Gross violations | Traded |
| --- | --- | ---: | ---: | ---: |
| inverse | same_topic_no_trade | 2668 | 2668 | 1 |
| nested_a_implies_b | championship_implies_conference | 11 | 11 | 1 |

## Top Blockers

| Blocker | Count |
| --- | ---: |
| market_exposure_limit | 2675 |
| relationship_confidence_below_threshold | 1028 |
| real_relationship_but_pairwise_not_tradeable | 300 |

## Suspicious Match Flags

| Flag | Count | Interpretation |
| --- | ---: | --- |
| season_missing | 4011 | Sports/election relationship lacks an explicit season or year. |
| weak_guard_evidence | 3863 | Evidence JSON did not include strong guard results. |
| competition_missing | 3109 | Sports relationship lacks a competition/league field. |
| low_confidence | 1028 | Deterministic/context confidence is low. |
| validation_rejected | 1028 | Context validation rejected this relationship. |
| candidate_missing | 1 | Election/candidate metadata is incomplete. |

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
