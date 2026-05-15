# Live Trading Checklist

> **Status: not applicable.** No live trading is supported in any phase before
> Phase 10. This file is a placeholder. Do not populate it until Phase 10 is
> being scoped.

When the time comes, this file will track the explicit human-review steps
required to flip `orders_allowed: true`, including:

- VPS uptime and IP confirmation
- Strategy approval (`approved_strategies.yaml` entry + sha256 match)
- Backtest results URL and reviewer
- Paper-trading PnL evidence
- Tiny-stake limit (`max_stake_usdc <= 1.0`)
- Kill-switch tested
- Manual approval token issued and stored
