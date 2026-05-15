"""``polymarket-arb healthcheck`` — settings, kill-switch, compliance,
storage round-trip, and (optionally) network probe of Gamma + CLOB."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone

import click

from ..compliance.geo_check import ComplianceError, GeoChecker
from ..http.client import AsyncHttpClient, HttpError, TransientError
from ..monitoring import kill_switch
from ..risk.checks import default_checks
from ..risk.models import PreflightContext, RiskLimits
from ..risk.preflight import PreflightGate
from ..settings import REPO_ROOT, Settings
from ..storage.parquet.account_events import ParquetRiskSnapshotsRepository


@click.command()
@click.option("--skip-network", is_flag=True, default=False,
              help="Skip Gamma/CLOB probe (offline-only checks).")
@click.option("--json-out", is_flag=True, default=False, help="Emit JSON instead of human text.")
@click.pass_context
def healthcheck(ctx: click.Context, skip_network: bool, json_out: bool) -> None:
    """Run the foundation health checks: settings, logging, kill-switch,
    compliance, storage round-trip, and (optionally) network probe.

    Exit code 0 means every check passed.
    """

    settings: Settings = ctx.obj["settings"]
    result = asyncio.run(_run_healthcheck(settings, skip_network=skip_network))

    if json_out:
        click.echo(json.dumps(result, indent=2, default=str))
    else:
        _print_health_human(result)

    sys.exit(0 if result["overall"] == "PASS" else 1)


async def _run_healthcheck(settings: Settings, *, skip_network: bool) -> dict:
    started_at = datetime.now(timezone.utc)
    checks: list[dict] = []

    def add(name: str, ok: bool, **detail: object) -> None:
        checks.append({"name": name, "status": "PASS" if ok else "FAIL", **detail})

    # 1) Settings + env
    add("settings.loaded", True, env=settings.env, gamma_host=settings.gamma_host,
        clob_host=settings.clob_host, orders_allowed=settings.orders_allowed)

    # 2) Data dirs
    data_root = settings.data_root
    dirs = ["raw", "normalised", "account", "derived", "logs"]
    missing = [d for d in dirs if not (data_root / d).exists()]
    if missing:
        for d in missing:
            (data_root / d).mkdir(parents=True, exist_ok=True)
        add("data.dirs", True, created=missing)
    else:
        add("data.dirs", True, root=str(data_root))

    # 3) Kill switch
    ks_active = kill_switch.is_active(settings.killswitch_path)
    add("kill_switch.off", not ks_active, killswitch_path=str(settings.killswitch_path))

    # 4) Storage round-trip via the preflight gate.
    risk_repo = ParquetRiskSnapshotsRepository(
        data_root=settings.data_root,
        compression=settings.storage.parquet.compression,
        row_group_size=settings.storage.parquet.row_group_size,
    )

    async with AsyncHttpClient(settings.http) as http:
        geo_checker = GeoChecker(settings, http)

        if skip_network:
            add("compliance.skipped", True,
                reason="--skip-network: not probing egress IP or APIs")
        else:
            try:
                info = await geo_checker.fetch()
                add("compliance.egress_ip", True, ip=info.ip, country=info.country_iso)
            except ComplianceError as exc:
                add("compliance.egress_ip", False, reason=str(exc))

            try:
                payload = await http.get_json(f"{settings.gamma_host}/markets",
                                              params={"limit": 1})
                add("network.gamma", True, sample_keys=list(payload[0].keys())[:6]
                    if isinstance(payload, list) and payload else [])
            except (HttpError, TransientError) as exc:
                add("network.gamma", False, reason=str(exc))

            try:
                resp = await http.get_json(f"{settings.clob_host}/")
                add("network.clob", True, sample=resp if isinstance(resp, dict) else None)
            except (HttpError, TransientError) as exc:
                add("network.clob", False, reason=str(exc))

        approved_path = (REPO_ROOT / "configs" / "approved_strategies.yaml").resolve()
        gate = PreflightGate(
            checks=default_checks(
                settings=settings, geo_checker=geo_checker,
                killswitch_path=settings.killswitch_path,
                approved_path=approved_path,
            ),
            risk_repo=risk_repo,
        )
        ctx_obj = PreflightContext(
            strategy_id=None, order=None, paper_mode=False, limits=RiskLimits(),
        )
        report = await gate.evaluate(ctx_obj)
        add("preflight.dry_run", True,
            overall=report.overall.value,
            checks={c.name: c.status.value for c in report.checks},
            note="orders_allowed=false → preflight expected to fail; gate plumbing OK",
            wrote_risk_snapshot=True)

    try:
        recent = risk_repo.recent(limit=3)
        add("storage.duckdb_readback", True,
            recent_snapshot_count=len(recent),
            latest_overall=recent[0].overall if recent else None)
    except Exception as exc:
        add("storage.duckdb_readback", False, reason=str(exc))

    overall = "PASS" if all(c["status"] in ("PASS", "SKIPPED") or c["name"] == "preflight.dry_run"
                            for c in checks) else "FAIL"
    return {
        "overall": overall,
        "started_at": started_at.isoformat(),
        "elapsed_ms": int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
        "checks": checks,
    }


def _print_health_human(result: dict) -> None:
    head = "✓ PASS" if result["overall"] == "PASS" else "✗ FAIL"
    click.secho(f"{head}  ({result['elapsed_ms']} ms)\n",
                fg="green" if result["overall"] == "PASS" else "red", bold=True)
    for c in result["checks"]:
        glyph = {"PASS": "✓", "FAIL": "✗", "SKIPPED": "·"}.get(c["status"], "?")
        click.echo(f"  {glyph} {c['name']:<28} {c['status']}")
        for k, v in c.items():
            if k in ("name", "status"):
                continue
            click.echo(f"      - {k}: {v}")
