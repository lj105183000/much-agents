"""Console setup that behaves on Windows GBK terminals."""

from __future__ import annotations

import logging
import sys
from typing import Callable


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def configure_logging(level: str = "INFO", *, verbose: bool = False, quiet: bool = False) -> None:
    resolved = "DEBUG" if verbose else ("ERROR" if quiet else level.upper())
    logging.basicConfig(
        level=getattr(logging, resolved, logging.INFO),
        stream=sys.stderr,
        format="%(levelname)s %(message)s",
        force=True,
    )


def make_progress(verbose: bool = False) -> Callable[[str, int, str], None]:
    def progress(stage: str, percent: int, message: str) -> None:
        if verbose:
            print(f"[{stage}] {percent:3d}% {message}", file=sys.stderr, flush=True)
        else:
            print(f"[{stage}] {message}", file=sys.stderr, flush=True)

    return progress
