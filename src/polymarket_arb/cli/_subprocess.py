"""Helpers for CLI commands that intentionally chain existing subcommands."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence

from ..settings import Settings


def run_cli_subcommand(args: Sequence[str], settings: Settings) -> None:
    env = os.environ.copy()
    env["POLYMARKET_ARB_STORAGE__DATA_ROOT"] = str(settings.data_root)
    env["POLYMARKET_ARB_GAMMA_HOST"] = settings.gamma_host
    env["POLYMARKET_ARB_CLOB_HOST"] = settings.clob_host
    subprocess.run(
        [sys.executable, "-m", "polymarket_arb.cli", *args],
        check=True,
        env=env,
    )
