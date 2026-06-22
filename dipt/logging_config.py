"""Centralised logging configuration for the DIPT system.

Call :func:`configure_logging` once at application start-up (entry points
only). Library modules should never configure logging themselves; they simply
obtain a logger via ``logging.getLogger(__name__)``.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False

_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger for console output.

    Idempotent: repeated calls have no additional effect. This should be
    invoked exactly once, from an application entry point.

    Args:
        level: Root log level name (e.g. ``"INFO"``, ``"DEBUG"``).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))

    root = logging.getLogger()
    root.setLevel(level.upper())
    root.addHandler(handler)

    # Silence noisy third-party libraries.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    _CONFIGURED = True
