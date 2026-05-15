"""File- and signal-based kill switch.

A single ``data/.killswitch`` file is the canonical "stop everything" trigger.
SIGUSR1 also latches the in-process flag so a Slack webhook (Phase 7+) can
trigger a stop without filesystem access.
"""

from __future__ import annotations

import contextlib
import signal
import threading
from pathlib import Path

from loguru import logger

_LOCK = threading.Lock()
_LATCHED = False


def _on_sigusr1(signum: int, frame: object | None) -> None:  # pragma: no cover
    global _LATCHED
    with _LOCK:
        _LATCHED = True
    logger.warning("kill switch latched via SIGUSR1")


def install_signal_handler() -> None:
    """Install the SIGUSR1 latch handler. Idempotent. Skipped on platforms
    that lack SIGUSR1 (Windows)."""

    if not hasattr(signal, "SIGUSR1"):
        return
    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGUSR1, _on_sigusr1)


def is_active(killswitch_path: Path) -> bool:
    """Return True if the kill switch file exists or SIGUSR1 was latched."""

    with _LOCK:
        latched = _LATCHED
    return latched or killswitch_path.exists()


def reset_for_tests() -> None:
    global _LATCHED
    with _LOCK:
        _LATCHED = False
