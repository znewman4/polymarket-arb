# Recording

The recorder repeatedly runs existing public read-only CLOB/score paths and
writes append-only rows plus a run manifest. It does not use authentication,
private keys, wallets, signatures, or order placement.

## Commands

```bash
python -m polymarket_arb.cli record quotes --limit 100 --interval 10s --duration 1h
python -m polymarket_arb.cli record snapshots --limit 100 --interval 30s --duration 1h
python -m polymarket_arb.cli record scores --limit 100 --interval 60s --duration 1h
python -m polymarket_arb.cli record run --limit 100 --quote-interval 10s --score-interval 60s --duration 1h
```

For a tiny first run:

```bash
python -m polymarket_arb.cli record quotes --limit 5 --interval 10s --duration 30s
```

Durations support `10s`, `1m`, and `1h`. A duration of `0s` performs one
iteration, which is useful for tests.

## Manifest

Every run writes:

```text
data/runs/<run_id>/manifest.json
```

The manifest includes command arguments, intervals, row counts by table,
tables written, git code version when available, config hash, status, and an
error summary if the run fails. Manifests are append-only and are never
deleted by the recorder.
