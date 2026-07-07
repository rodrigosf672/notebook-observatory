"""Notebook Observatory — a daily census of the public computational notebook ecosystem.

The package is organized into focused sub-modules:

- :mod:`notebook_observatory.github_client` — rate-limited, retrying GitHub REST client.
- :mod:`notebook_observatory.pypi_client` — PyPI download-context client.
- :mod:`notebook_observatory.collectors` — repository/notebook sampling and collection.
- :mod:`notebook_observatory.parsers` — notebook feature extraction.
- :mod:`notebook_observatory.detection` — extensible library detection registry.
- :mod:`notebook_observatory.analytics` — per-notebook metrics and daily aggregation.
- :mod:`notebook_observatory.storage` — append-only longitudinal dataset storage.
- :mod:`notebook_observatory.dashboard` — static dashboard site generation.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
