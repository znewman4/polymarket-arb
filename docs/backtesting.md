# Backtesting

The backtest package replays local recorded Parquet data. It never calls live
network APIs and never places orders. Simulated orders and fills are local
research artifacts only.

## Run

```bash
python -m polymarket_arb.cli backtest run --strategy score-threshold --threshold 0.8
python -m polymarket_arb.cli backtest list
python -m polymarket_arb.cli backtest show <BACKTEST_RUN_ID>
python -m polymarket_arb.cli backtest metrics <BACKTEST_RUN_ID>
```

The first strategy is a toy `score-threshold` strategy. It listens for
`market_score` events and emits a research signal when `final_signal_score`
meets the configured threshold. This is a replay harness, not a claim of
predictive power.

## Outputs

Each run writes:

```text
data/backtests/<backtest_run_id>/
  config.json
  signals.parquet
  simulated_orders.parquet
  fills.parquet
  positions.parquet
  equity_curve.parquet
  metrics.json
```

## Execution Simulator

The simulator walks recorded orderbook depth:

- buys consume asks from lowest price upward
- sells consume bids from highest price downward
- limit prices are respected
- partial fills are marked
- fees are simulated from `fee_bps`
- no midpoint fills are used by default

Prices, sizes, cash, fees, and positions use `Decimal` where practical.

## Limitations

Final outcome PnL is not computed until reliable resolution data is available.
The current metrics focus on replay counts, generated signals, simulated fill
quality, fees, cash, and open positions.
