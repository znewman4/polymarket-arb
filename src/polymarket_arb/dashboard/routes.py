"""Flask blueprint: dashboard pages + JSON health + CSV export.

Routes are deliberately thin — they parse query args, call into
``DuckDBQueryService``, and render a Jinja template (or return JSON / stream
CSV).  All data-shape logic lives in ``queries.py``.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, jsonify, render_template, request

from .queries import DuckDBQueryService

bp = Blueprint("dashboard", __name__)


def _qs() -> DuckDBQueryService:
    return current_app.extensions["dashboard_db"]


@bp.route("/")
def overview() -> str:
    qs = _qs()
    counters = qs.overview_counters()
    by_strategy = qs.signals_by_strategy()
    per_hour = qs.signals_per_hour_last_24h()
    top_markets = qs.top_markets_by_signal(limit=10)
    health = qs.health_snapshot()
    return render_template(
        "overview.html",
        counters=counters,
        by_strategy=by_strategy,
        per_hour=per_hour,
        top_markets=top_markets,
        health=health,
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


@bp.route("/signals")
def signals() -> str:
    qs = _qs()
    return render_template(
        "signals.html",
        no_fill=qs.no_fill_breakdown(),
        edge_dist=qs.edge_distribution(),
        limitless_gaps=qs.limitless_open_gaps(),
        active_page="signals",
    )


@bp.route("/markets")
def markets() -> str:
    qs = _qs()
    return render_template(
        "markets.html",
        coverage=qs.market_coverage(),
        by_type=qs.relationship_type_breakdown(),
        top_pairs=qs.markets_with_most_relationships(),
        active_page="markets",
    )


@bp.route("/health")
def health() -> Response:
    return jsonify(_qs().health_snapshot())
