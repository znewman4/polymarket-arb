# Deploying the paper-trading agent on a VPS

This directory turns the repo into something runnable 24/7 on a small VPS
(Hetzner CX22, DigitalOcean Basic, EC2 t3.small, etc).  Two services run side
by side:

* **`recorder`** — `polymarket-arb record snapshots` in a loop, populating
  `data/normalised/orderbook_snapshots/` so the agent always has live depth.
* **`agent`** — `polymarket-arb live agent`, which polls the lake every
  `agent_poll_interval_s` seconds, evaluates the configured strategy, and
  routes any `OrderIntent` through `OrderClient`.  In paper mode (the safe
  default) the client never opens a network socket to Polymarket and writes
  every attempt to `data/normalised/orders_log/`.

The agent runs **paper mode by default**.  Flipping to live trading requires:

1. `POLYMARKET_ARB_PAPER_MODE=false`
2. `POLYMARKET_ARB_ORDERS_ALLOWED=true`
3. `POLYMARKET_PRIVATE_KEY=<hex>` (and the signing implementation in
   `src/polymarket_arb/live/signing.py` actually shipped — currently a stub
   that raises).

If any of the three is missing, the OrderClient short-circuits with an
auditable `OrdersLogRow`.

---

## Option A: Docker Compose (recommended)

### Prerequisites
- A Linux VPS with **2 GB RAM**, **20 GB disk**, and Docker + Docker Compose
  installed.  `t3.small` / `CX22` works.  The parquet lake grows ~50 MB/day at
  default settings; size the disk accordingly.

### One-time setup

```bash
ssh root@your-vps
adduser polymarket --disabled-password --gecos ""
usermod -aG docker polymarket
su - polymarket
git clone https://github.com/<you>/polymarket-arb.git
cd polymarket-arb
cp .env.example .env
# Edit .env — at minimum set:
#   POLYMARKET_ARB_WATCHED_TOKENS=tok1,tok2,...
# Leave PAPER_MODE / ORDERS_ALLOWED at their defaults (paper / disallowed).
docker compose -f deploy/docker-compose.yml up -d --build
```

### Verifying the deployment

```bash
docker compose -f deploy/docker-compose.yml ps
# Both services should report `Up (healthy)` after ~30s.

docker compose -f deploy/docker-compose.yml logs -f agent
# Look for: "agent_loop starting paper_mode=True orders_allowed=False"

docker compose -f deploy/docker-compose.yml exec agent \
    polymarket-arb live healthcheck
# Returns JSON with paper_mode=true and orders_log_lake_writable=true.
```

### Engaging the kill switch from the host

The kill-switch file is `data/.killswitch`.  Both services mount `data/` and
poll the file on every tick — touching it halts the agent within one loop
iteration without restarting anything:

```bash
touch /home/polymarket/polymarket-arb/data/.killswitch
docker compose -f deploy/docker-compose.yml logs --tail=20 agent
# Expect: "agent_loop halted: kill switch active"
# When you're ready to resume:
rm /home/polymarket/polymarket-arb/data/.killswitch
docker compose -f deploy/docker-compose.yml restart agent
```

### Updating the deployment

```bash
cd ~/polymarket-arb
git pull
docker compose -f deploy/docker-compose.yml up -d --build
```

---

## Option B: systemd (no Docker)

### One-time setup

```bash
ssh root@your-vps
adduser polymarket --disabled-password --gecos ""
apt update && apt install -y python3.11 python3.11-venv git
sudo -u polymarket -i bash <<'EOF'
git clone https://github.com/<you>/polymarket-arb.git /opt/polymarket-arb
cd /opt/polymarket-arb
python3.11 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .
cp .env.example .env
# Edit .env exactly as in the Docker path above.
EOF

# Install the unit files.
cp /opt/polymarket-arb/deploy/systemd/polymarket-recorder.service /etc/systemd/system/
cp /opt/polymarket-arb/deploy/systemd/polymarket-agent.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now polymarket-recorder polymarket-agent
```

### Verifying

```bash
systemctl status polymarket-recorder polymarket-agent
journalctl -u polymarket-agent -f
sudo -u polymarket /opt/polymarket-arb/.venv/bin/polymarket-arb live healthcheck
```

### Kill switch

```bash
sudo -u polymarket touch /opt/polymarket-arb/data/.killswitch
# Agent halts on its next tick.  To resume:
sudo -u polymarket rm /opt/polymarket-arb/data/.killswitch
systemctl restart polymarket-agent
```

---

## Security checklist

- `.env` is **gitignored** — secrets never reach the repo.
- `POLYMARKET_PRIVATE_KEY` is only ever needed when `paper_mode=false`.  Leave
  it unset until the signing path is actually shipped.
- The kill-switch file is the single source of truth for "stop everything";
  it works from the host filesystem without container access.
- `IP_PROVIDER_PRIMARY` / `IP_PROVIDER_SECONDARY` are double-checked by the
  compliance gate before every order in live mode; default values geofence to
  EU egress.
- The agent's Docker healthcheck refuses to report healthy if
  `paper_mode=false` AND `orders_allowed=false` — surfaces config drift
  immediately.

## Resource notes

- Default settings record orderbook snapshots for the top 500 markets every
  30 s and run the agent loop every 10 s.  CPU is idle most of the time; disk
  fills at ~50 MB/day.
- The parquet lake is daily-partitioned (`dt=YYYY-MM-DD`).  Old partitions can
  be moved to cold storage; the agent only reads the most recent snapshot per
  token.
