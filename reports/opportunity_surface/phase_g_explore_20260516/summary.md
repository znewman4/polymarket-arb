# Opportunity Surface Report — 2026-05-16T11:59:05Z

> RESEARCH-ONLY — diagnostic / exploratory. Not trading advice.
> run_id: `phase_g_explore_20260516`
> preset: `EXPLORATORY_TRADE_SURFACE`

## Summary (ranked by trade count — PnL is secondary)

| Metric | Value |
| --- | --- |
| Relationships loaded | 1729 |
| Price history present | 1729 |
| Aligned price series | 1729 |
| Gross violations | 6470 |
| Candidates accepted | 72 |
| **Simulated trades executed** | **72** |
| Distinct relationships traded | 4 |
| Distinct spaces traded | 0 |
| Credibility | `exploratory_only_not_credible` |

## Simulated PnL (SECONDARY — do not use as primary criterion)

Net PnL: **4519.64931223282660940280616 USDC** (simulated, research-only, credibility = `exploratory_only_not_credible`)

> PnL is reported for completeness only. This refactor's goal is > **trade count and coverage**, not profit optimisation.

## Top relationship families by gross violations

| strategy_family | subtype | relationships | gross_violations | traded |
| --- | --- | --- | --- | --- |
| inverse | same_topic_no_trade | 5987 | 5759 | 1 |
| nested_a_implies_b | championship_implies_conference | 483 | 97 | 3 |

## Top blockers (why opportunities were not accepted)

| Blocker | Count |
| --- | --- |
| max_trades | 5784 |

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
