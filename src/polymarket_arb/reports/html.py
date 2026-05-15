"""Jinja2 HTML report renderer."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

_ENV: Environment | None = None


def _basename(path: str | None) -> str:
    if not path:
        return ""
    from pathlib import Path as _P
    return _P(path).name


def _env() -> Environment:
    global _ENV
    if _ENV is None:
        _ENV = Environment(
            loader=PackageLoader("polymarket_arb.reports", "templates"),
            autoescape=select_autoescape(["html"]),
        )
        _ENV.filters["basename"] = _basename
    return _ENV


def render_report(template_name: str, context: dict, output_path: Path) -> Path:
    """Render a Jinja2 template to ``output_path`` and return it."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    template = _env().get_template(template_name)
    html = template.render(**context)
    output_path.write_text(html, encoding="utf-8")
    return output_path
