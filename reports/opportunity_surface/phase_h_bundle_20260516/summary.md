# Opportunity Surface Report — 2026-05-16T12:04:05Z

> RESEARCH-ONLY — diagnostic / exploratory. Not trading advice.
> run_id: `phase_h_bundle_20260516`
> preset: `EXPLORATORY_BUNDLE_AFTER_EXPANSION`

## Summary (ranked by trade count — PnL is secondary)

| Metric | Value |
| --- | --- |
| Relationships loaded | 0 |
| Price history present | 0 |
| Aligned price series | 0 |
| Gross violations | 12450 |
| Candidates accepted | 0 |
| **Simulated trades executed** | **1092** |
| Distinct relationships traded | 0 |
| Distinct spaces traded | 4 |
| Credibility | `inconclusive` |

## Simulated PnL (SECONDARY — do not use as primary criterion)

Net PnL: **36.98094513889345901137716 USDC** (simulated, research-only, credibility = `inconclusive`)

> PnL is reported for completeness only. This refactor's goal is > **trade count and coverage**, not profit optimisation.

## Top blockers (why opportunities were not accepted)

| Blocker | Count |
| --- | --- |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=7/?) | 3067 |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=8/?) | 2985 |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=2/?) | 2739 |
| net_edge_below_threshold | 2709 |

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
