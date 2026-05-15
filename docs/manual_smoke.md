# Manual Smoke

Run this from the repo root after implementation changes that touch ingestion,
NLP, rulebooks, CLOB, fusion scoring, recording, or backtesting.

## Baseline

```bash
source .venv/bin/activate
python -m pytest -q
python -m polymarket_arb.cli healthcheck --skip-network
python -m polymarket_arb.cli healthcheck
```

## Tiny Real-Data Pipeline

```bash
python -m polymarket_arb.cli gamma fetch-markets --limit 20
python -m polymarket_arb.cli gamma list-markets --active --limit 10
python -m polymarket_arb.cli gamma search-markets bitcoin
python -m polymarket_arb.cli gamma show-market <MARKET_ID>

python -m polymarket_arb.cli nlp extract-market-semantics --limit 1 --provider mock
python -m polymarket_arb.cli nlp show-semantics <MARKET_ID>
python -m polymarket_arb.cli nlp embed-markets --limit 5

python -m polymarket_arb.cli nlp extract-market-semantics --limit 10 --provider ollama --rerun-stale
python -m polymarket_arb.cli nlp show-semantics <MARKET_ID>

python -m polymarket_arb.cli nlp score-semantics --limit 10 --all
python -m polymarket_arb.cli nlp extract-implications --limit 10
python -m polymarket_arb.cli nlp show-implications <MARKET_ID>

python -m polymarket_arb.cli clob fetch-quotes --limit 10
python -m polymarket_arb.cli clob fetch-orderbook <MARKET_ID>
python -m polymarket_arb.cli clob snapshot-active-markets --limit 10

python -m polymarket_arb.cli score score-markets --limit 10
python -m polymarket_arb.cli score show-score <MARKET_ID>
```

## Phase 4.5 / 5 Smoke

```bash
python -m polymarket_arb.cli inspect tables
python -m polymarket_arb.cli inspect counts
python -m polymarket_arb.cli inspect market <MARKET_ID>
python -m polymarket_arb.cli inspect pipeline <MARKET_ID>
python -m polymarket_arb.cli inspect score-distribution
python -m polymarket_arb.cli inspect export-semantics-review --sample 20 --out review.csv

python -m polymarket_arb.cli record quotes --limit 5 --interval 10s --duration 30s
python -m polymarket_arb.cli backtest run --strategy score-threshold --threshold 0.8
```

## Expected Data

These should contain files after the smoke run:

```bash
find data/raw/gamma data/raw/nlp data/raw/clob -type f | head
find data/normalised/markets data/normalised/events -type f | head
find data/normalised/market_semantics data/normalised/market_embeddings -type f | head
find data/normalised/rulebook_evaluations data/normalised/market_implications -type f | head
find data/normalised/orderbook_snapshots data/normalised/best_quotes -type f | head
find data/normalised/market_scores -type f | head
find data/runs data/backtests -type f | head
```

## Safety Audits

Chain-of-thought must not be persisted:

```bash
grep -R "<think>" data/raw/nlp data/normalised 2>/dev/null | head
grep -R '"thinking"' data/raw/nlp data/normalised 2>/dev/null | head
```

There must be no live order placement path:

```bash
rg -n "private_key|place_order|OrderClient|wallet|funder|signature|signer" \
  src tests configs -g '!**/__pycache__/**' -g '!*.pyc'
```

Only future-looking risk/preflight comments may mention execution. CLOB ingest,
recording, scoring, and backtesting must remain no-auth and local/public-data
only.
