"""Flask blueprint: dashboard pages + JSON health + CSV export.

Routes are deliberately thin — they parse query args, call into
``DuckDBQueryService``, and render a Jinja template (or return JSON / stream
CSV).  All data-shape logic lives in ``queries.py``.
"""

from __future__ import annotations

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
    cache = _cache()
    counters = cache.get("overview_counters")
    if counters is None:
        # Cache still warming — auto-refresh every 10s
        return _LOADING_PAGE, 202
    return render_template(
        "overview.html",
        counters=counters,
        by_strategy=cache.get("signals_by_strategy", []),
        per_hour=cache.get("signals_per_hour_last_24h", []),
        top_markets=cache.get("top_markets_by_signal", []),
        health=cache.get("health_snapshot", {}),
        cumulative_notional=cache.get("cumulative_notional", []),
        expected_pnl=cache.get(
            "expected_pnl",
            {
                "total_expected_pnl": 0.0,
                "total_cost_basis": 0.0,
                "expected_return_pct": 0.0,
                "trade_count": 0,
            },
        ),
        sharpe=cache.get("sharpe_stats", {"sharpe": None, "days_of_data": 0}),
        auto_refresh_seconds=30,
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
    return render_template("tradebook.html", data=data, active_page="trades")


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
        auto_refresh_seconds=30,
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
        auto_refresh_seconds=10,
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
    )


@bp.route("/health")
def health() -> Response:
    return Response("ok", status=200, mimetype="text/plain")
