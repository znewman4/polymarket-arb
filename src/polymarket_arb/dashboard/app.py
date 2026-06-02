"""Flask app factory for the read-only paper-trading dashboard."""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Flask

from ..settings import Settings
from .cache import DashboardCache
from .queries import DuckDBQueryService
from .routes import bp as dashboard_bp


def create_app(settings: Settings) -> Flask:
    app = Flask(__name__)
    qs = DuckDBQueryService(settings.data_root)
    app.extensions["dashboard_db"] = qs
    app.extensions["dashboard_cache"] = DashboardCache(qs)
    app.config["LIMITLESS_PAPER_MODE"] = settings.limitless_paper_mode
    app.config["RELATIONSHIP_PAPER_MODE"] = settings.paper_mode
    app.register_blueprint(dashboard_bp)

    @app.template_filter("datetimeformat")
    def _datetimeformat(value: int | None) -> str:
        if value is None:
            return "—"
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    @app.context_processor
    def _dashboard_context() -> dict[str, str]:
        return {
            "last_updated_utc": datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        }

    return app


__all__ = ["create_app"]
