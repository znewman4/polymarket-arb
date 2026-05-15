# Data Inspection

Inspection commands are local-only. They read DuckDB/Parquet state and never hit
Gamma, CLOB, Ollama, or any live execution path.

```bash
python -m polymarket_arb.cli inspect tables
python -m polymarket_arb.cli inspect counts
python -m polymarket_arb.cli inspect market <MARKET_ID>
python -m polymarket_arb.cli inspect pipeline <MARKET_ID>
python -m polymarket_arb.cli inspect freshness
python -m polymarket_arb.cli inspect audit-data
```

`inspect tables` reports table paths, parquet file counts, approximate row
counts, and latest ingestion timestamps.

`inspect counts` summarizes coverage: markets with no semantics, no rulebook
evaluation, no implications, no CLOB quote, and no market score.

`inspect market` gives a compact market-centered view: Gamma fields, semantics,
implication count, latest quotes, and fusion score.

`inspect pipeline` shows the stage-by-stage status for one market and suggests
the next command for missing stages.

`inspect audit-data` emits PASS/WARN/FAIL checks for data presence, coverage,
chain-of-thought persistence, and obvious live-trading code paths.

## Review Export

```bash
python -m polymarket_arb.cli inspect export-semantics-review \
  --sample 100 \
  --only-review-needed \
  --sort ambiguity_score_desc \
  --out review.csv
```

The CSV includes structured explanations, rationales, uncertainty notes,
ambiguity scores, and latest market score. It does not include DeepSeek
thinking or raw chain-of-thought.

## Score Distribution

```bash
python -m polymarket_arb.cli inspect score-distribution --top 20
```

This reports score count, min/max/mean/median, recommendation bucket counts,
top scores, highest ambiguity markets, lowest liquidity-score markets, and
scores affected by stale quote freshness.
