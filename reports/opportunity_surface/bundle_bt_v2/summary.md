# Opportunity Surface Report — 2026-05-16T11:54:47Z

> RESEARCH-ONLY — diagnostic / exploratory. Not trading advice.
> run_id: `bundle_bt_v2`
> preset: `BUNDLE_BT_V2`

## Summary (ranked by trade count — PnL is secondary)

| Metric | Value |
| --- | --- |
| Relationships loaded | 0 |
| Price history present | 0 |
| Aligned price series | 0 |
| Gross violations | 12930 |
| Candidates accepted | 0 |
| **Simulated trades executed** | **16** |
| Distinct relationships traded | 0 |
| Distinct spaces traded | 3 |
| Credibility | `data_insufficient` |

## Top blockers (why opportunities were not accepted)

| Blocker | Count |
| --- | --- |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=1/?) | 3321 |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=7/?) | 3062 |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=8/?) | 2961 |
| net_edge_below_threshold | 1740 |
| already_open | 978 |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=19/?) | 548 |
| incomplete_bundle_buy_all_yes_blocked (completeness=unknown, observed=26/?) | 357 |

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
