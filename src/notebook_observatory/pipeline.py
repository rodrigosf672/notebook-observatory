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
