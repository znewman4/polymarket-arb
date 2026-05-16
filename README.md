# polymarket-arb

Local-first Polymarket semantic research platform.

This repo ingests public market data, formalises market wording into validated
semantics, applies deterministic YAML rulebooks, stores append-only Parquet
tables, and produces research-only scores. **No live trading. No private-key
logic. No wallets. No order placement of any kind.**

## Quickstart

```bash
make install
make test
make healthcheck
```

`healthcheck` runs offline checks (settings load, kill-switch off, data dirs
writable, storage round-trip) followed by a network probe of the public Gamma
and CLOB read endpoints. Public read probes may pass from the UK; trading/order
placement remains disabled and outside this repo's implemented scope.

## Implemented Phase Map

```text
Phase 0   safety/storage/repo foundation
Phase 1   Gamma market/event ingestion
Phase 1.5 local NLP semantic extraction with Ollama/DeepSeek or mock clients
Phase 1.6 deterministic YAML rulebook scoring
Phase 2   single-market implication extraction
Phase 3   public read-only CLOB orderbook/quote ingestion
Phase 4   weighted research-only fusion scoring
Phase 4.5 data inspection, audit, review exports, REST recording
Phase 5   offline backtest/replay foundation
```

## Current Research Status

This project is research-only. The current context-aware audit state is:

- Manual context rules: 7 approved rules, with invalidating rules still
  analysis-only.
- Relationship coverage: all 5,214 relationship pairs have both price
  histories; no stale coverage rows remain in the latest audit.
- NBA Finals -> Conference rows are normalized as sports-progression nesting:
  `P(championship) <= P(conference)`.
- Latest reviewed low-confidence context-aware run:
  `reviewed_context_low_conf`.
- Latest reviewed result: 2 simulated non-diagnostic trade pairs, +$1,375.26
  simulated PnL, $0.00 drawdown, $5.00 slippage.
- Latest null baseline: 0 trades, $0.00 PnL.
- Latest slim sensitivity: 24 cells, 7 positive cells.
- Final credibility label: `data_insufficient`, because fewer than 30 reviewed
  non-diagnostic trade pairs executed.
- Diagnostic comparison remains `diagnostic_only_not_credible` and must not be
  used as credible positive evidence.

Latest local reports:

```text
reports/master_audit/latest/index.html
reports/master_audit/latest/master_audit.csv
reports/master_audit/latest/all_source_rows.csv
reports/master_audit/latest/nba_finals_conference_audit.csv
reports/context_strategy_backtests/reviewed_context_low_conf/index.html
```

Next required research work is to increase reviewed deterministic sample size
without promoting non-deterministic pairs. Good candidates are more sports
progression, EPL exact-finish -> top-N, complete balance-of-power spaces, and
threshold nesting. Pairs such as Champions League vs domestic league winners or
endorsement markets should stay research-only unless their market terms prove a
deterministic relationship.

## CLI

Canonical commands use subgroups; flat aliases are also registered for the
same callbacks.

```bash
polymarket-arb gamma fetch-markets
polymarket-arb gamma list-markets --active --limit 10
polymarket-arb nlp extract-market-semantics --limit 1 --provider mock
polymarket-arb nlp score-semantics --limit 5
polymarket-arb nlp extract-implications --limit 5
polymarket-arb clob fetch-quotes --limit 20
polymarket-arb score score-markets --limit 20
polymarket-arb inspect counts
polymarket-arb record quotes --limit 5 --interval 10s --duration 30s
polymarket-arb backtest run --strategy score-threshold --threshold 0.8
```

## Repo Map

```
configs/           dev.yaml, approved_strategies.yaml, semantic_rules/*.yaml
data/              raw/ normalised/ account/ derived/ logs/ debug/  (gitignored)
docs/              architecture, connectivity, storage, trade_gate, live_trading_checklist
src/polymarket_arb/
    cli/               Click command groups + flat aliases
    settings.py        Pydantic-settings - yaml + .env loader
    logging_setup.py   Loguru config (JSON to file, pretty to console)
    compliance/        geo_check (egress IP + country) + trade_gate
    http/              async httpx wrapper w/ retry + token-bucket rate limit
    ingest/            Gamma + public CLOB read-only ingestion
    nlp/               Ollama/mock extraction, schemas, prompts, embeddings
    semantics/         YAML rulebook loading + deterministic scorers
    inspect/           local lake inspection, audit, CSV review exports
    record/            public REST recording loops + run manifests
    backtest/          offline replay, simulated fills, metrics
    fusion/            research-only weighted scoring
    storage/           DTOs, parquet repos, DuckDB views
    risk/              preflight gate + 10 checks
    monitoring/        kill_switch
tests/             mirror of src/ — pytest + respx + tmp_path fixtures
```

## Trade gate (read this before flipping any flag)

`POLYMARKET_ARB_ORDERS_ALLOWED` defaults to `false`. This repository currently
has no order client and no authenticated trading path. **Do not edit the
default in code.** If execution is ever added in a later project, it must remain
behind the preflight gate and a separate signed deploy config.

See [docs/trade_gate.md](docs/trade_gate.md) for the threat model and
[docs/connectivity.md](docs/connectivity.md) for the VPS/UK/VPN runbook.
See [docs/manual_smoke.md](docs/manual_smoke.md) for the tiny real-data
validation sequence, [docs/data_inspection.md](docs/data_inspection.md) for
local audits, [docs/recording.md](docs/recording.md) for REST recording, and
[docs/backtesting.md](docs/backtesting.md) for offline replay.
