"""Command-line interface for the Notebook Observatory.

Subcommands:

* ``collect``    — run one daily collection (sample, parse, aggregate, store).
* ``aggregate``  — recompute snapshots/adoption from stored observations
                   (without re-collecting), e.g. after a metric change.
* ``dashboard``  — rebuild the static site from stored datasets.
* ``all``        — collect, then rebuild the dashboard.

Usage examples::

    nbobs collect                 # collect for today (UTC)
    nbobs collect --date 2026-07-01
    nbobs dashboard
    nbobs all

The same entry point is used by the GitHub Actions workflow.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import __version__
from .analytics.aggregate import aggregate_day
from .collectors.sampler import EARLIEST_COHORT_YEAR
from .dashboard.build import build_site
from .logging_utils import get_logger
from .pipeline import run_backfill, run_collection
from .storage.datasets import DatasetStore

logger = get_logger(__name__)


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    return dt.date.fromisoformat(value)


def _cmd_collect(args: argparse.Namespace) -> int:
    report = run_collection(run_date=_parse_date(args.date))
    logger.info(
        "Collected %d notebooks (%d parsed) across %d repos.",
        report.notebooks_collected,
        report.notebooks_parsed,
        report.repos_sampled,
    )
    for lib in report.top_libraries[:5]:
        logger.info("  %-14s %5.1f%%", lib["library"], lib["adoption_pct"])
    return 0


def _cmd_aggregate(args: argparse.Namespace) -> int:
    """Recompute aggregates for a stored date without re-collecting."""
    store = DatasetStore()
    run_date = _parse_date(args.date) or dt.datetime.now(dt.UTC).date()
    rd = run_date.isoformat()
    obs = store.read_observations(rd)
    if obs.empty:
        logger.error("No stored observations for %s.", rd)
        return 1
    snapshot, adoption = aggregate_day(obs, rd)
    store.append_daily_snapshot(snapshot)
    store.append_library_adoption(adoption)
    store.rebuild_duckdb()
    logger.info("Re-aggregated %d observations for %s.", len(obs), rd)
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    """Backfill historical creation-year cohorts (newest year first)."""
    reports = run_backfill(start_year=args.start_year, end_year=args.end_year)
    total = sum(r.notebooks_collected for r in reports)
    logger.info("Backfill complete: %d cohorts, %d notebooks total.", len(reports), total)
    for r in reports:
        logger.info(
            "  %s: %d collected, %d parsed", r.run_date, r.notebooks_collected, r.notebooks_parsed
        )
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    out = build_site(repo_url=args.repo_url)
    logger.info("Dashboard built at %s", out)
    return 0


def _cmd_all(args: argparse.Namespace) -> int:
    rc = _cmd_collect(args)
    if rc != 0:
        return rc
    return _cmd_dashboard(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nbobs", description="Notebook Observatory — daily census of computational notebooks."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    date_help = "Run date (YYYY-MM-DD); defaults to today (UTC)."
    repo_url_default = "https://github.com/rodrigosf672/notebook-observatory"

    p_collect = sub.add_parser("collect", help="Run one daily collection.")
    p_collect.add_argument("--date", help=date_help)
    p_collect.set_defaults(func=_cmd_collect)

    p_agg = sub.add_parser("aggregate", help="Recompute aggregates from stored observations.")
    p_agg.add_argument("--date", help=date_help)
    p_agg.set_defaults(func=_cmd_aggregate)

    p_bf = sub.add_parser(
        "backfill", help="Collect historical creation-year cohorts (2013..present)."
    )
    p_bf.add_argument(
        "--start-year", type=int, default=EARLIEST_COHORT_YEAR, help="Earliest creation year."
    )
    p_bf.add_argument(
        "--end-year", type=int, default=None, help="Latest creation year (default: current)."
    )
    p_bf.set_defaults(func=_cmd_backfill)

    p_dash = sub.add_parser("dashboard", help="Rebuild the static dashboard.")
    p_dash.add_argument("--repo-url", default=repo_url_default)
    p_dash.set_defaults(func=_cmd_dashboard)

    p_all = sub.add_parser("all", help="Collect then rebuild the dashboard.")
    p_all.add_argument("--date", help=date_help)
    p_all.add_argument("--repo-url", default=repo_url_default)
    p_all.set_defaults(func=_cmd_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception:
        logger.exception("Command failed.")
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
