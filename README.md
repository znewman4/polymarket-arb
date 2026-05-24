# polymarket-arb

Fully automated Polymarket arbitrage bot running in **paper-trading mode** on AWS EC2 (eu-west-1). The bot ingests live orderbook data, detects pricing inefficiencies across related markets (nesting, contradiction, inverse, sports-progression), and paper-trades signals via an authenticated order client. A Limitless × Polymarket cross-market arb scanner runs in parallel. A read-only Flask dashboard is served over an SSM port-forward.

Live trading infrastructure is complete but gated: the signing path is a stub and two env flags (`POLYMARKET_ARB_PAPER_MODE=false` + `POLYMARKET_ARB_ORDERS_ALLOWED=true`) must both be flipped explicitly.

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
```

---

## Services (Docker Compose)

Five services run on the EC2 instance, all sharing the same `/app/data` parquet lake:

| Service | Role |
|---------|------|
| `recorder` | Continuously snapshots live orderbooks + refreshes markets (30s loop) |
| `agent` | Paper-trades relationship signals via `live agent --strategy-auto-tokens` |
| `limitless-arb` | Paper-trades Limitless × Polymarket arb gaps every 5 minutes |
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

The signing path (`src/polymarket_arb/live/signing.py`) is a stub that always raises — live orders cannot be submitted even if both flags are flipped, until EIP-712 signing is implemented.

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
deploy/                 Dockerfile.agent, docker-compose.yml, README.md, healthcheck.sh
docs/                   trade_gate, connectivity, storage, live_trading_checklist
src/polymarket_arb/
    cli/                Click subgroups: gamma, clob, live, limitless, dashboard,
                        record, backtest, research, relationships, inspect, …
    settings.py         Pydantic-settings — yaml + env var loader (POLYMARKET_ARB_*)
    compliance/         geo_check + trade_gate (orders_allowed flag)
    live/               agent_loop, order_client, models, signing stub
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
tests/                  mirror of src/ — 821 tests, pytest + respx + tmp_path
```

---

## Current State (2026-05-24)

- EC2 running: all 5 services healthy
- Orderbook coverage: ~93 markets (recorder growing the lake daily)
- Relationship candidates: 2,990 pairs synced from local S3
- Signals: not yet firing — waiting for overlap between relationships and live orderbooks
- Orders log: empty — will populate once coverage reaches relationship pairs
- Dashboard: live, accessible via SSM port-forward

The `/markets` dashboard page shows coverage progress in real time.
