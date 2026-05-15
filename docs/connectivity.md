# Connectivity & Egress Runbook

## Why this matters

Polymarket geoblocks the United Kingdom on its **website and order endpoints**.
Public read endpoints (Gamma + CLOB GET) are reachable from most regions but
this is not contractually guaranteed and can change.

**Phase 0–9 of this repo never places an order.** The
`POLYMARKET_ARB_ORDERS_ALLOWED` flag and `risk/preflight.py` together make
order placement a compile-time impossibility (the order client — added Phase 10
— refuses to instantiate without a `PreflightToken`, which the gate refuses to
produce until every check passes). This means our day-to-day legal exposure
during research is the same as anyone calling a public REST API.

## Recommended deployment

- **Move Phase 3+ recording to a small VPS** in a permitted EU jurisdiction
  (Germany / Netherlands / Ireland / etc.). The user is in the UK; consumer
  VPN drops would otherwise corrupt the data lake mid-record.
- Phase 0–2 (manual CLI calls) on a UK laptop+VPN is fine because the gates
  refuse to run if `current egress country ∉ allowed_egress_countries`.

## Egress IP check

`compliance/geo_check.py` queries TWO independent providers:

1. `https://api.ipify.org?format=json` (returns `{"ip": "..."}`)
2. `https://ifconfig.co/json`        (returns `{"ip":..., "country_iso":"DE",...}`)

For provider 1 we then call `https://ipapi.co/{ip}/json/` to resolve country.
The two country answers must agree. If they disagree, the gate fails closed.

A successful result is cached for `compliance.ip_check_ttl_s` (default 5 min).

## What happens on VPN drop

1. `EgressIPWhitelistCheck` returns Fail on next check.
2. Preflight gate refuses to issue a `PreflightToken`.
3. The CLI command exits non-zero and writes a `risk_snapshots` row.
4. Operator must verify VPN is up and re-run.

## Static IP rotation

If the VPS IP changes (e.g. provider migration):

1. Update `configs/dev.yaml#compliance.allowed_egress_countries` if region also
   changed.
2. Rotate any persisted IP-check cache (delete `data/normalised/risk_snapshots/`
   if you want a fresh audit trail; otherwise leave it — the cache is in-memory
   only).
3. Re-run `polymarket-arb healthcheck`. The check should pass before any
   recording resumes.
