# polymarket-arb

Fully automated Polymarket arbitrage bot running on AWS EC2 (eu-west-1), with **paper-trading as the safe default**. The bot ingests live orderbook data, detects pricing inefficiencies across related markets (nesting, contradiction, inverse, sports-progression), and routes all order attempts through a single audited `OrderClient`. A Limitless × Polymarket cross-market arb scanner runs in parallel. A read-only Flask dashboard is served over an SSM port-forward.

Live trading infrastructure is now wired through a local Node.js signer microservice (`poly-signer`) for Polymarket CLOB V2 Magic/proxy wallet order submission. Live orders are still gated by explicit env flags (`POLYMARKET_ARB_PAPER_MODE=false` + `POLYMARKET_ARB_ORDERS_ALLOWED=true`), the kill switch, compliance checks, and signer service availability.

---

## Quickstart

```bash
make install
make test
polymarket-arb live healthcheck
```

To start the local dashboard against the dev lake:

```bash
polymarket-arb dashboard serve --port 5000
# open http://localhost:5000
```

---

## Implemented Phase Map

```text
Phase 0   safety / storage / parquet-lake foundation
Phase 1   Gamma market + event ingestion
Phase 1.5 local NLP semantic extraction (Ollama/DeepSeek or mock)
Phase 1.6 deterministic YAML rulebook scoring
Phase 2   single-market implication extraction
Phase 3   public read-only CLOB orderbook ingestion
Phase 4   weighted research-only fusion scoring
Phase 4.5 data inspection, audit, review exports, REST recording
Phase 5   offline standardised backtest framework (8-lane, depth-aware, causality-gated)
Phase 5.5 relationship miner upgrade — mutually_exclusive_category, temporal subtypes
Phase 6   live order infrastructure — OrderClient, agent_loop, paper_mode, orders_log
Phase 7   VPS deployment — Docker Compose (recorder + agent), systemd, healthcheck
Phase 8   Limitless × Polymarket cross-market arb scanner
Phase 9   Relationship-miner Docker service (6-hour validate loop)
Phase 10  Read-only Flask dashboard — overview / orders / signals / markets / health
Phase 11  Polymarket CLOB V2 live submission via Node signer sidecar
```

---

## Services (Docker Compose)

Six services run on the EC2 instance. Python services share the same `/app/data` parquet lake, and live Polymarket submission goes through the signer sidecar:

| Service | Role |
|---------|------|
| `recorder` | Continuously snapshots live orderbooks + refreshes markets (30s loop) |
| `agent` | Paper-trades relationship signals via `live agent --strategy-auto-tokens` |
| `limitless-arb` | Paper-trades Limitless × Polymarket arb gaps every 5 minutes |
| `poly-signer` | Node.js signer/submitter for Polymarket CLOB V2 Magic/proxy wallet accounts |
| `relationship-miner` | Scores and promotes relationship candidates every 6 hours |
| `dashboard` | Flask read-only UI at container port 5000, accessed via SSM port-forward |

```bash
# Deploy / update all services
cd ~/polymarket-arb && git pull
sudo docker compose -f deploy/docker-compose.yml --env-file ~/polymarket-arb/.env up -d --build
```

See [deploy/README.md](deploy/README.md) for full EC2 setup, kill switch, and SSM dashboard access.

---

## Dashboard

A read-only Flask dashboard gives a live view of the parquet lake. Access it via SSM port-forwarding — no host port is published.

```bash
# From your laptop
aws ssm start-session \
  --target i-0a6672c60a510b3bf \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["5000"],"localPortNumber":["5000"]}' \
  --region eu-west-1
# open http://localhost:5000
```

Pages: `/` overview, `/orders` filterable orders log + CSV export, `/signals` no-fill analysis + edge histogram + Limitless gaps, `/markets` lake coverage + relationship type breakdown, `/health` JSON probe.

---

## Trade Gate

`POLYMARKET_ARB_ORDERS_ALLOWED` defaults to `false`. `POLYMARKET_ARB_PAPER_MODE` defaults to `true`. All order attempts pass through `OrderClient`, which enforces both flags before any network socket is opened.

For live Polymarket orders, Python calls the signer sidecar at `POLYMARKET_ARB_POLYMARKET_SIGNER_URL` (default `http://poly-signer:7777`). The signer container owns EIP-712 signing and CLOB V2 submission using `@polymarket/clob-client-v2`, `viem`, and the configured Magic/proxy wallet funder.

Required live env values:

```bash
POLYMARKET_ARB_PAPER_MODE=false
POLYMARKET_ARB_ORDERS_ALLOWED=true
POLYMARKET_ARB_POLYMARKET_SIGNER_URL=http://poly-signer:7777
POLYMARKET_ARB_POLYMARKET_PRIVATE_KEY=...
POLYMARKET_ARB_POLYMARKET_API_KEY=...
POLYMARKET_ARB_POLYMARKET_API_SECRET=...
POLYMARKET_ARB_POLYMARKET_API_PASSPHRASE=...
POLYMARKET_ARB_POLYMARKET_FUNDER=...   # proxy wallet / funder address
POLYMARKET_ARB_POLYMARKET_SIGNATURE_TYPE=1  # use 3 for deposit-wallet / POLY_1271 accounts
```

> **Identity must be consistent.** For a deposit-wallet / proxy account
> (`SIGNATURE_TYPE=3`), the `FUNDER` is the on-chain proxy that holds the USDC,
> and `PRIVATE_KEY` **must** be the key of that proxy's *owner* EOA (the address
> returned by the proxy's `owner()`).  The `API_KEY`/`API_SECRET`/`API_PASSPHRASE`
> must also be the CLOB credentials **derived by that same owner EOA**
> (`createOrDeriveApiKey`).  If the key, the API creds, and the proxy owner do
> not all resolve to the same EOA, the CLOB rejects orders with
> *"the order signer address has to be the address of the API KEY"* (creds belong
> to a different EOA) or *"maker address not allowed, please use the deposit
> wallet flow"* (wrong signature type for the funder).  The signer logs the
> derived signer address and runs an owner-match self-check on boot.

See [docs/trade_gate.md](docs/trade_gate.md) for the full threat model.

---

## CLI

```bash
# Market ingestion
polymarket-arb gamma fetch-markets --all
polymarket-arb record snapshot-active-markets --limit 500

# Live agent (paper mode)
polymarket-arb live agent --strategy relationship_diagnostic --strategy-auto-tokens
polymarket-arb live healthcheck

# Limitless arb
polymarket-arb limitless scan-arb --execute

# Relationship pipeline
polymarket-arb relationships validate

# Backtest
polymarket-arb research standardised-backtest

# Dashboard
polymarket-arb dashboard serve --port 5000

# Inspection
polymarket-arb inspect counts
polymarket-arb inspect orders-log --limit 50
```

---

## Repo Map

```
configs/                dev.yaml, approved_strategies.yaml, semantic_rules/
data/                   raw/ normalised/ account/ derived/ logs/ debug/  (gitignored)
deploy/                 Dockerfile.agent, docker-compose.yml, README.md, healthcheck.sh,
                        signer/ Node.js CLOB V2 signer sidecar
docs/                   trade_gate, connectivity, storage, live_trading_checklist
src/polymarket_arb/
    cli/                Click subgroups: gamma, clob, live, limitless, dashboard,
                        record, backtest, research, relationships, inspect, …
    settings.py         Pydantic-settings — yaml + env var loader (POLYMARKET_ARB_*)
    compliance/         geo_check + trade_gate (orders_allowed flag)
    live/               agent_loop, order_client, models, signer HTTP wrapper
    strategies/         evaluate_relationship_at_tick (nesting/contradiction logic)
    limitless/          cross-market arb scanner (Limitless × Polymarket)
    dashboard/          Flask app factory, DuckDBQueryService, routes, Jinja templates
    backtest/           standardised 8-lane orchestrator, depth-aware fills
    ingest/             Gamma + CLOB read-only ingestion
    nlp/                Ollama/mock extraction, schemas, prompts, embeddings
    semantics/          YAML rulebook loading + deterministic scorers
    inspect/            local lake inspection, audit, CSV review exports
    record/             REST recording loops + run manifests
    storage/            DTOs, parquet repos, DuckDB views
    risk/               PreflightGate (10 checks before every order)
    monitoring/         kill_switch (file-based: touch data/.killswitch)
tests/                  mirror of src/ — pytest + respx + tmp_path
```

---

## Current State (2026-05-31)

- EC2 deployment is Docker Compose based, with recorder, agent, limitless-arb, poly-signer, relationship-miner, and dashboard services.
- Polymarket live order submission has moved off Python signing and into the `poly-signer` Node.js sidecar because the Python CLOB V2 SDK path fails for Magic/proxy wallet accounts.
- `OrderClient` remains the choke point: kill switch, preflight, paper/live mode, compliance gate, then signer HTTP call.
- Limitless × Polymarket arb now refreshes authoritative Polymarket token IDs from CLOB, fetches missing Limitless exchange addresses from market detail, guards against live-Limitless/paper-Polymarket execution, and tracks open positions through the positions lake.
- Dashboard positions now ignore snapshot rows when selecting open position state, and dashboard refresh is 30s.
- Live trading is deployable but intentionally gated. Keep paper mode and orders disallowed until `.env`, Secrets Manager, signer health, and a small end-to-end test order are confirmed.

## Kill Switches

All live and paper order-routing paths still honour the global kill switch:

```bash
touch data/.killswitch
```

Strategy-specific files pause one lane without stopping everything else:

- `data/.killswitch_limitless_arb` stops only the Limitless × Polymarket arb executor.
- `data/.killswitch_agent` stops only the `relationship_aggressive` live agent strategy.

Remove the relevant file to resume:

```bash
rm data/.killswitch
rm data/.killswitch_limitless_arb
rm data/.killswitch_agent
```

Useful live-signer checks after deploy:

```bash
sudo docker logs polymarket-arb-poly-signer --tail=20
sudo docker exec polymarket-arb-poly-signer wget -qO- http://localhost:7777/health
sudo docker exec polymarket-arb-limitless-arb python3 -c "import httpx; print(httpx.get('http://poly-signer:7777/health', timeout=5).json())"
```
