#!/bin/sh
# Docker / systemd healthcheck.  Exits 0 iff the agent is in a safe state.
#
# Rules:
#   - kill_switch_active=true                 → unhealthy (someone halted trading)
#   - paper_mode=false AND orders_allowed=false → unhealthy (config drift)
#   - orders_log_lake_writable=false          → unhealthy (data dir broken)
# Everything else (e.g. paper_mode=true regardless of orders_allowed) is OK.
set -eu

OUT="$(python -m polymarket_arb.cli live healthcheck 2>/dev/null)" || {
  echo "live healthcheck failed to run" >&2
  exit 1
}
printf '%s\n' "$OUT" | python -c '
import json, sys
data = json.load(sys.stdin)
errs = []
if data.get("kill_switch_active"):
    errs.append("kill_switch_active=true")
if (not data.get("paper_mode")) and (not data.get("orders_allowed")):
    errs.append("paper_mode=false but orders_allowed=false (live config drift)")
if not data.get("orders_log_lake_writable"):
    errs.append("orders_log_lake not writable")
if errs:
    print("UNHEALTHY: " + "; ".join(errs))
    sys.exit(1)
print("HEALTHY: paper_mode={} orders_allowed={}".format(
    data.get("paper_mode"), data.get("orders_allowed")))
'
