% =============================================================================
# POLYMARKET ARBITRAGE & PROBABILISTIC INEFFICIENCY SYSTEM
% =============================================================================

Project Proposal
================

Build an in-house Polymarket research, backtesting, and execution system that identifies probabilistic inefficiencies across prediction markets.

The project should begin as a **data and research platform**, then evolve into a **paper-trading engine**, and only later become a **live execution bot** with strict risk controls.

Core principle:

> AI models can assist research, market parsing, relationship discovery, and probability estimation, but deterministic validation and risk controls must decide whether anything is tradeable.

---

% =============================================================================
## 1. CORE SYSTEM OVERVIEW
% =============================================================================

```text
Polymarket Gamma API / CLOB API / WebSockets / Data API
        ↓
Raw data ingestion + immutable storage
        ↓
Market parser + resolution-rule extractor
        ↓
Market relationship graph
        ↓
Strategy engines
        ├── hard arbitrage scanner
        ├── threshold ladder scanner
        ├── latency/staleness engine
        ├── cross-market probability model
        └── AI-assisted research agent
        ↓
Depth-aware opportunity optimiser
        ↓
Backtest / replay / paper trading layer
        ↓
Risk gate
        ↓
Execution router
        ↓
Order reconciliation + monitoring + audit logs
```

The system should be built around three different types of opportunity:

| Opportunity Type | Meaning | Example |
|---|---|---|
| **Hard logical arbitrage** | Prices violate a formal relationship | Exhaustive outcomes cost less than $1 total |
| **Soft probabilistic edge** | Our model estimates a different probability from the market | Market says 42%, model says 55% |
| **Latency / information edge** | Market is stale relative to external information | BTC moves sharply but a 5-minute market has not updated |

---

% =============================================================================
## 2. DATA INGESTION LAYERS
% =============================================================================

The ingestion system should be modular. Each data source should have:

- a connector,
- schema validation,
- raw-data storage,
- normalised-data storage,
- retry and rate-limit handling,
- ingestion timestamps,
- monitoring and health checks.

---

### 2.1 Polymarket Gamma API Layer
==================================

**Purpose:**  
Discover events, markets, descriptions, resolution criteria, tags, end dates, outcomes, token IDs, liquidity, and market status.

**Data to collect:**

- Event ID
- Market ID
- Market question
- Market description
- Resolution source / rules
- Event slug
- Market slug
- Category / tags
- Start date
- End date
- Active / closed / archived flags
- Outcome names
- CLOB token IDs
- Initial outcome prices
- Volume / liquidity
- Related markets inside the same event

**Why this matters:**

The Gamma layer is the source of truth for market discovery. It is also where we begin identifying related markets that could form arbitrage constraints.

**Planned module:**

```text
src/polymarket_arb/ingest/gamma.py
```

**Outputs:**

```text
data/raw/gamma/events/YYYY-MM-DD/*.json
data/raw/gamma/markets/YYYY-MM-DD/*.json
data/normalised/markets.parquet
data/normalised/events.parquet
```

---

### 2.2 Polymarket CLOB REST Layer
=================================

**Purpose:**  
Fetch executable orderbook data, prices, spreads, tick sizes, fee rates, and market-specific trading parameters.

**Data to collect:**

- Full orderbook snapshots
- Best bid / ask
- Spread
- Midpoint
- Tick size
- Fee rate
- Neg-risk flag
- Last trade price
- Market liquidity at each price level
- Token-level metadata

**Why this matters:**

Arbitrage must be calculated from **executable bids, asks, and depth**, not midpoint prices.

A market can look mispriced at the midpoint but not be tradeable once real orderbook depth, slippage, and fees are included.

**Planned module:**

```text
src/polymarket_arb/ingest/clob_rest.py
```

**Outputs:**

```text
data/raw/clob/orderbooks/YYYY-MM-DD/*.json
data/normalised/orderbook_snapshots.parquet
data/normalised/best_quotes.parquet
```

---

### 2.3 Polymarket CLOB WebSocket Layer
======================================

**Purpose:**  
Maintain real-time orderbook state and detect fast-moving opportunities.

**Data to collect:**

- Orderbook deltas
- Price updates
- Trade prints
- User order updates
- User trade fills
- Disconnection/reconnection events
- Local orderbook reconstruction state

**Why this matters:**

Hard arbitrage and latency strategies require fresh orderbook data. The system must know whether a quote is 100 ms old or 10 seconds old.

**Planned module:**

```text
src/polymarket_arb/ingest/clob_ws.py
```

**Outputs:**

```text
data/raw/ws/messages/YYYY-MM-DD/*.jsonl
data/normalised/orderbook_deltas.parquet
data/normalised/trade_prints.parquet
```

**Implementation notes:**

- Keep a local reconstructed orderbook per token ID.
- Validate periodic snapshots against REST orderbook.
- Mark orderbooks as stale after a configurable timeout.
- Strategy engines must refuse to trade stale books.

---

### 2.4 Polymarket Data API Layer
================================

**Purpose:**  
Track positions, fills, trades, leaderboards, wallet activity, and PnL.

**Data to collect:**

- Current positions
- Historical trades
- Open orders
- Closed orders
- Fill prices
- Average entry price
- Realised PnL
- Unrealised PnL
- Wallet-level activity
- Public trader activity if used for smart-money analysis

**Why this matters:**

A strategy cannot be trusted unless every order, fill, cancellation, and position is reconciled.

**Planned module:**

```text
src/polymarket_arb/ingest/data_api.py
```

**Outputs:**

```text
data/account/orders.parquet
data/account/fills.parquet
data/account/positions.parquet
data/account/pnl.parquet
```

---

### 2.5 External Market Data Layer
=================================

**Purpose:**  
Pull external data that can imply probabilities before Polymarket fully updates.

---

#### Crypto Feeds
-----------------

Useful for 5-minute / 15-minute BTC, ETH, SOL markets.

Potential sources:

- Binance WebSocket
- Coinbase WebSocket
- Kraken
- Deribit options data
- Funding rates
- Realised volatility
- Orderbook imbalance

**Planned modules:**

```text
src/polymarket_arb/ingest/external/binance.py
src/polymarket_arb/ingest/external/coinbase.py
src/polymarket_arb/ingest/external/deribit.py
```

---

#### News Feeds
---------------

Useful for politics, macro, companies, sports, geopolitics.

Potential sources:

- RSS feeds
- GDELT
- Event Registry
- Official government feeds
- Official sports injury reports
- Official weather APIs
- Polling aggregators

**Planned modules:**

```text
src/polymarket_arb/ingest/external/news.py
src/polymarket_arb/ingest/external/rss.py
src/polymarket_arb/ingest/external/gdelt.py
```

---

#### Official-Data Feeds
------------------------

Useful for hard-resolution markets.

Examples:

- election result sources,
- weather station readings,
- sports result APIs,
- government statistics releases,
- central bank calendars,
- court docket updates.

**Planned module:**

```text
src/polymarket_arb/ingest/external/official_sources.py
```

---

### 2.6 Raw Data Lake
====================

Every API response should be stored raw before normalisation.

**Reason:**

- Allows reprocessing if schemas change.
- Provides an audit trail.
- Supports replay and backtesting.
- Prevents silent data corruption.

**Suggested structure:**

```text
data/
├── raw/
│   ├── gamma/
│   ├── clob_rest/
│   ├── clob_ws/
│   ├── data_api/
│   └── external/
├── normalised/
│   ├── markets.parquet
│   ├── events.parquet
│   ├── orderbook_snapshots.parquet
│   ├── orderbook_deltas.parquet
│   ├── trades.parquet
│   └── external_signals.parquet
├── account/
│   ├── orders.parquet
│   ├── fills.parquet
│   ├── positions.parquet
│   └── pnl.parquet
└── derived/
    ├── market_graph.parquet
    ├── constraints.parquet
    ├── opportunities.parquet
    └── strategy_signals.parquet
```

---

% =============================================================================
## 3. USEFUL RESEARCH PAPERS
% =============================================================================

### 3.1 Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets
============================================================================

**Link:**  
https://ideas.repec.org/p/arx/papers/2508.03474.html

**Direct archive / PDF source:**  
https://dspace.networks.imdea.org/handle/20.500.12761/1941?locale-attribute=en

**Why useful:**

This is the most directly relevant paper. It studies arbitrage on Polymarket through mutually exclusive and exhaustive condition sets.

**Techniques to copy:**

- Treat markets as probability constraints.
- Search for condition sets that should sum to $1.
- Detect underpriced or overpriced outcome bundles.
- Build a market relationship graph.
- Use heuristics to reduce the search space.

**Implementation idea:**

```text
src/polymarket_arb/graph/constraints.py
src/polymarket_arb/strategies/hard_arb.py
```

---

### 3.2 PolySwarm: Multi-Agent LLM Framework for Prediction Market Trading
=========================================================================

**Link:**  
https://ideas.repec.org/p/arx/papers/2604.03888.html

**arXiv PDF:**  
http://arxiv.org/pdf/2604.03888

**Why useful:**

This provides a blueprint for using multiple AI agents to estimate probabilities, aggregate confidence, detect cross-market inefficiencies, and trade stale prices.

**Techniques to copy carefully:**

- Multi-agent probability estimates.
- Confidence-weighted aggregation.
- Bayesian combination with market-implied probabilities.
- KL divergence / Jensen-Shannon divergence for detecting pricing disagreement.
- Fractional Kelly sizing.
- Explicit discussion of hallucination and regulatory risk.

**Implementation idea:**

```text
src/polymarket_arb/ai/agent_pool.py
src/polymarket_arb/ai/probability_aggregator.py
src/polymarket_arb/strategies/ai_probability_edge.py
```

---

### 3.3 PredictionMarketBench
============================

**Paper:**  
https://ideas.repec.org/p/arx/papers/2602.00133.html

**GitHub:**  
https://github.com/Oddpool/PredictionMarketBench

**Why useful:**

This is useful for the replay and backtesting side of the project. It provides a benchmark pattern for replaying real prediction-market orderbooks and testing agents under execution constraints.

**Techniques to copy:**

- Event-driven replay engine.
- Agent interface.
- Orderbook snapshots and trade prints.
- Maker/taker simulation.
- Fee modelling.
- Equity curve output.
- Trade logs.
- PnL visualisation.

**Implementation idea:**

```text
src/polymarket_arb/backtest/replay_engine.py
src/polymarket_arb/backtest/execution_sim.py
src/polymarket_arb/backtest/agent_interface.py
```

---

### 3.4 Exploring Decentralized Prediction Markets: Accuracy, Skill, and Bias on Polymarket
=========================================================================================

**Link:**  
https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5910522

**Why useful:**

This is useful because it reminds us that Polymarket is not simply “wrong”. Many markets are efficient, and profits are likely concentrated among sophisticated traders.

**Techniques to copy:**

- Calibration analysis.
- Brier scores.
- Bias detection.
- Category-level market efficiency analysis.
- Trader-skill analysis.

**Implementation idea:**

```text
src/polymarket_arb/research/calibration.py
src/polymarket_arb/research/bias_detection.py
```

---

% =============================================================================
## 4. USEFUL GITHUB REPOSITORIES
% =============================================================================

### 4.1 Polymarket Python CLOB Client
===================================

**Repo:**  
https://github.com/Polymarket/py-clob-client

**Use for:**

- Python prototype.
- Reading prices and orderbooks.
- Posting orders.
- Cancelling orders.
- Managing API credentials.
- Fetching user trades.

**What to steal:**

- Basic client setup.
- Order creation examples.
- Market order and limit order patterns.
- Token allowance handling.
- API credential flow.

**Where it fits:**

```text
src/polymarket_arb/execution/polymarket_py_client.py
```

---

### 4.2 Polymarket TypeScript CLOB Client
========================================

**Repo:**  
https://github.com/Polymarket/clob-client

**Use for:**

- TypeScript / Bun / Node execution service.
- Fast API integration.
- Cleaner web dashboard integration.

**What to steal:**

- Typed order structures.
- Error handling patterns.
- Signer/funder setup.
- API authentication examples.

**Where it fits:**

```text
services/execution-ts/
```

---

### 4.3 Polymarket Rust CLOB Client
==================================

**Repo:**  
https://github.com/Polymarket/rs-clob-client

**Use for:**

- Serious production execution later.
- WebSocket streaming.
- Low-latency typed order routing.
- Safer authenticated state transitions.
- Heartbeats.
- Data API and Gamma API integration.

**What to steal:**

- WebSocket orderbook structure.
- Authenticated order/trade stream handling.
- Heartbeat / cancel-on-disconnect logic.
- Strong typing for execution-critical code.

**Where it fits:**

```text
services/execution-rs/
```

---

### 4.4 Polymarket Agents
========================

**Repo:**  
https://github.com/Polymarket/agents

**Use for:**

- AI-assisted market research.
- RAG over market descriptions/news.
- Market filtering.
- Agent-style workflows.

**What to steal:**

- Gamma API wrapper.
- Market/event object models.
- RAG architecture.
- CLI structure.
- Agent separation from execution.

**Do not blindly copy:**

- Fully autonomous AI trading flow.
- Recursive retry logic without limits.
- Any live execution without a deterministic risk gate.

**Where it fits:**

```text
src/polymarket_arb/ai/
src/polymarket_arb/research/
```

---

### 4.5 PredictionMarketBench
============================

**Repo:**  
https://github.com/Oddpool/PredictionMarketBench

**Use for:**

- Backtesting and replay architecture.
- Agent interface.
- Maker/taker simulation.
- Equity curve and trade output.

**What to steal:**

- `episodes/` data format.
- `Agent` / `AgentContext` abstraction.
- Replay loop structure.
- Trade log format.
- Metrics output.

**Where it fits:**

```text
src/polymarket_arb/backtest/
```

---

### 4.6 BTC 15-Minute Polymarket Trading Bot
===========================================

**Repo:**  
https://github.com/aulekator/Polymarket-BTC-15-Minute-Trading-Bot

**Use for:**

- System structure inspiration.
- Ingestion / strategy / execution / monitoring split.
- Redis mode switching.
- Grafana monitoring.
- Signal fusion architecture.

**What to steal conceptually:**

```text
core/
data_sources/
execution/
monitoring/
feedback/
grafana/
redis_control.py
view_paper_trades.py
```

**Caution:**

This is more of a directional BTC bot than a pure arbitrage system. Use the infrastructure ideas, not necessarily the strategy logic.

---

### 4.7 ProbablyProfit
=====================

**Repo:**  
https://github.com/randomness11/probablyprofit

**PyPI:**  
https://pypi.org/project/probablyprofit/

**Use for:**

- AI-agent trading framework ideas.
- Natural-language strategy definitions.
- Paper trading.
- Risk limits.
- Order lifecycle management.
- Kill switch.
- Dashboard / CLI design.

**What to steal conceptually:**

- `RiskManager`
- `OrderManager`
- paper/live mode separation
- emergency stop
- preflight checks
- dashboard
- strategy prompt files

**Caution:**

Do not allow LLM text to directly trigger live orders. LLM output must be converted into structured probability estimates, then passed through deterministic validators.

---

### 4.8 Third-Party Polymarket Arbitrage Bot Repos
=================================================

**Example repo:**  
https://github.com/Bolymarket/Polymarket-arbitrage-trading-bot-python

**Use for:**

- Strategy naming ideas.
- Understanding what public bot sellers claim to do.
- Extracting possible categories:
  - 101-cent bot,
  - ladder bot,
  - sticky bot,
  - endcycle sniper,
  - lost-token sniper.

**Caution:**

Treat strong claims like “never loses” as marketing. Do not copy unsafe code or send credentials to unknown tools.

---

% =============================================================================
## 5. FIVE INITIAL STRATEGIES
% =============================================================================

### Strategy 1 — Hard Constraint Arbitrage
=========================================

**Concept:**

Find sets of markets/outcomes that should sum to exactly $1 because they are mutually exclusive and exhaustive.

Example:

```text
Candidate A wins election
Candidate B wins election
Candidate C wins election
Other wins election
```

If the executable cost of buying all outcomes is less than $1 after fees/slippage, buy the full basket.

**Signal:**

```text
sum(best_ask_i for all exhaustive outcomes) < 1 - fees - safety_margin
```

**Trade type:**

- Hard arbitrage.
- Prefer FOK/IOC-style execution.
- Must avoid partial-fill risk.

**Required modules:**

```text
src/polymarket_arb/graph/constraint_builder.py
src/polymarket_arb/strategies/hard_constraint_arb.py
src/polymarket_arb/optimisation/basket_solver.py
```

**Risks:**

- Bad market grouping.
- Non-exhaustive outcome set.
- Ambiguous resolution criteria.
- Stale orderbook.
- Partial fill.

---

### Strategy 2 — Threshold Ladder Arbitrage
==========================================

**Concept:**

Some markets form monotonic ladders.

Example:

```text
BTC > 80k
BTC > 90k
BTC > 100k
BTC > 110k
```

The probability of a more extreme threshold should never be higher than the probability of an easier threshold.

**Constraint:**

```text
P(BTC > 110k) <= P(BTC > 100k) <= P(BTC > 90k) <= P(BTC > 80k)
```

**Signal:**

Flag when the market violates monotonicity using executable bid/ask prices.

**Trade type:**

- Relative value.
- Sometimes hard-ish arbitrage if all legs are clean.
- Often better as a mispricing signal than a guaranteed arb.

**Required modules:**

```text
src/polymarket_arb/parsing/threshold_parser.py
src/polymarket_arb/graph/ladder_builder.py
src/polymarket_arb/strategies/threshold_ladder_arb.py
```

**Markets to target:**

- Crypto price thresholds.
- Temperature thresholds.
- Election vote-share thresholds.
- Sports score thresholds.
- Inflation / interest-rate thresholds.

---

### Strategy 3 — Latency/Staleness Crypto Engine
===============================================

**Concept:**

For short-duration crypto markets, Polymarket prices may lag centralised exchange prices.

Example:

```text
BTC 5-minute market: "Will BTC close above $X?"
```

If Binance/Coinbase price moves rapidly, we can estimate the probability of the market resolving YES before Polymarket updates fully.

**Signal inputs:**

- spot price,
- target price,
- time to expiry,
- realised volatility,
- short-horizon drift,
- orderbook depth,
- Polymarket stale quote age.

**Simplified probability model:**

```text
P(close_above_target) = model(spot, target, time_remaining, realised_volatility)
```

Trade if:

```text
model_probability - executable_market_price > required_edge
```

**Trade type:**

- Soft probabilistic edge.
- Very latency-sensitive.
- Requires strict stale-data guards.

**Required modules:**

```text
src/polymarket_arb/ingest/external/binance.py
src/polymarket_arb/models/crypto_probability.py
src/polymarket_arb/strategies/crypto_latency.py
```

**Risks:**

- Thin books.
- Fast reversals.
- Queue position.
- Slippage.
- Trading into faster bots.
- Incorrect expiry/settlement logic.

---

### Strategy 4 — Resolution Wording Arbitrage
============================================

**Concept:**

Two markets can look similar but resolve differently.

Example:

```text
"Will X be announced by June?"
vs
"Will X happen by June?"
```

The market may price them almost equally even though they have different real-world probabilities.

**Signal:**

Use AI/NLP to detect:

- different resolution source,
- different date boundary,
- “announced” vs “implemented”,
- “will happen” vs “will be confirmed”,
- subjective wording,
- different oracle/source,
- different timezone.

**Trade type:**

- Soft edge.
- Not hard arbitrage.
- Useful for flagging markets where humans underprice wording differences.

**Required modules:**

```text
src/polymarket_arb/ai/resolution_parser.py
src/polymarket_arb/parsing/date_logic.py
src/polymarket_arb/strategies/resolution_wording_edge.py
```

**AI role:**

The AI proposes the semantic difference.  
The deterministic system decides whether the difference is tradeable.

---

### Strategy 5 — Market Creation / New Listing Scanner
=====================================================

**Concept:**

Newly listed markets may be mispriced before liquidity, bots, and related-market traders catch up.

**Pipeline:**

```text
new market detected
→ parse market wording
→ find related existing markets
→ build candidate constraints
→ validate relationship
→ scan orderbook depth
→ trade or alert
```

**Signal:**

Trade only when:

```text
new_market_price strongly violates existing related market structure
```

**Trade type:**

- Can be hard arbitrage if relationship is deterministic.
- Often soft relative-value trade.

**Required modules:**

```text
src/polymarket_arb/ingest/gamma_watch.py
src/polymarket_arb/graph/relationship_candidates.py
src/polymarket_arb/strategies/new_market_scanner.py
```

**Risks:**

- Bad AI-suggested market relationships.
- Low liquidity.
- Wide spreads.
- Resolution wording mismatch.
- New market metadata changing after creation.

---

% =============================================================================
## 6. OPEN-SOURCE AI INTEGRATION
% =============================================================================

### 6.1 Important Legal and Security Note
========================================

Do **not** build this repo around leaked proprietary model weights, including any allegedly leaked Claude model.

Reasons:

- It may be illegal or against licence terms.
- It makes the repo impossible to publish safely.
- It creates security and supply-chain risks.
- It may contaminate the project academically or commercially.
- It is unnecessary because strong legal open-weight models exist.

Instead, use legitimate open-weight or open-source-compatible models.

---

### 6.2 Recommended AI Model Options
===================================

#### Option A — DeepSeek-R1
---------------------------

**Model link:**  
https://huggingface.co/deepseek-ai/DeepSeek-R1

**Use case:**

- reasoning-heavy market analysis,
- probability explanation,
- relationship discovery,
- resolution-rule interpretation.

**Pros:**

- Strong reasoning.
- Open weights.
- Can be self-hosted.
- Good for internal research summaries.

**Cons:**

- Full model is large.
- Requires serious GPU infrastructure unless using distilled/quantised variants.
- Needs guardrails to avoid hallucinated market relationships.

---

#### Option B — DeepSeek-R1 Distilled Models
--------------------------------------------

Useful for local or cheaper inference.

Candidate models:

- DeepSeek-R1-Distill-Qwen-1.5B
- DeepSeek-R1-Distill-Qwen-7B
- DeepSeek-R1-Distill-Qwen-14B
- DeepSeek-R1-Distill-Qwen-32B

**Use in this project:**

- Market description summarisation.
- Resolution-rule extraction.
- Candidate relationship discovery.
- Local research assistant.
- Cheap first-pass market filtering.

---

#### Option C — Qwen Open Models
--------------------------------

**Model hub:**  
https://huggingface.co/Qwen

**Use case:**

- structured extraction,
- JSON generation,
- market classification,
- cheaper local agents.

**Where useful:**

```text
src/polymarket_arb/ai/local_llm.py
src/polymarket_arb/ai/extractors.py
```

---

#### Option D — Mistral Open Models
-----------------------------------

**Model weights/docs:**  
https://docs.mistral.ai/getting-started/models/weights/

**Use case:**

- fast local inference,
- RAG,
- market summarisation,
- lightweight agent workers.

---

### 6.3 AI Architecture
======================

Do not build a single “AI decides trade” module.

Use AI in layers:

```text
AI Layer 1: Market parser
    ↓
Extract structured fields from market text.

AI Layer 2: Relationship proposer
    ↓
Suggest that Market A implies Market B, or that a set is exhaustive.

AI Layer 3: Research assistant
    ↓
Summarise external sources and produce probability estimates.

AI Layer 4: Probability ensemble
    ↓
Combine model estimates, market price, historical priors, and external data.

AI Layer 5: Explanation generator
    ↓
Explain why a signal was accepted or rejected.
```

The AI should output structured JSON only:

```json
{
  "market_id": "example",
  "relationship_type": "threshold_ladder",
  "related_market_ids": ["id_1", "id_2"],
  "confidence": 0.82,
  "reasoning_summary": "Market A has a stricter threshold than Market B.",
  "trade_permission": false
}
```

The AI should **never** output direct executable orders.

---

### 6.4 AI Safety Gates
======================

Before any AI-derived relationship can be traded:

1. Relationship must be converted into a formal constraint.
2. Constraint must pass deterministic validation.
3. Resolution criteria must be checked.
4. Market group must be human-reviewed at first.
5. Backtest must show positive expectancy.
6. Live trading must begin in alert-only mode.
7. Risk engine must approve final order.

---

% =============================================================================
## 7. PROPOSED REPOSITORY STRUCTURE
% =============================================================================

```text
polymarket-arb/
├── README.md
├── pyproject.toml
├── .env.example
├── docker-compose.yml
├── configs/
│   ├── dev.yaml
│   ├── paper.yaml
│   ├── live_tiny.yaml
│   └── strategies.yaml
├── data/
│   ├── raw/
│   ├── normalised/
│   ├── account/
│   └── derived/
├── notebooks/
│   ├── 01_market_discovery.ipynb
│   ├── 02_orderbook_depth.ipynb
│   ├── 03_constraint_graph.ipynb
│   └── 04_strategy_backtest.ipynb
├── src/
│   └── polymarket_arb/
│       ├── ingest/
│       │   ├── gamma.py
│       │   ├── clob_rest.py
│       │   ├── clob_ws.py
│       │   ├── data_api.py
│       │   └── external/
│       │       ├── binance.py
│       │       ├── coinbase.py
│       │       ├── news.py
│       │       └── official_sources.py
│       ├── storage/
│       │   ├── raw_writer.py
│       │   ├── parquet_store.py
│       │   └── database.py
│       ├── parsing/
│       │   ├── market_parser.py
│       │   ├── date_logic.py
│       │   ├── threshold_parser.py
│       │   └── resolution_parser.py
│       ├── graph/
│       │   ├── market_graph.py
│       │   ├── relationship_candidates.py
│       │   ├── validators.py
│       │   └── constraints.py
│       ├── strategies/
│       │   ├── hard_constraint_arb.py
│       │   ├── threshold_ladder_arb.py
│       │   ├── crypto_latency.py
│       │   ├── resolution_wording_edge.py
│       │   └── new_market_scanner.py
│       ├── models/
│       │   ├── crypto_probability.py
│       │   ├── calibration.py
│       │   └── kelly.py
│       ├── optimisation/
│       │   ├── basket_solver.py
│       │   ├── depth_solver.py
│       │   └── portfolio_allocator.py
│       ├── ai/
│       │   ├── local_llm.py
│       │   ├── agent_pool.py
│       │   ├── probability_aggregator.py
│       │   ├── extractors.py
│       │   └── prompts/
│       ├── backtest/
│       │   ├── replay_engine.py
│       │   ├── execution_sim.py
│       │   ├── agent_interface.py
│       │   └── metrics.py
│       ├── execution/
│       │   ├── polymarket_client.py
│       │   ├── order_router.py
│       │   ├── order_types.py
│       │   ├── reconciliation.py
│       │   └── kill_switch.py
│       ├── risk/
│       │   ├── limits.py
│       │   ├── sizing.py
│       │   ├── compliance_gate.py
│       │   └── stale_data_guard.py
│       ├── monitoring/
│       │   ├── metrics.py
│       │   ├── dashboard.py
│       │   ├── alerts.py
│       │   └── logs.py
│       └── cli.py
├── services/
│   ├── execution-rs/
│   ├── dashboard/
│   └── worker/
├── tests/
│   ├── test_parsing/
│   ├── test_graph/
│   ├── test_strategies/
│   ├── test_backtest/
│   └── test_execution/
└── docs/
    ├── architecture.md
    ├── data_schema.md
    ├── strategy_notes.md
    ├── risk_controls.md
    └── live_trading_checklist.md
```

---

% =============================================================================
## 8. PHASE MAP
% =============================================================================

### Phase 0 — Repo Foundation
============================

**Goal:**  
Create a clean skeleton repo with config, logging, testing, and data folders.

**Tasks:**

- Create Python package structure.
- Add `pyproject.toml`.
- Add `.env.example`.
- Add `configs/dev.yaml`.
- Add logging.
- Add CLI.
- Add test structure.
- Add Docker Compose for local services.

**Useful repos to steal from:**

- BTC bot folder split:  
  https://github.com/aulekator/Polymarket-BTC-15-Minute-Trading-Bot

- ProbablyProfit CLI / risk / paper-live separation:  
  https://github.com/randomness11/probablyprofit

**Deliverable:**

```text
python -m polymarket_arb.cli healthcheck
```

---

### Phase 1 — Polymarket Market Discovery
=========================================

**Goal:**  
Pull all active markets/events and store them.

**Tasks:**

- Implement Gamma API client.
- Store raw JSON.
- Build normalised market/event tables.
- Extract token IDs.
- Track active/closed/archived status.
- Add market search CLI.

**Useful sources:**

- Python CLOB client:  
  https://github.com/Polymarket/py-clob-client

- Polymarket Agents Gamma wrapper:  
  https://github.com/Polymarket/agents

**Deliverable:**

```text
python -m polymarket_arb.cli fetch-markets
python -m polymarket_arb.cli list-markets --query bitcoin
```

---

### Phase 2 — Orderbook Ingestion
=================================

**Goal:**  
Pull executable orderbook data and build local snapshots.

**Tasks:**

- Implement CLOB REST client wrapper.
- Fetch orderbook by token ID.
- Fetch best bid/ask.
- Fetch spreads.
- Fetch tick sizes.
- Store depth snapshots.
- Compute executable basket prices.

**Useful sources:**

- Python CLOB client:  
  https://github.com/Polymarket/py-clob-client

- Rust CLOB client:  
  https://github.com/Polymarket/rs-clob-client

**Deliverable:**

```text
python -m polymarket_arb.cli fetch-orderbook --token-id TOKEN
python -m polymarket_arb.cli scan-spreads
```

---

### Phase 3 — Raw Data Recorder
===============================

**Goal:**  
Start recording data continuously.

**Tasks:**

- Add scheduler.
- Add WebSocket recorder.
- Add raw JSONL writer.
- Add Parquet normalisation.
- Add stale-data detection.
- Add health checks.

**Useful sources:**

- Rust CLOB WebSocket examples:  
  https://github.com/Polymarket/rs-clob-client

- BTC bot WebSocket/data-source structure:  
  https://github.com/aulekator/Polymarket-BTC-15-Minute-Trading-Bot

**Deliverable:**

```text
python -m polymarket_arb.cli record --markets active --duration 1h
```

---

### Phase 4 — Market Relationship Graph
=======================================

**Goal:**  
Build a graph of related markets and candidate constraints.

**Tasks:**

- Parse market questions.
- Detect binary markets.
- Detect threshold markets.
- Detect same-event groups.
- Detect mutually exclusive outcomes.
- Detect exhaustive bundles.
- Detect implication relationships.
- Add AI-assisted candidate relationship suggestions.
- Add deterministic validators.

**Useful sources:**

- Arbitrage paper:  
  https://ideas.repec.org/p/arx/papers/2508.03474.html

- Polymarket Agents for AI/RAG inspiration:  
  https://github.com/Polymarket/agents

**Deliverable:**

```text
python -m polymarket_arb.cli build-graph
python -m polymarket_arb.cli show-related --market-id MARKET_ID
```

---

### Phase 5 — First Hard-Arbitrage Scanner
==========================================

**Goal:**  
Detect underpriced exhaustive baskets and simple YES/NO mispricings.

**Tasks:**

- Implement exhaustive-bundle scanner.
- Implement negation-pair scanner.
- Use executable asks/bids.
- Include fees/slippage.
- Include depth.
- Compute max trade size from depth.
- Save opportunities to database.
- Add alert-only output.

**Useful sources:**

- Unravelling the Probabilistic Forest:  
  https://ideas.repec.org/p/arx/papers/2508.03474.html

- STRAT probability simplex ideas:  
  https://www.usestrat.com/literature/polymarket

**Deliverable:**

```text
python -m polymarket_arb.cli scan-hard-arb --alert-only
```

---

### Phase 6 — Backtest / Replay Engine
======================================

**Goal:**  
Test strategies against stored historical orderbook data.

**Tasks:**

- Implement replay engine.
- Implement agent interface.
- Simulate taker fills.
- Simulate partial fills.
- Simulate maker orders later.
- Add fees.
- Add settlement.
- Output trade logs and equity curves.

**Useful source:**

- PredictionMarketBench:  
  https://github.com/Oddpool/PredictionMarketBench

**Deliverable:**

```text
python -m polymarket_arb.cli backtest --strategy hard_constraint_arb --date YYYY-MM-DD
```

---

### Phase 7 — Paper Trading
==========================

**Goal:**  
Run strategies on live data without sending orders.

**Tasks:**

- Add paper account.
- Add simulated fills.
- Add position tracking.
- Add PnL.
- Add dashboard.
- Add alerts.
- Add strategy performance summary.

**Useful sources:**

- ProbablyProfit:  
  https://github.com/randomness11/probablyprofit

- BTC bot monitoring/Grafana:  
  https://github.com/aulekator/Polymarket-BTC-15-Minute-Trading-Bot

**Deliverable:**

```text
python -m polymarket_arb.cli paper --strategy hard_constraint_arb
```

---

### Phase 8 — AI Research Layer
==============================

**Goal:**  
Add local/open-source AI to improve market parsing and strategy research.

**Tasks:**

- Add local LLM inference wrapper.
- Add model provider interface.
- Add structured JSON output parser.
- Add market summariser.
- Add resolution-rule extractor.
- Add relationship proposer.
- Add confidence scoring.
- Add hallucination tests.
- Add human-review queue.

**Legal model options:**

- DeepSeek-R1:  
  https://huggingface.co/deepseek-ai/DeepSeek-R1

- Qwen:  
  https://huggingface.co/Qwen

- Mistral open models:  
  https://docs.mistral.ai/getting-started/models/weights/

**Deliverable:**

```text
python -m polymarket_arb.cli ai-parse-market --market-id MARKET_ID
python -m polymarket_arb.cli ai-suggest-relationships
```

---

### Phase 9 — Crypto Latency Strategy
====================================

**Goal:**  
Build the first soft-edge strategy using BTC/ETH/SOL external feeds.

**Tasks:**

- Add Binance WebSocket.
- Add Coinbase WebSocket.
- Add realised volatility model.
- Add probability model for short-horizon close above/below target.
- Add stale quote detector.
- Backtest against stored data.
- Paper trade.

**Useful sources:**

- PolySwarm latency arbitrage discussion:  
  https://ideas.repec.org/p/arx/papers/2604.03888.html

- BTC 15-minute bot structure:  
  https://github.com/aulekator/Polymarket-BTC-15-Minute-Trading-Bot

**Deliverable:**

```text
python -m polymarket_arb.cli paper --strategy crypto_latency
```

---

### Phase 10 — Tiny Live Execution
=================================

**Goal:**  
Enable very small live trades only for deterministic hard-arb opportunities.

**Tasks:**

- Add private key handling.
- Add API credential setup.
- Add order signing.
- Add FOK/IOC-style execution where possible.
- Add max stake limits.
- Add quote-age limits.
- Add kill switch.
- Add auto-cancel.
- Add post-trade reconciliation.
- Add manual approval flag.

**Useful sources:**

- Python CLOB client:  
  https://github.com/Polymarket/py-clob-client

- Rust CLOB client:  
  https://github.com/Polymarket/rs-clob-client

- ProbablyProfit kill switch ideas:  
  https://github.com/randomness11/probablyprofit

**Deliverable:**

```text
python -m polymarket_arb.cli live-tiny --strategy hard_constraint_arb --max-trade-usdc 1
```

---

% =============================================================================
## 9. RISK CONTROLS
% =============================================================================

### 9.1 Data Risk Controls
=========================

- Refuse to trade stale orderbooks.
- Refuse to trade if WebSocket disconnected.
- Refuse to trade if REST snapshot disagrees with local book.
- Refuse to trade if market metadata changed recently.
- Refuse to trade if token IDs are missing or ambiguous.

---

### 9.2 Strategy Risk Controls
=============================

- Max stake per market.
- Max stake per event.
- Max total unresolved capital.
- Max number of open orders.
- Max number of markets per strategy.
- Min edge after fees/slippage.
- Min liquidity.
- Max spread.
- Max quote age.
- Strategy-specific kill switch.

---

### 9.3 Execution Risk Controls
==============================

- Prefer all-or-none execution for hard arbitrage.
- Cancel remaining legs if one leg fails.
- Never allow AI to submit orders directly.
- Reconcile positions after every order.
- Cancel all orders on disconnect.
- Cancel all orders on failed reconciliation.
- Store every order request and response.

---

### 9.4 Compliance Controls
==========================

- Do not bypass geoblocks.
- Do not trade from restricted jurisdictions.
- Do not scrape or use sources against their terms.
- Do not use leaked proprietary model weights.
- Keep a live-trading checklist.
- Keep audit logs.

---

% =============================================================================
## 10. FIRST BUILD MILESTONES
% =============================================================================

### Milestone 1 — Market Data Explorer
=====================================

**Target:**  
Working local market database.

```text
[x] Fetch active markets
[x] Store raw JSON
[x] Store normalised market table
[x] CLI search
[x] Fetch orderbook for selected token
```

---

### Milestone 2 — Relationship Graph Prototype
=============================================

**Target:**  
Detect obvious relationships.

```text
[x] Same-event grouping
[x] Binary YES/NO grouping
[x] Threshold parser
[x] Exhaustive-bundle candidates
[x] Manual review file
```

---

### Milestone 3 — Hard-Arb Alert Engine
======================================

**Target:**  
Alert-only scanner.

```text
[x] Compute executable basket cost
[x] Include fees/slippage
[x] Include depth
[x] Save opportunity
[x] Print alert
[x] No live orders
```

---

### Milestone 4 — Backtest Engine
================================

**Target:**  
Replay stored data.

```text
[x] Replay snapshots
[x] Simulate market orders
[x] Simulate partial fills
[x] Output PnL
[x] Output trade log
```

---

### Milestone 5 — Paper Trading
===============================

**Target:**  
Full live-data dry run.

```text
[x] Live data
[x] Simulated orders
[x] Position tracking
[x] PnL tracking
[x] Dashboard
[x] Alerts
```

---

### Milestone 6 — AI-Assisted Parser
===================================

**Target:**  
Local model extracts useful market structure.

```text
[x] Run DeepSeek/Qwen/Mistral locally or via controlled endpoint
[x] Extract resolution criteria
[x] Suggest related markets
[x] Output JSON only
[x] Human review queue
[x] Deterministic validation
```

---

### Milestone 7 — Tiny Live Execution
====================================

**Target:**  
Real orders, tiny size, hard-arb only.

```text
[x] API credentials
[x] Order signing
[x] Risk gate
[x] FOK/IOC preference
[x] Reconciliation
[x] Kill switch
[x] Max trade size = very small
```

---

% =============================================================================
## 11. PROJECT PRINCIPLE
% =============================================================================

The system should evolve in this order:

```text
record data
→ understand market structure
→ detect theoretical opportunities
→ calculate executable opportunities
→ backtest
→ paper trade
→ tiny live hard-arb only
→ expand strategy set
```

The worst version of this project would be:

```text
LLM reads market
→ LLM decides probability
→ bot places trade
```

The best version is:

```text
LLM proposes structure
→ deterministic validator checks it
→ optimiser calculates executable edge
→ risk gate approves
→ paper trading confirms
→ tiny live execution begins
```

---

% =============================================================================
## 12. SUMMARY
% =============================================================================

This project should not begin as a trading bot.

It should begin as a **Polymarket data recorder and market-structure research system**.

The first serious edge should be:

```text
hard logical arbitrage
+ executable orderbook depth
+ market relationship graph
+ replay testing
+ paper trading
```

Only after that should the repo expand into:

```text
AI-assisted parsing
+ crypto latency models
+ cross-market probabilistic models
+ live execution
```

The final target is an in-house research and execution platform where every trade can be explained, replayed, audited, and risk-checked.