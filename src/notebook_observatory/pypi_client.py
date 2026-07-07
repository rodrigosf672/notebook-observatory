"""A small client for PyPI package metadata.

Used to enrich library-adoption signals with ecosystem context (latest version,
summary, release recency). Download *counts* are intentionally not fetched from
the deprecated per-package stats endpoint; instead we surface stable metadata
that is always available from the JSON API. The BigQuery ``pypi.downloads``
dataset is the correct source for volume statistics and is documented in
``docs/METHODOLOGY.md`` as an optional enrichment.
"""

from __future__ import annotations

from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .config import CLIENT
from .logging_utils import get_logger

logger = get_logger(__name__)

PYPI_ROOT = "https://pypi.org/pypi"


class PyPIClient:
    """Fetch stable package metadata from the PyPI JSON API."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": CLIENT.user_agent, "Accept": "application/json"})

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.0, min=1, max=10),
        reraise=True,
    )
    def _get(self, url: str) -> requests.Response:
        return self.session.get(url, timeout=CLIENT.timeout_seconds)

    def package_metadata(self, name: str) -> dict[str, Any] | None:
        """Return ``{name, version, summary, requires_python, releases}`` or None.

        Returns ``None`` when the package does not exist (404) so callers can
        treat absence as a soft signal rather than an error.
        """
        try:
            resp = self._get(f"{PYPI_ROOT}/{name}/json")
        except requests.RequestException as exc:
            logger.debug("PyPI fetch failed for %s: %s", name, exc)
            return None
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            logger.debug("PyPI %s returned %s", name, resp.status_code)
            return None
        data = resp.json()
        info = data.get("info", {})
        releases = data.get("releases", {})
        return {
            "name": info.get("name", name),
            "version": info.get("version"),
            "summary": info.get("summary"),
            "requires_python": info.get("requires_python"),
            "release_count": len(releases),
        }
