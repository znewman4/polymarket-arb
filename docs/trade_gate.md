# Trade Gate — Threat Model

## What the gate prevents

Any path from "code runs" to "real money moves on Polymarket" must pass through
`risk/preflight.py::PreflightGate.assert_can_trade(strategy, order)`. The gate
returns a `PreflightToken` that the (Phase 10) `OrderClient` requires in its
constructor and on every call. This means:

- An LLM that emits a fabricated `place_order` call cannot trade — it has no
  token.
- A strategy with a logic bug that calls `place_order(...)` directly cannot
  trade — same reason.
- A test that imports `OrderClient` and tries to invoke it will fail at
  construction unless it explicitly fakes a token (which a code review can
  spot).
- Flipping the `orders_allowed` flag to `true` is necessary but not sufficient;
  every other check must also pass.

## The 10 checks (current Phase status in brackets)

| # | Check | Implemented in |
|---|---|---|
| 1 | `VPSReachableCheck` | Phase 0 (localhost variant) |
| 2 | `EgressIPWhitelistCheck` | Phase 0 |
| 3 | `KillSwitchOffCheck` | Phase 0 |
| 4 | `OrdersAllowedFlagCheck` | Phase 0 |
| 5 | `PaperModeNotActiveCheck` | Phase 0 |
| 6 | `StrategyApprovedCheck` | Phase 0 (interface), enforced Phase 4 |
| 7 | `RiskLimitsCheck` | Phase 0 (interface), enforced Phase 4 |
| 8 | `BalanceAvailableCheck` | Phase 0 (interface), enforced Phase 7 |
| 9 | `OrderbookFreshnessCheck` | Phase 0 (interface), enforced Phase 3 |
| 10 | `ManualApprovalFlagCheck` | Phase 0 (interface), enforced Phase 10 |

Every check writes a `risk_snapshots` row regardless of whether it passed or
failed. The audit trail is therefore complete: any "near-trade" event leaves
a record of every gate that allowed it through.

## Anti-pattern

Do **not** wrap order placement in a try/except that suppresses
`PreflightFailure`. The exception is a feature, not a bug.

## Anti-pattern (LLM)

The AI layer (Phase 8) **never** receives an `OrderClient`. It receives a
read-only research interface. Its outputs are structured JSON proposals, which
go through deterministic validation before any human can authorise them.
