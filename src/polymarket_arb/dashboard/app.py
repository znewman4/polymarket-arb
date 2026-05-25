"""Flask app factory for the read-only paper-trading dashboard."""

from __future__ import annotations

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
    app.register_blueprint(dashboard_bp)
    return app


__all__ = ["create_app"]
