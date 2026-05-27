"""``polymarket-arb limitless ...`` -- Limitless x Polymarket cross-market arb.

``scan-arb``: fetches live prices from both venues, fuzzy-matches by question
text, computes arb gaps, displays a rich table, and optionally executes both
legs (paper or live) when opportunities are found.
"""

from __future__ import annotations

import asyncio
import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table

from ..http.client import AsyncHttpClient, HttpError
from ..ingest.gamma.client import GammaClient
from ..ingest.limitless.client import LimitlessClient
from ..limitless.arb_scanner import compute_arb, execute_arb, match_markets
from ..limitless.models import ArbMatch
from ..live.order_client import OrderClient
from ..settings import Settings
from ..storage.parquet.orders_log_repo import ParquetOrdersLogRepository
from ..storage.parquet.raw_writer import RawWriter

console = Console()


@click.group(name="limitless")
def limitless_cmd() -> None:
    """Limitless x Polymarket cross-market arbitrage scanner."""


# ─── scan-arb ────────────────────────────────────────────────────────────────


@limitless_cmd.command(name="scan-arb")
@click.option("--tolerance", type=float, default=0.02, show_default=True,
              help="Arb-gap threshold to classify as ARB_OPPORTUNITY.")
@click.option("--threshold", type=float, default=0.82, show_default=True,
              help="Fuzzy-match minimum score 0-1 for question matching.")
@click.option("--stake-usdc", type=float, default=50.0, show_default=True,
              help="USDC size per leg when executing.")
@click.option("--min-net-edge", type=float, default=0.02, show_default=True,
              help="Minimum arb gap required before executing a trade.")
@click.option("--execute/--no-execute", default=False, show_default=True,
              help="Place orders on matched opportunities (paper or live).")
@click.option("--csv/--no-csv", "write_csv", default=True, show_default=True,
              help="Save all results to a timestamped CSV file.")
@click.option("--trade-type",
              type=click.Choice(["amm", "clob", "all"], case_sensitive=False),
              default="all", show_default=True,
              help="Filter Limitless markets by trade type.")
@click.option("--max-poly-pages", type=int, default=20, show_default=True,
              help="Max Gamma pages to fetch (100 markets each).")
@click.pass_context
def scan_arb(
    ctx: click.Context,
    tolerance: float,
    threshold: float,
    stake_usdc: float,
    min_net_edge: float,
    execute: bool,
    write_csv: bool,
    trade_type: str,
    max_poly_pages: int | None,
) -> None:
    """Scan Limitless and Polymarket for pricing discrepancies.

    Fetches live prices from both venues, fuzzy-matches markets by question
    text, and shows a table of arbitrage opportunities.  With --execute,
    places paper (or live) orders on every opportunity above --min-net-edge.
    """
    settings: Settings = ctx.obj["settings"]
    started = time.monotonic()

    console.rule("[bold blue]Limitless x Polymarket Arb Scanner[/bold blue]")

    results = asyncio.run(_run_scan(
        settings,
        tolerance=tolerance,
        threshold=threshold,
        stake_usdc=stake_usdc,
        min_net_edge=min_net_edge,
        execute=execute,
        trade_type=trade_type if trade_type != "all" else None,
        max_poly_pages=max_poly_pages,
    ))

    elapsed = time.monotonic() - started
    console.print(f"[dim]Scan completed in {elapsed:.1f}s[/dim]\n")

    _display_results(results, tolerance=tolerance)

    if write_csv:
        _save_csv(results, settings.data_root)


async def _run_scan(
    settings: Settings,
    *,
    tolerance: float,
    threshold: float,
    stake_usdc: float,
    min_net_edge: float,
    execute: bool,
    trade_type: str | None,
    max_poly_pages: int | None,
) -> list[ArbMatch]:
    raw_writer = RawWriter(settings.data_root)

    async with AsyncHttpClient(settings.http) as http:
        lim_client_obj = LimitlessClient(
            limitless_host=settings.limitless_host,
            http=http,
            raw_writer=raw_writer,
        )
        gamma_client = GammaClient(
            gamma_host=settings.gamma_host,
            http=http,
            raw_writer=raw_writer,
        )

        console.log("[cyan]Fetching Limitless markets…[/cyan]")
        lim_markets = await lim_client_obj.fetch_all_markets(trade_type=trade_type)

        console.log("[cyan]Fetching Polymarket markets…[/cyan]")
        poly_raw = await _fetch_poly_raw(gamma_client, max_pages=max_poly_pages)

    console.log(f"[cyan]Limitless: {len(lim_markets)} binary markets[/cyan]")
    console.log(f"[cyan]Polymarket: {len(poly_raw)} markets[/cyan]")

    matches = match_markets(lim_markets, poly_raw, threshold=threshold)
    results = compute_arb(matches, tolerance=tolerance)

    if execute:
        results = await _run_execute(
            settings=settings,
            results=results,
            lim_client_obj=lim_client_obj,
            stake_usdc=stake_usdc,
            min_net_edge=min_net_edge,
        )

    return results


async def _fetch_poly_raw(
    gamma_client: GammaClient,
    max_pages: int | None,
) -> list[dict[str, Any]]:
    """Collect raw Gamma market dicts (not parsed MarketRow) for arb matching."""
    raw_markets: list[dict[str, Any]] = []
    try:
        async for raw in gamma_client.iter_raw_markets(active=True, closed=False, max_pages=max_pages):
            raw_markets.append(raw)
    except HttpError as exc:
        if "422" in str(exc):
            pass  # Gamma pagination limit reached — use what we have
        else:
            raise
    return raw_markets


async def _run_execute(
    settings: Settings,
    results: list[ArbMatch],
    lim_client_obj: LimitlessClient,
    stake_usdc: float,
    min_net_edge: float,
) -> list[ArbMatch]:
    opps = [r for r in results if r.status == "ARB_OPPORTUNITY"]
    if not opps:
        return results

    paper_mode = settings.limitless_paper_mode
    console.log(
        f"[yellow]Executing {len(opps)} opportunit(ies) "
        f"({'PAPER' if paper_mode else 'LIVE'})…[/yellow]"
    )

    key_id: str | None = None
    key_secret: str | None = None
    wallet_address: str | None = None
    private_key: str | None = None
    if not paper_mode:
        key_id, key_secret, wallet_address, private_key = _load_limitless_creds()

    orders_log_repo = ParquetOrdersLogRepository(settings.data_root)
    poly_client = OrderClient(settings)

    async with AsyncHttpClient(settings.http) as http:
        from ..limitless.order_client import LimitlessOrderClient
        lim_order_client = LimitlessOrderClient(
            limitless_host=settings.limitless_host,
            http=http,
            kill_switch_path=settings.killswitch_path,
            paper_mode=paper_mode,
            key_id=key_id,
            key_secret=key_secret,
            wallet_address=wallet_address,
            private_key=private_key,
        )

        for match in opps:
            lim_res, poly_res = await execute_arb(
                match,
                lim_client=lim_order_client,
                poly_client=poly_client,
                stake_usdc=stake_usdc,
                min_net_edge=min_net_edge,
                orders_log_repo=orders_log_repo,
            )
            poly_status = poly_res.status if poly_res is not None else "skipped"
            console.log(
                f"  {match.limitless.slug[:40]}: "
                f"lim={lim_res.status} poly={poly_status}"
            )

    return results


def _load_limitless_creds() -> tuple[str, str, str, str]:
    """Load Limitless API credentials, private key, and derive wallet address.

    Returns:
        (key_id, key_secret, wallet_address, private_key)

    key_id and key_secret come from the ``limitless/api_credentials`` secret.
    private_key and wallet_address come from ``polymarket/api_credentials``
    (private_key field); wallet_address is derived via
    ``eth_account.Account.from_key(private_key).address``.
    """
    try:
        import boto3  # type: ignore[import-untyped]
        sm = boto3.client("secretsmanager", region_name="eu-west-1")

        lim_secret = sm.get_secret_value(SecretId="limitless/api_credentials")
        creds = json.loads(lim_secret["SecretString"])
        key_id: str = creds["key_id"]
        key_secret: str = creds["key_secret"]
    except Exception as exc:
        raise click.ClickException(
            f"Failed to load limitless/api_credentials from Secrets Manager: {exc}\n"
            "Run: aws secretsmanager create-secret --name limitless/api_credentials "
            "--secret-string '{\"key_id\":\"...\",\"key_secret\":\"...\"}' --region eu-west-1"
        ) from exc

    try:
        from eth_account import Account  # type: ignore[import-untyped]
        poly_secret = json.loads(
            sm.get_secret_value(SecretId="polymarket/api_credentials")["SecretString"]
        )
        private_key: str = poly_secret["private_key"]
        wallet_address: str = Account.from_key(private_key).address
    except Exception as exc:
        raise click.ClickException(
            f"Failed to derive wallet address from polymarket/api_credentials: {exc}\n"
            "Ensure the 'polymarket/api_credentials' secret contains a 'private_key' field."
        ) from exc

    return key_id, key_secret, wallet_address, private_key


# ─── display ─────────────────────────────────────────────────────────────────

_STATUS_STYLE = {
    "ARB_OPPORTUNITY": "[bold green]✓ ARB[/bold green]",
    "OVER_ROUND":      "[yellow]OVER[/yellow]",
    "EFFICIENT":       "[dim]eff[/dim]",
}


def _display_results(results: list[ArbMatch], tolerance: float) -> None:
    opps = [r for r in results if r.status == "ARB_OPPORTUNITY"]

    if not opps:
        console.print(
            f"\n[bold yellow]No arbitrage opportunities found "
            f"(tolerance=±{tolerance}, {len(results)} pairs matched).[/bold yellow]\n"
        )
        console.print(
            "[dim]Tip: try --tolerance 0.05 or --threshold 0.75 to widen the net.[/dim]\n"
        )
        return

    table = Table(
        title=f"⚡  Arb Opportunities  (tolerance ±{tolerance})",
        show_lines=True,
        header_style="bold magenta",
    )
    table.add_column("Market", style="white", max_width=44, overflow="fold")
    table.add_column("Lim p(Yes)", style="cyan", justify="right")
    table.add_column("Poly p(Yes)", style="green", justify="right")
    table.add_column("Sum", style="yellow", justify="right")
    table.add_column("Gap", style="bold green", justify="right")
    table.add_column("Sim", justify="right")
    table.add_column("Status", style="bold white", justify="center")

    for r in opps:
        table.add_row(
            r.limitless.title or r.limitless.slug,
            f"{r.limitless.yes_price:.4f}",
            f"{r.poly.yes_price:.4f}",
            f"{r.limitless.yes_price + r.poly.yes_price:.4f}",
            f"{r.arb_gap:+.4f}",
            f"{r.similarity:.2f}",
            _STATUS_STYLE.get(r.status, r.status),
        )

    console.print(table)
    console.print(
        f"\n[bold green]{len(opps)} opportunit(ies) from "
        f"{len(results)} matched pairs.[/bold green]\n"
    )


# ─── CSV output ──────────────────────────────────────────────────────────────

_CSV_FIELDS = [
    "limitless_slug", "limitless_title", "poly_condition_id", "poly_question",
    "limitless_yes", "poly_yes", "total", "arb_gap", "similarity", "status",
]


def _save_csv(results: list[ArbMatch], data_root: Path) -> None:
    out_dir = data_root / "cross_market_arb"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"arb_{ts}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "limitless_slug":    r.limitless.slug,
                "limitless_title":   r.limitless.title,
                "poly_condition_id": r.poly.condition_id,
                "poly_question":     r.poly.question,
                "limitless_yes":     round(r.limitless.yes_price, 6),
                "poly_yes":          round(r.poly.yes_price, 6),
                "total":             round(r.limitless.yes_price + r.poly.yes_price, 6),
                "arb_gap":           round(r.arb_gap, 6),
                "similarity":        round(r.similarity, 4),
                "status":            r.status,
            })
    console.print(f"[dim]Results saved to[/dim] [bold]{csv_path}[/bold]")
