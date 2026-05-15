"""Loguru setup: structured JSON to disk, pretty to console."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from .settings import Settings

_CONFIGURED = False


def configure_logging(settings: Settings) -> None:
    """Idempotent loguru configuration.

    Console handler emits human-friendly text. File handler writes
    one JSON object per line so Phase 7+ Grafana / loki ingestion is trivial.
    """

    global _CONFIGURED
    if _CONFIGURED:
        return

    logger.remove()
    log_path = settings.logging.json_log_path
    if not log_path.is_absolute():
        log_path = (Path(__file__).resolve().parents[2] / log_path).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger.add(
        sys.stderr,
        level=settings.logging.level,
        backtrace=False,
        diagnose=False,
        format=("<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | "
                "<cyan>{name}</cyan>:<cyan>{line}</cyan> - {message}"),
    )
    logger.add(
        str(log_path),
        level=settings.logging.level,
        rotation=settings.logging.rotation,
        retention=settings.logging.retention,
        serialize=True,
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )
    logger.debug("logging configured", level=settings.logging.level, path=str(log_path))
    _CONFIGURED = True


def reset_logging_for_tests() -> None:
    """Tests use this to force re-configuration with a fresh sink."""

    global _CONFIGURED
    logger.remove()
    _CONFIGURED = False
