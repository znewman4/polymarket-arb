# Manual Smoke Checklist

Run this from the repo root after implementation changes that touch ingestion,
NLP, rulebooks, CLOB, or fusion scoring.

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

## Expected Data

These should contain files after the smoke run:

```bash
find data/raw/gamma data/raw/nlp data/raw/clob -type f | head
find data/normalised/markets data/normalised/events -type f | head
find data/normalised/market_semantics data/normalised/market_embeddings -type f | head
find data/normalised/rulebook_evaluations data/normalised/market_implications -type f | head
find data/normalised/orderbook_snapshots data/normalised/best_quotes -type f | head
find data/normalised/market_scores -type f | head
```

## Safety Audits

Chain-of-thought must not be persisted:

```bash
grep -R "<think>" data/raw/nlp data/normalised 2>/dev/null | head
grep -R '"thinking"' data/raw/nlp data/normalised 2>/dev/null | head
```

There must be no live order placement path:

```bash
rg -n "private_key|place_order|OrderClient|wallet" src tests configs \
  -g '!**/__pycache__/**' -g '!*.pyc'
```

Expected result: only future-looking risk/preflight comments may mention
`OrderClient`; no CLOB ingestion or score command should use auth, wallets, or
order placement.
