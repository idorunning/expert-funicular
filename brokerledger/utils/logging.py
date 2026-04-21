"""Structured logging to file + stderr."""
from __future__ import annotations

import os
import sys

from loguru import logger

from .. import paths

_configured = False

_TRACE_FORMAT = (
    "<green>{time:HH:mm:ss}</green> "
    "<cyan>{module}</cyan> "
    "<level>{level.name[0]}</level> {message}"
)


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    paths.ensure_dirs()
    logger.remove()
    # BROKERLEDGER_TRACE=1 makes the GUI / CLI print INFO lines to stdout in a
    # compact one-line format so the broker can watch the categoriser think
    # live in the terminal. The file sink and stderr sink are unaffected.
    trace_on = os.environ.get("BROKERLEDGER_TRACE", "").lower() in {"1", "true", "yes"}
    stderr_level = "INFO" if trace_on else level
    logger.add(sys.stderr, level=stderr_level, enqueue=False)
    if trace_on:
        logger.add(sys.stdout, level="INFO", format=_TRACE_FORMAT,
                   enqueue=False, colorize=True)
    logger.add(
        paths.logs_dir() / "brokerledger.log",
        rotation="5 MB",
        retention=5,
        level="DEBUG",
        enqueue=True,
    )
    _configured = True


__all__ = ["configure_logging", "logger"]
