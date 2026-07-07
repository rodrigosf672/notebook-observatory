"""End-to-end daily pipeline orchestration.

Ties together the collector, parser, detection, metrics, aggregation, and
storage into the single ``run_collection`` entry point the CLI and the GitHub
Action call. Each stage is defensive: a failure to parse one notebook never
aborts the run, and the run always produces a (possibly partial) snapshot.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

from .analytics.aggregate import aggregate_day
from .analytics.records import build_observation
from .collectors.notebook_collector import NotebookCollector
from .collectors.sampler import EARLIEST_COHORT_YEAR, build_cohort_plan
from .config import PATHS
from .github_client import GitHubClient
from .logging_utils import get_logger
from .storage.datasets import DatasetStore

logger = get_logger(__name__)


@dataclass
class RunReport:
    """Summary of a completed collection run (also written to disk as JSON)."""

    run_date: str
    notebooks_collected: int
    notebooks_parsed: int
    repos_sampled: int
    strata: list[str]
    top_libraries: list[dict[str, Any]]
    client_stats: dict[str, int]
    errors: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_date": self.run_date,
            "notebooks_collected": self.notebooks_collected,
            "notebooks_parsed": self.notebooks_parsed,
            "repos_sampled": self.repos_sampled,
            "strata": self.strata,
            "top_libraries": self.top_libraries,
            "client_stats": self.client_stats,
            "errors": self.errors,
        }


def run_collection(run_date: dt.date | None = None) -> RunReport:
    """Run one full daily collection and persist all datasets.

    Steps: collect -> build per-notebook observations -> aggregate -> write
    partitioned observations, append snapshot + adoption, rebuild DuckDB.

    Args:
        run_date: Date to collect for; defaults to today (UTC).

    Returns:
        A :class:`RunReport`.
    """
    run_date = run_date or dt.datetime.now(dt.UTC).date()
    rd = run_date.isoformat()
    logger.info("=== Notebook Observatory collection run: %s ===", rd)

    client = GitHubClient()
    collector = NotebookCollector(client=client)
    result = collector.collect(run_date=run_date)

    observations = [build_observation(nb, rd) for nb in result.notebooks]
    obs_df = pd.DataFrame(observations)

    if obs_df.empty:
        logger.warning("No notebooks collected for %s; writing empty snapshot.", rd)

    snapshot, adoption = aggregate_day(obs_df, rd)

    store = DatasetStore()
    if not obs_df.empty:
        store.write_observations(obs_df, rd)
    store.append_daily_snapshot(snapshot)
    store.append_library_adoption(adoption)
    store.rebuild_duckdb()

    top_libs = (
        adoption.sort_values("adoption_pct", ascending=False)
        .head(10)[["library", "category", "adoption_pct", "notebook_count"]]
        .to_dict(orient="records")
    )

    report = RunReport(
        run_date=rd,
        notebooks_collected=len(result.notebooks),
        notebooks_parsed=int(obs_df["parse_ok"].sum()) if not obs_df.empty else 0,
        repos_sampled=result.repos_considered,
        strata=[s["stratum"] for s in result.plan],
        top_libraries=top_libs,
        client_stats=result.client_stats,
        errors=result.errors,
    )

    # Persist a machine-readable run report for the dashboard and release notes.
    report_path = PATHS.datasets / "last_run_report.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    logger.info("Run report written to %s", report_path)

    return report


def run_cohort(year: int, client: GitHubClient | None = None) -> RunReport:
    """Collect one **creation-year cohort** and persist it under ``YEAR-01-01``.

    Samples notebooks whose repository was created in ``year`` (stratified by
    popularity and topic), builds observations tagged ``collection_type="cohort"``,
    and appends a snapshot + adoption row keyed on ``year-01-01`` — so the
    longitudinal store gains one point per historical year.

    Args:
        year: Repository creation year to sample.
        client: Optional shared client (to pool the rate-limit budget across a
            multi-year backfill).

    Returns:
        A :class:`RunReport` for the cohort.
    """
    cohort_date = f"{year:04d}-01-01"
    logger.info("=== Notebook Observatory cohort run: %d ===", year)

    client = client or GitHubClient()
    collector = NotebookCollector(client=client)
    plan = build_cohort_plan(year)
    result = collector.collect(plan=plan)

    observations = [
        build_observation(nb, cohort_date, collection_type="cohort") for nb in result.notebooks
    ]
    obs_df = pd.DataFrame(observations)
    if obs_df.empty:
        logger.warning("No notebooks collected for cohort %d.", year)

    snapshot, adoption = aggregate_day(obs_df, cohort_date)

    store = DatasetStore()
    if not obs_df.empty:
        store.write_observations(obs_df, cohort_date)
    store.append_daily_snapshot(snapshot)
    store.append_library_adoption(adoption)
    store.rebuild_duckdb()

    top_libs = (
        adoption.sort_values("adoption_pct", ascending=False)
        .head(10)[["library", "category", "adoption_pct", "notebook_count"]]
        .to_dict(orient="records")
    )
    return RunReport(
        run_date=cohort_date,
        notebooks_collected=len(result.notebooks),
        notebooks_parsed=int(obs_df["parse_ok"].sum()) if not obs_df.empty else 0,
        repos_sampled=result.repos_considered,
        strata=[s["stratum"] for s in result.plan],
        top_libraries=top_libs,
        client_stats=result.client_stats,
        errors=result.errors,
    )


def run_backfill(
    start_year: int = EARLIEST_COHORT_YEAR,
    end_year: int | None = None,
    client: GitHubClient | None = None,
) -> list[RunReport]:
    """Backfill creation-year cohorts for ``start_year..end_year`` (inclusive).

    Runs newest-year first so that if the request budget is exhausted mid-run,
    the most data-rich recent cohorts are captured first. A shared client pools
    the rate-limit budget across years.

    Args:
        start_year: Earliest creation year (default: earliest with data).
        end_year: Latest creation year (default: current UTC year).
        client: Optional shared client.

    Returns:
        One :class:`RunReport` per collected year.
    """
    end_year = end_year or dt.datetime.now(dt.UTC).year
    client = client or GitHubClient()
    reports: list[RunReport] = []
    for year in range(end_year, start_year - 1, -1):
        reports.append(run_cohort(year, client=client))
    return reports
