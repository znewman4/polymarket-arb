"""Lake maintenance: compact small parquet files into one per partition."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import click
import duckdb

from ..http.client import AsyncHttpClient
from ..ingest.limitless.parser import parse_limitless_market
from ..limitless.signing import sign_request
from ..live.signing import build_clob_client
from ..settings import Settings
from ..storage.parquet.relationship_candidates_repo import (
    ParquetRelationshipCandidatesRepository,
)


@click.group(name="maintenance")
def maintenance_cmd() -> None:
    """Lake maintenance operations."""


@dataclass(frozen=True)
class _ConnectivityCheck:
    label: str
    ok: bool
    detail: str


@maintenance_cmd.command(name="compact-lake")
@click.option(
    "--older-than-days",
    type=int,
    default=1,
    show_default=True,
    help="Only compact partitions older than this many days.",
)
@click.option(
    "--min-files",
    type=int,
    default=10,
    show_default=True,
    help="Skip partitions with fewer than this many parquet files.",
)
@click.option("--dry-run/--no-dry-run", default=False, show_default=True)
@click.pass_context
def compact_lake(
    ctx: click.Context,
    older_than_days: int,
    min_files: int,
    dry_run: bool,
) -> None:
    """Compact small parquet files within each dt= partition into a single file.

    Iterates over every table under ``data/normalised/`` and every
    ``dt=YYYY-MM-DD`` partition older than the cutoff.  When a partition has
    more than ``--min-files`` parquet files, merges them into a single
    ``part-compacted.parquet`` and deletes the originals.  Idempotent: a
    partition that already holds a single compacted file is skipped.
    """
    settings: Settings = ctx.obj["settings"]
    root = Path(settings.data_root) / "normalised"
    cutoff = date.today() - timedelta(days=older_than_days)
    if not root.exists():
        click.echo(f"no normalised dir at {root}")
        return

    con = duckdb.connect(":memory:")
    total_compacted = 0
    total_saved_bytes = 0

    for table_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for partition in sorted(table_dir.glob("dt=*")):
            try:
                dt_str = partition.name.split("=", 1)[1]
                dt_val = date.fromisoformat(dt_str)
            except (IndexError, ValueError):
                continue
            if dt_val > cutoff:
                continue

            target = partition / "part-compacted.parquet"
            files = sorted(p for p in partition.glob("*.parquet") if p.name != target.name)
            has_target = target.exists()

            # Already a single compacted file with no stragglers — skip.
            if has_target and not files:
                continue

            # Include the existing target so we merge new arrivals into it.
            if has_target:
                files.append(target)

            if len(files) < min_files:
                continue

            size_before = sum(f.stat().st_size for f in files)
            click.echo(
                f"{table_dir.name}/{partition.name}: {len(files)} files, "
                f"{size_before / 1024 / 1024:.2f} MB → compacting"
                + (" (dry-run)" if dry_run else "")
            )
            if dry_run:
                continue

            tmp = partition / "part-compacted.parquet.tmp"
            file_list = "[" + ",".join(f"'{f}'" for f in files) + "]"
            con.execute(
                f"COPY (SELECT * FROM read_parquet({file_list})) "
                f"TO '{tmp}' (FORMAT PARQUET)"
            )
            for f in files:
                if f != target:
                    f.unlink()
            if target.exists():
                target.unlink()
            tmp.replace(target)
            size_after = target.stat().st_size
            saved = size_before - size_after
            total_saved_bytes += saved
            click.echo(
                f"  → {size_after / 1024 / 1024:.2f} MB "
                f"(saved {saved / 1024 / 1024:.2f} MB)"
            )
            total_compacted += 1

    click.echo(
        f"compacted {total_compacted} partition(s), "
        f"saved {total_saved_bytes / 1024 / 1024:.2f} MB total"
    )


@maintenance_cmd.command(name="diagnose-relationships")
@click.option(
    "--output",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="CSV output path. Defaults to data/diagnose_relationships_<date>.csv.",
)
@click.pass_context
def diagnose_relationships(ctx: click.Context, output: Path | None) -> None:
    """Join orders_log with relationship_candidates and write a CSV summary.

    Groups by relationship type/id/questions/confidence and aggregates the
    number of signals and average gross edge each relationship has produced.
    Used to spot which relationship types are actually firing and at what
    edge — there is currently no other visibility into this.
    """
    settings: Settings = ctx.obj["settings"]
    root = Path(settings.data_root) / "normalised"
    orders_glob = root / "orders_log" / "dt=*" / "*.parquet"
    rels_glob = root / "relationship_candidates" / "dt=*" / "*.parquet"

    out = output or (
        Path(settings.data_root)
        / f"diagnose_relationships_{date.today().isoformat()}.csv"
    )
    out.parent.mkdir(parents=True, exist_ok=True)

    sql = f"""
        COPY (
          SELECT
            r.relationship_type AS relationship_type,
            r.relationship_id AS relationship_id,
            r.question_a AS question_a,
            r.question_b AS question_b,
            TRY_CAST(r.confidence AS DOUBLE) AS confidence,
            COUNT(o.intent_id) AS signal_count,
            ROUND(AVG(TRY_CAST(o.avg_fill_price AS DOUBLE)), 4) AS avg_fill_price,
            ROUND(AVG(
              CASE WHEN o.notes LIKE '%gross_edge=%'
                   THEN TRY_CAST(regexp_extract(o.notes, 'gross_edge=([0-9.\\-]+)', 1) AS DOUBLE)
              END
            ), 4) AS avg_gross_edge
          FROM read_parquet('{orders_glob}', hive_partitioning=true) o
          LEFT JOIN read_parquet('{rels_glob}', hive_partitioning=true) r
            ON o.source_relationship_id = r.relationship_id
          GROUP BY r.relationship_type, r.relationship_id,
                   r.question_a, r.question_b, r.confidence
          ORDER BY signal_count DESC
        ) TO '{out}' (HEADER, DELIMITER ',')
    """
    try:
        con = duckdb.connect(":memory:")
        con.execute(sql)
        click.echo(f"wrote {out}")
    except Exception as exc:
        click.echo(f"diagnose-relationships failed: {exc}", err=True)
        raise SystemExit(1) from exc


@maintenance_cmd.command(name="test-connectivity")
@click.pass_context
def test_connectivity(ctx: click.Context) -> None:
    """Run safe end-to-end connectivity checks for Limitless and Polymarket."""
    settings: Settings = ctx.obj["settings"]
    checks = asyncio.run(_run_connectivity_checks(settings))
    for check in checks:
        icon = "✓" if check.ok else "✗"
        click.echo(f"[{icon}] {check.label:<18} — {check.detail}")

    if all(check.ok for check in checks):
        click.echo("All checks passed. Safe to go live.")
        return

    raise SystemExit(1)


async def _run_connectivity_checks(settings: Settings) -> list[_ConnectivityCheck]:
    checks: list[_ConnectivityCheck] = []
    async with AsyncHttpClient(settings.http) as http:
        checks.append(await _check_limitless_auth(settings, http))
        checks.append(await _check_limitless_market(settings, http))

    checks.append(await asyncio.to_thread(_check_polymarket_auth, settings))
    checks.append(await asyncio.to_thread(_check_polymarket_book, settings))
    return checks


async def _check_limitless_auth(
    settings: Settings,
    http: AsyncHttpClient,
) -> _ConnectivityCheck:
    label = "Limitless auth"
    try:
        creds = _load_limitless_credentials()
        path = f"/profiles/public/{creds['wallet_address']}"
        headers = sign_request(
            key_id=creds["key_id"],
            key_secret=creds["key_secret"],
            method="GET",
            path=path,
            body="",
        )
        payload = await http.request_json(
            "GET",
            f"{settings.limitless_host.rstrip('/')}{path}",
            headers=headers,
        )
        if not isinstance(payload, dict):
            raise RuntimeError(f"profile response was not an object: {payload!r}")
        owner_id = payload.get("owner_id", payload.get("id"))
        if owner_id in (None, ""):
            raise RuntimeError(f"profile response missing owner_id/id: {payload!r}")
        return _ConnectivityCheck(label, True, f"owner_id={owner_id}")
    except Exception as exc:
        return _ConnectivityCheck(label, False, _format_error(exc))


async def _check_limitless_market(
    settings: Settings,
    http: AsyncHttpClient,
) -> _ConnectivityCheck:
    label = "Limitless market"
    try:
        payload = await http.request_json(
            "GET",
            f"{settings.limitless_host.rstrip('/')}/markets",
            params={"limit": 1},
        )
        raw_market = _first_market_payload(payload)
        if raw_market is None:
            raise RuntimeError(f"market response contained no market object: {payload!r}")
        market = parse_limitless_market(raw_market)
        if market is None:
            raise RuntimeError(f"parser rejected market payload: {raw_market!r}")
        if not market.address:
            raise RuntimeError(f"parsed market missing address: {raw_market!r}")
        title = market.title or market.slug
        return _ConnectivityCheck(
            label,
            True,
            f"found market {_quote_truncated(title, 32)} address={_abbrev(market.address)}",
        )
    except Exception as exc:
        return _ConnectivityCheck(label, False, _format_error(exc))


def _check_polymarket_auth(settings: Settings) -> _ConnectivityCheck:
    label = "Polymarket auth"
    try:
        creds = _load_polymarket_credentials()
        client = _build_polymarket_client(settings, creds)
        payload = client.get_api_keys()
        if payload in (None, ""):
            raise RuntimeError("get_api_keys returned an empty response")
        return _ConnectivityCheck(
            label,
            True,
            f"api_key={_abbrev(creds['api_key'])} active",
        )
    except Exception as exc:
        return _ConnectivityCheck(label, False, _format_error(exc))


def _check_polymarket_book(settings: Settings) -> _ConnectivityCheck:
    label = "Polymarket book"
    try:
        token_id = _first_relationship_token(settings.data_root)
        if not token_id:
            raise RuntimeError(
                "no token IDs found in relationship_candidates lake "
                f"under {settings.data_root}"
            )
        creds = _load_polymarket_credentials()
        client = _build_polymarket_client(settings, creds)
        book = client.get_order_book(token_id)
        bid = _best_price(getattr(book, "bids", None), best=max)
        ask = _best_price(getattr(book, "asks", None), best=min)
        if bid is None and ask is None:
            raise RuntimeError(f"order book contained no bids or asks: {book!r}")
        return _ConnectivityCheck(
            label,
            True,
            f"token {_abbrev(token_id)} bid={_format_price(bid)} ask={_format_price(ask)}",
        )
    except Exception as exc:
        return _ConnectivityCheck(label, False, _format_error(exc))


def _load_limitless_credentials() -> dict[str, str]:
    lim = _load_secret_json("limitless/api_credentials")
    poly = _load_secret_json("polymarket/api_credentials")
    try:
        from eth_account import Account  # type: ignore[import-untyped]

        private_key = str(poly["private_key"])
        return {
            "key_id": str(lim["key_id"]),
            "key_secret": str(lim["key_secret"]),
            "wallet_address": str(poly.get("wallet_address") or Account.from_key(private_key).address),
        }
    except Exception as exc:
        raise RuntimeError(
            "failed to load Limitless credentials or derive wallet address "
            "from polymarket/api_credentials"
        ) from exc


def _load_polymarket_credentials() -> dict[str, str]:
    secret = _load_secret_json("polymarket/api_credentials")
    field_names = ("private_key", "api_key", "api_secret", "api_passphrase")
    missing = [name for name in field_names if not secret.get(name)]
    if missing:
        raise RuntimeError(
            "polymarket/api_credentials missing required field(s): "
            + ", ".join(missing)
        )
    return {name: str(secret[name]) for name in field_names}


def _load_secret_json(secret_id: str) -> dict[str, Any]:
    try:
        import boto3  # type: ignore[import-untyped]

        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "eu-west-1"
        sm = boto3.client("secretsmanager", region_name=region)
        value = sm.get_secret_value(SecretId=secret_id)
        payload = json.loads(value["SecretString"])
        if not isinstance(payload, dict):
            raise RuntimeError(f"{secret_id} SecretString is not a JSON object")
        return payload
    except Exception as exc:
        raise RuntimeError(f"failed to load {secret_id} from Secrets Manager") from exc


def _build_polymarket_client(settings: Settings, creds: dict[str, str]) -> Any:
    return build_clob_client(
        private_key_hex=creds["private_key"],
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        api_passphrase=creds["api_passphrase"],
        funder=(
            creds.get("funder", "")
            or creds.get("funder_address", "")
            or creds.get("proxy_wallet_address", "")
            or settings.polymarket_funder
        ),
        chain_id=settings.polymarket_chain_id,
        host=settings.polymarket_clob_host,
    )


def _first_relationship_token(data_root: Path) -> str | None:
    repo = ParquetRelationshipCandidatesRepository(data_root)
    for row in repo.iter_latest():
        for token in (
            row.token_id_a_yes,
            row.token_id_a_no,
            row.token_id_b_yes,
            row.token_id_b_no,
        ):
            if token:
                return str(token)
    return None


def _first_market_payload(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        return next((item for item in payload if isinstance(item, dict)), None)
    if isinstance(payload, dict):
        for key in ("data", "markets", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return next((item for item in value if isinstance(item, dict)), None)
        return payload if payload else None
    return None


def _best_price(levels: Any, *, best: Any) -> Decimal | None:
    if not levels:
        return None
    prices: list[Decimal] = []
    for level in levels:
        price = getattr(level, "price", None)
        if price is None and isinstance(level, dict):
            price = level.get("price")
        if price in (None, ""):
            continue
        try:
            prices.append(Decimal(str(price)))
        except (InvalidOperation, ValueError):
            continue
    return best(prices) if prices else None


def _format_price(price: Decimal | None) -> str:
    if price is None:
        return "n/a"
    return str(price.normalize())


def _abbrev(value: str, *, prefix: int = 6, suffix: int = 3) -> str:
    if len(value) <= prefix + suffix + 3:
        return value
    return f"{value[:prefix]}...{value[-suffix:]}"


def _quote_truncated(value: str, limit: int) -> str:
    if len(value) > limit:
        value = f"{value[:limit - 3]}..."
    return repr(value)


def _format_error(exc: BaseException) -> str:
    parts = [f"{type(exc).__name__}: {exc}"]
    response = getattr(exc, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None)
        text = getattr(response, "text", "")
        if status_code is not None:
            parts.append(f"status={status_code}")
        if text:
            parts.append(f"body={text}")
    cause = exc.__cause__
    if cause is not None:
        parts.append(f"cause={type(cause).__name__}: {cause}")
        cause_response = getattr(cause, "response", None)
        if cause_response is not None:
            status_code = getattr(cause_response, "status_code", None)
            text = getattr(cause_response, "text", "")
            if status_code is not None:
                parts.append(f"cause_status={status_code}")
            if text:
                parts.append(f"cause_body={text}")
    return " | ".join(parts)


__all__ = ["maintenance_cmd"]
