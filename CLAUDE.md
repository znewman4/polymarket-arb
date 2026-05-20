# Polymarket Arbitrage Bot — Claude Code Context

## What This Project Is
A fully automated trading bot for Polymarket (world's largest decentralised prediction market). The bot monitors prediction market pairs, detects pricing inefficiencies between related markets (nesting, contradiction, inverse relationships), and places orders via Polymarket's CLOB API. Currently running in **paper trading mode** (no real orders placed).

Operator is UK-based. Bot runs on AWS EC2 in Ireland (non-blocked jurisdiction).

---

## Architecture Overview

### Infrastructure
- **EC2:** t3.small, eu-west-1 (Dublin, Ireland), instance `i-0a6672c60a510b3bf` (ArbModelVS)
- **Elastic IP:** `52.50.100.56` (Irish, verified not blocked by Polymarket)
- **Access:** AWS SSM Session Manager only — no SSH, no open inbound ports
- **Wallet:** `0x64e8a3549Dbb8e097dfB2C23F1a59B62D7DE2474` (Polygon, funded with ~$19.48 USDC + 59 POL)
- **Key storage:** AWS Secrets Manager (`polygon` secret = private key, `polymarket/api_credentials` = CLOB API creds)

### Docker Services (deploy/docker-compose.yml)
Two services share `/app/data` bind mount:
- **recorder** — runs continuous loop: `fetch-markets --all` → `snapshot-active-markets --limit 2000` → sleep 30s. Populates `data/normalised/orderbook_snapshots/` and `data/normalised/markets/`
- **agent** — runs `live agent --strategy relationship_diagnostic --strategy-auto-tokens`. Polls orderbook lake every 10s, evaluates relationship signals, writes to `data/normalised/orders_log/`

### Key Settings (prod)
```
POLYMARKET_ARB_PAPER_MODE=true          # Safe default — never submits orders
POLYMARKET_ARB_ORDERS_ALLOWED=false     # Compliance gate
POLYMARKET_ARB_AGENT_STRATEGY=relationship_diagnostic
POLYMARKET_ARB_AGENT_POLL_INTERVAL_S=10
```

---

## Codebase Map

### Live Trading
- `src/polymarket_arb/live/agent_loop.py` — main loop, calls strategy, routes intents through OrderClient
- `src/polymarket_arb/live/order_client.py` — single choke point: kill switch → preflight → paper fill → orders_log
- `src/polymarket_arb/live/models.py` — OrderResult, OrdersLogRow
- `src/polymarket_arb/live/signing.py` — EIP-712 stub (always raises — live trading not yet implemented)
- `src/polymarket_arb/cli/live.py` — CLI: `live agent`, `live healthcheck`. Contains _STRATEGIES dict with noop, relationship_diagnostic, relationship_aggressive

### Strategy Signal Logic
- `src/polymarket_arb/strategies/nesting_contradiction.py` — `evaluate_relationship_at_tick()`: takes RelationshipCandidateRow + AlignedPricePoint, returns StrategyCandidateRow or None
- Strategy thresholds:
  - `relationship_diagnostic`: min_gross_edge=0.05, fee_bps=0, slippage_bps=50, min_net_edge=0.02
  - `relationship_aggressive`: min_gross_edge=0.03, fee_bps=0, slippage_bps=50, min_net_edge=0.01

### Backtest Framework
- `src/polymarket_arb/backtest/standardised/` — 8-lane standardised backtest orchestrator
- Most recent run: `data/backtests/full8_depth_20260518_203014/` — 1,508 legs, causality gate active, depth-aware execution (0% coverage currently)
- Key findings: sports_progression +1,765% avg return, nesting +441%, control −90% (confirms signal vs noise)

### Storage
- `data/normalised/` — parquet lake, hive-partitioned by `dt=YYYY-MM-DD`
  - `orderbook_snapshots/` — populated by recorder
  - `relationship_candidates/` — synced from local machine via S3 (bucket: `polymarket-arb-data-znewman`)
  - `orders_log/` — written by agent on every place_order call
  - `markets/`, `best_quotes/`, `events/`
- `src/polymarket_arb/storage/parquet/orderbook_repo.py` — `latest_books_bulk()` for batch token lookup (fixes "too many open files")

### Risk / Safety
- `src/polymarket_arb/monitoring/kill_switch.py` — file-based: `touch data/.killswitch` halts agent within one tick
- `src/polymarket_arb/compliance/trade_gate.py` — `raise_if_orders_disallowed()` checks `orders_allowed` flag
- `src/polymarket_arb/risk/preflight.py` — PreflightGate runs checks before every order

---

## Four Implementation Phases (All Complete)

| Phase | What | Status |
|-------|------|--------|
| 1 | Causality gate — drops trades where entry_ts >= resolution_ts | ✅ |
| 2 | Depth-aware execution — re-fills against recorded orderbook depth, falls back to flat-bps | ✅ |
| 3 | Live order infrastructure — OrderClient, agent_loop, orders_log, paper_mode flag | ✅ |
| 4 | VPS deployment — Docker Compose, systemd units, healthcheck, deploy/README.md | ✅ |

---

## Current Status (as of 2026-05-20)

| Item | Status |
|------|--------|
| EC2 running, SSM accessible | ✅ |
| Elastic IP verified Irish, not blocked | ✅ |
| Docker recorder + agent running | ✅ |
| Relationship candidates lake (2,990 token pairs) | ✅ Synced from local via S3 |
| Orderbook snapshots being recorded | ✅ ~186/cycle, 93 markets |
| Agent watching 2,990 tokens | ✅ |
| Signals firing | ❌ No overlap yet between relationships and live orderbooks |
| Orders in orders_log | ❌ Empty — waiting for coverage |
| Dashboard / UI | ❌ Not built yet |

**Root cause of no signals:** The relationship candidates reference ~1,500 market pairs built up over months of local data collection. The EC2 orderbook lake only has 93 markets so far. As the recorder runs continuously and coverage grows, signals should start appearing.

---

## Deferred Work

- **Strategy standardisation** — relationship promotion from `needs_manual_review → accepted`. Deferred until ≥3 weeks of standardised backtest logs accumulate.
- **Live trading** — `signing.py` is a stub that always raises. Requires EIP-712 implementation + explicit opt-in (`paper_mode=False` AND `orders_allowed=True` AND private key configured).
- **Dashboard UI** — Flask app served via SSM tunnel (no open ports). Not yet built.
- **Depth-aware backtest realism** — currently 0% orderbook coverage in backtest. Will improve as EC2 lake grows.

---

## Development Workflow

```
Edit locally in VS Code
  → git push to main
  → SSM terminal on EC2:
      cd ~/polymarket-arb && git pull && sudo docker compose -f deploy/docker-compose.yml --env-file ~/polymarket-arb/.env up -d --build
  → Check logs:
      sudo docker compose -f ~/polymarket-arb/deploy/docker-compose.yml --env-file ~/polymarket-arb/.env logs --tail=30 agent
```

### Useful EC2 Commands
```bash
# Check both services
sudo docker compose -f ~/polymarket-arb/deploy/docker-compose.yml --env-file ~/polymarket-arb/.env ps

# Agent logs
sudo docker compose -f ~/polymarket-arb/deploy/docker-compose.yml --env-file ~/polymarket-arb/.env logs --tail=30 agent

# Recorder logs
sudo docker compose -f ~/polymarket-arb/deploy/docker-compose.yml --env-file ~/polymarket-arb/.env logs --tail=20 recorder

# Check orders_log
sudo docker exec polymarket-arb-agent python3 -c "
import duckdb; con = duckdb.connect()
try:
    rows = con.execute(\"SELECT status, COUNT(*) FROM read_parquet('/app/data/normalised/orders_log/dt=*/*.parquet', hive_partitioning=true) GROUP BY status\").fetchall()
    [print(r) for r in rows]
except Exception as e: print('Empty:', e)
"

# Kill switch
touch ~/polymarket-arb/data/.killswitch   # halt
rm ~/polymarket-arb/data/.killswitch      # resume

# Healthcheck
sudo docker exec polymarket-arb-agent /usr/local/bin/agent-healthcheck

# Sync relationship candidates from S3 (if needed)
aws s3 cp s3://polymarket-arb-data-znewman/relationship_candidates/ ~/polymarket-arb/data/normalised/relationship_candidates/ --recursive --region eu-west-1
```

---

## Key Contracts / Interfaces

### evaluate_relationship_at_tick signature
```python
def evaluate_relationship_at_tick(
    rel: RelationshipCandidateRow,
    point: AlignedPricePoint,
    run_id: str,
    min_gross_edge: float,
    fee_bps: Decimal,
    slippage_bps: Decimal,
    min_net_edge: float,
    execution_model: str = "price_history_only",
    execution_model_confidence: float = 0.4,
    stake_usdc: Decimal = Decimal("250"),
) -> StrategyCandidateRow | None
```

### OrderClient.place_order gates (in order)
1. Kill switch check
2. Preflight verdict (caller-supplied)
3. paper_mode branch → simulate fill via execution_sim
4. Compliance gate (orders_allowed)
5. Signing stub (always raises in current phase)

### orders_log row statuses
`paper_filled` | `paper_partial` | `paper_no_book` | `paper_no_fill` | `rejected_kill_switch` | `rejected_preflight` | `rejected_orders_disallowed` | `rejected_live_signing_not_ready` | `rejected_unsupported_side`

---

## AWS Account Details
- **Account ID:** 179598271667
- **Region:** eu-west-1
- **IAM Role on EC2:** InstanceRole (has SSM, CloudWatch, Secrets Manager read, S3 read)
- **IAM User for local CLI:** polymarket-cli (S3FullAccess)
- **S3 bucket:** polymarket-arb-data-znewman (relationship candidates sync)
- **Secrets Manager:** `polygon` (private key), `polymarket/api_credentials` (CLOB API creds)