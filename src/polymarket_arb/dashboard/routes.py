"""Flask blueprint: dashboard pages + JSON health + CSV export.

Routes are deliberately thin — they parse query args, call into
``DuckDBQueryService``, and render a Jinja template (or return JSON / stream
CSV).  All data-shape logic lives in ``queries.py``.
"""

from __future__ import annotations

import copy
import csv
import io
from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, render_template, request

from .cache import DashboardCache
from .queries import DuckDBQueryService

bp = Blueprint("dashboard", __name__)


def _cache() -> DashboardCache:
    return current_app.extensions["dashboard_cache"]


def _qs() -> DuckDBQueryService:
    return current_app.extensions["dashboard_db"]


_LOADING_PAGE = (
    "<html><head><meta http-equiv='refresh' content='10'></head>"
    "<body>Loading dashboard, please wait...</body></html>"
)


@bp.route("/")
def overview() -> str:
    summary = copy.deepcopy(_cache().get("overview_summary"))
    if not summary:
        summary = _qs().overview_summary()
    summary["limitless_arb"]["mode"] = (
        "PAPER" if current_app.config.get("LIMITLESS_PAPER_MODE", True) else "LIVE"
    )
    summary["relationship_agent"]["mode"] = (
        "PAPER" if current_app.config.get("RELATIONSHIP_PAPER_MODE", True) else "LIVE"
    )
    return render_template(
        "overview.html",
        summary=summary,
        auto_refresh_seconds=60,
        active_page="overview",
    )


@bp.route("/orders")
def orders() -> str:
    qs = _qs()
    strategy_id = request.args.get("strategy_id") or None
    status = request.args.get("status") or None
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    data = qs.orders_page(
        strategy_id=strategy_id,
        status=status,
        date_from=date_from,
        date_to=date_to,
        page=page,
        per_page=50,
    )
    return render_template(
        "orders.html",
        data=data,
        filters={
            "strategy_id": strategy_id or "",
            "status": status or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
        },
        active_page="orders",
        auto_refresh_seconds=60,
    )


@bp.route("/orders.csv")
def orders_csv() -> Response:
    qs = _qs()
    strategy_id = request.args.get("strategy_id") or None
    status = request.args.get("status") or None
    date_from = request.args.get("date_from") or None
    date_to = request.args.get("date_to") or None

    def generate():
        header_written = False
        for cols, rows in qs.iter_orders_for_csv(
            strategy_id=strategy_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
        ):
            if not header_written:
                buf = io.StringIO()
                csv.writer(buf).writerow(cols)
                yield buf.getvalue()
                header_written = True
            if rows:
                buf = io.StringIO()
                csv.writer(buf).writerows(rows)
                yield buf.getvalue()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        generate(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="orders_{today}.csv"',
        },
    )


@bp.route("/trades")
def trades() -> str:
    qs = _qs()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    data = qs.tradebook_page(page=page, per_page=50)
    return render_template(
        "tradebook.html",
        data=data,
        active_page="trades",
        auto_refresh_seconds=60,
    )


@bp.route("/trades.csv")
def trades_csv() -> Response:
    qs = _qs()

    def generate():
        header_written = False
        for cols, rows in qs.iter_tradebook_for_csv():
            if not header_written:
                buf = io.StringIO()
                csv.writer(buf).writerow(cols)
                yield buf.getvalue()
                header_written = True
            if rows:
                buf = io.StringIO()
                csv.writer(buf).writerows(rows)
                yield buf.getvalue()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        generate(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="trades_{today}.csv"',
        },
    )


@bp.route("/positions")
def positions() -> str:
    data = _cache().get("open_positions", [])
    summary = {
        "count": len(data),
        "total_cost_basis": round(sum(float(row.get("notional_usdc") or 0) for row in data), 2),
        "total_mtm_pnl": round(sum(float(row.get("mtm_pnl") or 0) for row in data), 4),
        "total_locked_profit": round(sum(float(row.get("locked_profit") or 0) for row in data), 4),
    }
    return render_template(
        "positions.html",
        positions=data,
        summary=summary,
        auto_refresh_seconds=60,
        active_page="positions",
    )


@bp.route("/live")
def live_monitor() -> str:
    qs = _qs()
    data = qs.live_monitor_data()
    return render_template(
        "live_monitor.html",
        data=data,
        active_page="live",
        auto_refresh_seconds=60,
    )


@bp.route("/arb")
def arb_positions() -> str:
    qs = _qs()
    open_positions = qs.open_arb_positions()
    closed_positions = qs.closed_arb_positions()
    realised = [
        float(row.get("realised_profit") or 0.0)
        for row in closed_positions
    ]
    wins = sum(1 for pnl in realised if pnl > 0)
    summary = {
        "total_realised_pnl": round(sum(realised), 4),
        "open_count": len(open_positions),
        "closed_count": len(closed_positions),
        "win_rate_pct": round((wins / len(realised) * 100.0) if realised else 0.0, 1),
    }
    return render_template(
        "arb_positions.html",
        kill_switches=qs.arb_kill_switch_status(),
        open_positions=open_positions,
        closed_positions=closed_positions,
        summary=summary,
        active_page="arb",
        auto_refresh_seconds=60,
    )


@bp.route("/relationships")
def relationship_monitor() -> str:
    qs = _qs()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    min_confidence = 0.85
    summary = qs.relationship_candidates_summary(min_confidence=min_confidence)
    open_trades = qs.relationship_open_trades()
    closed_trades = qs.relationship_closed_trades()
    realised = [float(row.get("realised_pnl") or 0.0) for row in closed_trades]
    browser = qs.relationship_browser(
        min_confidence=min_confidence,
        page=page,
        per_page=50,
    )
    summary = {
        **summary,
        "open_trades": len(open_trades),
        "closed_trades": len(closed_trades),
        "realised_pnl": round(sum(realised), 4),
    }
    return render_template(
        "relationship_monitor.html",
        summary=summary,
        open_trades=open_trades,
        closed_trades=closed_trades,
        browser=browser,
        active_page="relationships",
        auto_refresh_seconds=60,
    )


@bp.route("/signals")
def signals() -> str:
    cache = _cache()
    return render_template(
        "signals.html",
        no_fill=cache.get("no_fill_breakdown", []),
        edge_dist=cache.get("edge_distribution", []),
        limitless_gaps=cache.get("limitless_open_gaps", []),
        active_page="signals",
        auto_refresh_seconds=60,
    )


@bp.route("/markets")
def markets() -> str:
    cache = _cache()
    return render_template(
        "markets.html",
        coverage=cache.get("market_coverage", {"total_markets": 0, "active_markets": 0, "markets_with_book_today": 0}),
        by_type=cache.get("relationship_type_breakdown", []),
        top_pairs=cache.get("markets_with_most_relationships", []),
        active_page="markets",
        auto_refresh_seconds=60,
    )


@bp.route("/health")
def health() -> Response:
    return Response("ok", status=200, mimetype="text/plain")
