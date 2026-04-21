"""Structured logging to file + stderr."""
from __future__ import annotations

import sys

from loguru import logger

from .. import paths

_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    paths.ensure_dirs()
    logger.remove()
    logger.add(sys.stderr, level=level, enqueue=False)
    logger.add(
        paths.logs_dir() / "brokerledger.log",
        rotation="5 MB",
        retention=5,
        level="DEBUG",
        enqueue=True,
    )
    _configured = True


__all__ = ["configure_logging", "logger"]
