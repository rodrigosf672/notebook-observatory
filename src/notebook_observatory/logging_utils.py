"""Structured, consistent logging for the whole package.

A single :func:`get_logger` entry point configures a shared handler once. Log
level is controlled by the ``NBOBS_LOG_LEVEL`` environment variable (default
``INFO``). Timestamps are UTC and ISO-8601-ish for grep-friendly CI logs.
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def _configure_root() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = os.environ.get("NBOBS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(stream=sys.stderr)
    fmt = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
    handler.setFormatter(logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S"))

    root = logging.getLogger("notebook_observatory")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger under the ``notebook_observatory`` root.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A configured :class:`logging.Logger`.
    """
    _configure_root()
    if not name.startswith("notebook_observatory"):
        name = f"notebook_observatory.{name}"
    return logging.getLogger(name)
