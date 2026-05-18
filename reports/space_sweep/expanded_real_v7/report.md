# Space sweep report — 2026-05-17T10:33:58Z

> RESEARCH-ONLY — diagnostic / exploratory.  Not trading advice.
> run_id: `expanded_real_v7`
> report_integrity: **ok**
> credibility: **exploratory_only_not_credible**

## Headline figures

| Metric | Value |
| --- | --- |
| Spaces analysed | 356 |
| Accepted simulated trades | 0 |
| Gross violations | 50000 |
| Net violations | 46349 |
| Distinct relationships traded | 0 |
| Simulated PnL (sum across spaces) | 0.00 USDC |
| Total deployed trade cost | 0.00 USDC |
| Total return on trade cost | 0.0000% |
| Diagnostic-only relationships (excluded from totals) | 117393 |

## Grade distribution

| Grade | Count |
| --- | --- |
| `B_PROMISING_GATE_BLOCKED` | 83 |
| `C_INFRASTRUCTURE_BLOCKED` | 5 |
| `D_VALID_BUT_STRATEGICALLY_WEAK` | 13 |
| `E_INVALID_OR_AUDIT_RISK` | 255 |

## Top spaces by accepted trade count

| space_id | grade | trades | distinct_rels | pnl | median_trade_return_pct | primary_blocker |
| --- | --- | --- | --- | --- | --- | --- |
| `07d506e253374f263e904d383a58bb1e` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `089caf0e270e445bb06e54a63b74917d` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `09710712af37ce0f20f1be516ee57b73` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `09d1c47f43ec1bb0f703ddcd94373d2d` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `0b6d07e1b4fc8bf6607e55ee1f49d88c` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `0dae4c7ee55662a5291baa962c3b7601` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `0e69f68f13a7e9e9ff06741e7b2da41d` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `0e7c37808cfd2f32e5291426bc4dc812` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `0e8631d60f527c74720931a440120cc7` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `0f1659e7a4a9343185d77ce9d62fc80b` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `0fc8b118d25cd06786dde98a9868ffd0` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `1303f6a7de164326ab7c0b8ecde5a186` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `164d4e28d713de23f133b38aafd460ca` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `168bbfbb9860c4778688598c0a786afd` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |
| `1834f6d8a0006738da37beb0d38cc163` | `B_PROMISING_GATE_BLOCKED` | 0 | 0 | 0.00 | 0.0000% | economic_or_replay |

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

## Integrity notes (first 10)

- trade_id=bfb30e594f9f4362aa33d5437b732d1b dropped: no strategy_family
- trade_id=d490571513e54f41acd78cd96f9855d0 dropped: no strategy_family
- trade_id=fdb8891e95594e4b84adf04446096c77 dropped: no strategy_family
- trade_id=71ea106e5c7f41dfa7534cad6a6af5cf dropped: no strategy_family
- trade_id=821fba0a887b4a5097c476b9a03cffdd dropped: no strategy_family
- trade_id=84885a48c9f94e7092864d59cdaa6dae dropped: no strategy_family
- trade_id=ad96aed5f2ca48fe8aa903bacb969557 dropped: no strategy_family
- trade_id=3c2e188372aa4f28ab51f52e40d9331f dropped: no strategy_family
- trade_id=fb0b86d2a9a144ac8f20d86c5db45df6 dropped: no strategy_family
- trade_id=07245f5bef19481199903c77accbcdf8 dropped: no strategy_family
