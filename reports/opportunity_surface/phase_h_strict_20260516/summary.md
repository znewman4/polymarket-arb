# Opportunity Surface Report — 2026-05-16T12:04:05Z

> RESEARCH-ONLY — diagnostic / exploratory. Not trading advice.
> run_id: `phase_h_strict_20260516`
> preset: `STRICT_RESEARCH`

## Summary (ranked by trade count — PnL is secondary)

| Metric | Value |
| --- | --- |
| Relationships loaded | 1945 |
| Price history present | 917 |
| Aligned price series | 617 |
| Gross violations | 2679 |
| Candidates accepted | 4 |
| **Simulated trades executed** | **4** |
| Distinct relationships traded | 2 |
| Distinct spaces traded | 0 |
| Credibility | `data_insufficient` |

## Simulated PnL (SECONDARY — do not use as primary criterion)

Net PnL: **1577.35437712721701238182776 USDC** (simulated, research-only, credibility = `data_insufficient`)

> PnL is reported for completeness only. This refactor's goal is > **trade count and coverage**, not profit optimisation.

## Top relationship families by gross violations

| strategy_family | subtype | relationships | gross_violations | traded |
| --- | --- | --- | --- | --- |
| inverse | same_topic_no_trade | 2668 | 2668 | 1 |
| nested_a_implies_b | championship_implies_conference | 11 | 11 | 1 |

## Top blockers (why opportunities were not accepted)

| Blocker | Count |
| --- | --- |
| market_exposure_limit | 2675 |
| relationship_confidence_below_threshold | 1028 |
| real_relationship_but_pairwise_not_tradeable | 300 |

## Files in this report

| File | Contents |
| --- | --- |
| `opportunity_surface.csv` | Every gross violation signal (pre-execution) |
| `trade_candidates.csv` | Signals that passed economic evaluation |
| `accepted_simulated_trades.csv` | Executed simulated trades |
| `blocked_opportunities.csv` | Rejected candidates with blocker reason |
| `expansion_family_summary.csv` | Per-family rollup (by trade count) |
| `suspicious_matches.csv` | Audit flags (Phase G: enriched) |
| `suspicious_match_audit.csv` | Random spot-check sampler by bucket |
| `before_after_counts.csv` | Count summary for this run |
| `master_report.md` | Narrative + statistics report |
