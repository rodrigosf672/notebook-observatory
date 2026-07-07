"""Centralized configuration.

All tunables live here and can be overridden by environment variables so the
same code runs identically in local development and in GitHub Actions. Nothing
in this module performs I/O at import time beyond reading environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Repository root (…/notebook-observatory). Resolved relative to this file so it
# works regardless of the current working directory.
PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[1]


@dataclass(frozen=True)
class Paths:
    """Filesystem layout for datasets and generated artifacts."""

    repo_root: Path = REPO_ROOT
    datasets: Path = field(default_factory=lambda: REPO_ROOT / "datasets")
    observations: Path = field(default_factory=lambda: REPO_ROOT / "datasets" / "observations")
    site: Path = field(default_factory=lambda: REPO_ROOT / "site")
    cache: Path = field(default_factory=lambda: REPO_ROOT / ".cache")

    @property
    def daily_snapshots_parquet(self) -> Path:
        return self.datasets / "daily_snapshots.parquet"

    @property
    def daily_snapshots_csv(self) -> Path:
        return self.datasets / "daily_snapshots.csv"

    @property
    def library_adoption_parquet(self) -> Path:
        return self.datasets / "library_adoption.parquet"

    @property
    def library_adoption_csv(self) -> Path:
        return self.datasets / "library_adoption.csv"

    @property
    def duckdb_file(self) -> Path:
        return self.datasets / "observatory.duckdb"

    def ensure(self) -> None:
        """Create all directories that must exist for a run."""
        for p in (self.datasets, self.observations, self.site, self.cache):
            p.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class CollectionConfig:
    """Budget and behavior for a single daily collection run.

    Defaults are tuned to stay comfortably within the GitHub REST rate limits
    for an authenticated token (5000 core requests/hour, 30 search
    requests/minute). Override via environment variables in CI to scale up or
    down without code changes.
    """

    # Target number of notebooks to fully parse in one run.
    target_notebooks: int = _env_int("NBOBS_TARGET_NOTEBOOKS", 400)
    # How many repositories to sample from before walking their trees.
    repo_sample_size: int = _env_int("NBOBS_REPO_SAMPLE", 120)
    # Max notebooks taken from any single repository (diversity guard).
    max_notebooks_per_repo: int = _env_int("NBOBS_MAX_NB_PER_REPO", 5)
    # Max raw notebook bytes to download (skip pathological giant notebooks).
    max_notebook_bytes: int = _env_int("NBOBS_MAX_NB_BYTES", 4_000_000)
    # Hard ceiling on core API requests spent per run (safety valve).
    max_core_requests: int = _env_int("NBOBS_MAX_CORE_REQUESTS", 4000)
    # Hard ceiling on search requests per run.
    max_search_requests: int = _env_int("NBOBS_MAX_SEARCH_REQUESTS", 60)
    # Deterministic seed offset; combined with the run date for reproducibility.
    seed: int = _env_int("NBOBS_SEED", 0)


@dataclass(frozen=True)
class ClientConfig:
    """HTTP client behavior."""

    user_agent: str = os.environ.get("NBOBS_USER_AGENT", "notebook-observatory/0.1")
    timeout_seconds: int = _env_int("NBOBS_HTTP_TIMEOUT", 30)
    max_retries: int = _env_int("NBOBS_MAX_RETRIES", 5)
    use_cache: bool = _env_bool("NBOBS_USE_CACHE", True)
    # When True (default in CI), sleep to respect rate limits. When False, raise
    # instead of sleeping — useful for tests.
    respect_rate_limit: bool = _env_bool("NBOBS_RESPECT_RATE_LIMIT", True)


def github_token() -> str | None:
    """Return the GitHub token from the environment, if any.

    Checks ``GITHUB_TOKEN`` then ``GH_TOKEN``. Returns ``None`` for anonymous
    access (subject to much lower rate limits).
    """
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


PATHS = Paths()
COLLECTION = CollectionConfig()
CLIENT = ClientConfig()
