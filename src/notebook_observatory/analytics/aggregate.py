"""Roll per-notebook observations into daily snapshots.

Two tidy tables are produced from a day's observation DataFrame:

* **daily_snapshot** — one row per run date: sample sizes, parse success, mean
  and median of every derived metric, structural aggregates (mean cells/lines,
  markdown ratio), Python-version distribution, and format-version mix.
* **library_adoption** — long-format ``(run_date, library, category,
  notebook_count, adoption_pct)`` — the fraction of the day's *parsed* notebooks
  importing each library. This is the table that answers "which technologies are
  growing".

Adoption is expressed as a percentage of successfully parsed notebooks so that
parse failures do not deflate adoption signals. All aggregates are computed with
pandas and returned as DataFrames; persistence is the storage layer's job.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..detection.libraries import all_known_libraries, get_registry
from ..logging_utils import get_logger

logger = get_logger(__name__)

# Metric columns produced by analytics.metrics.NotebookMetrics.
_METRIC_COLUMNS = [
    "notebook_size_index",
    "narrative_index",
    "reproducibility_score",
    "visualization_density",
    "interactive_widget_score",
    "scientific_computing_score",
    "ml_usage_score",
    "complexity_score",
    "educational_score",
    "documentation_density",
]


def build_daily_snapshot(observations: pd.DataFrame, run_date: str) -> pd.DataFrame:
    """Aggregate one day's observations into a single-row snapshot DataFrame.

    Args:
        observations: Per-notebook rows (output of ``build_observation``).
        run_date: ISO date string for the run.

    Returns:
        A one-row DataFrame keyed by ``run_date``.
    """
    total = len(observations)
    parsed = observations[observations["parse_ok"]] if total else observations
    n_parsed = len(parsed)

    # Collection mode ("daily" or "cohort"); default to daily for older rows.
    collection_type = "daily"
    if total and "collection_type" in observations.columns:
        modes = observations["collection_type"].dropna().unique()
        if len(modes):
            collection_type = str(modes[0])

    snap: dict[str, Any] = {
        "run_date": run_date,
        "collection_type": collection_type,
        "notebooks_collected": total,
        "notebooks_parsed": n_parsed,
        "parse_success_rate": round(n_parsed / total, 4) if total else 0.0,
        # Repositories that actually contributed at least one collected notebook
        # (distinct from the run report's `repos_sampled`, which counts the whole
        # sampled candidate pool including repos that yielded no notebooks).
        "repos_contributing": int(observations["repo_full_name"].nunique()) if total else 0,
        "strata_sampled": int(observations["stratum"].nunique()) if total else 0,
    }

    if n_parsed:
        # Metric means and medians.
        for col in _METRIC_COLUMNS:
            snap[f"{col}_mean"] = round(float(parsed[col].mean()), 4)
            snap[f"{col}_median"] = round(float(parsed[col].median()), 4)

        # Structural aggregates.
        snap["mean_total_cells"] = round(float(parsed["total_cells"].mean()), 3)
        snap["median_total_cells"] = float(parsed["total_cells"].median())
        snap["mean_code_cells"] = round(float(parsed["code_cells"].mean()), 3)
        snap["mean_markdown_cells"] = round(float(parsed["markdown_cells"].mean()), 3)
        snap["mean_total_lines"] = round(float(parsed["total_lines"].mean()), 3)
        snap["mean_imports"] = round(float(parsed["import_count"].mean()), 3)
        snap["mean_outputs"] = round(float(parsed["total_outputs"].mean()), 3)
        snap["pct_with_output"] = round(float((parsed["has_any_output"]).mean()), 4)
        snap["pct_executed_in_order"] = round(float((parsed["fully_executed_in_order"]).mean()), 4)
        snap["pct_with_widgets"] = round(float((parsed["has_widget_state"]).mean()), 4)

        # Python version distribution (top versions as pct).
        pyver = parsed["python_major_minor"].dropna()
        pyver = pyver[pyver != ""]
        if len(pyver):
            dist = (pyver.value_counts(normalize=True) * 100).round(2)
            for ver, pct in dist.items():
                safe = str(ver).replace(".", "_")
                snap[f"pyver_{safe}_pct"] = float(pct)
            snap["pct_with_python_version"] = round(len(pyver) / n_parsed, 4)
        else:
            snap["pct_with_python_version"] = 0.0

        # nbformat mix.
        nbf = parsed["nbformat"].dropna()
        if len(nbf):
            snap["mean_nbformat"] = round(float(nbf.mean()), 3)
            snap["pct_nbformat_4"] = round(float((nbf == 4).mean()), 4)

    return pd.DataFrame([snap])


def build_library_adoption(observations: pd.DataFrame, run_date: str) -> pd.DataFrame:
    """Compute per-library adoption for one day (long format).

    Returns a DataFrame with columns ``run_date, library, category,
    notebook_count, adoption_pct`` — one row per known library.
    """
    parsed = observations[observations["parse_ok"]] if len(observations) else observations
    n_parsed = len(parsed)
    registry = get_registry()

    collection_type = "daily"
    if len(observations) and "collection_type" in observations.columns:
        modes = observations["collection_type"].dropna().unique()
        if len(modes):
            collection_type = str(modes[0])

    rows: list[dict[str, Any]] = []
    for lib in all_known_libraries():
        col = f"lib_{lib}"
        count = int(parsed[col].sum()) if (n_parsed and col in parsed.columns) else 0
        rows.append(
            {
                "run_date": run_date,
                "collection_type": collection_type,
                "library": lib,
                "category": registry.libraries[lib].category,
                "notebook_count": count,
                "adoption_pct": round(100.0 * count / n_parsed, 3) if n_parsed else 0.0,
            }
        )
    return pd.DataFrame(rows)


def aggregate_day(observations: pd.DataFrame, run_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Convenience: return ``(daily_snapshot, library_adoption)`` for a run."""
    logger.info("Aggregating %d observations for %s.", len(observations), run_date)
    return (
        build_daily_snapshot(observations, run_date),
        build_library_adoption(observations, run_date),
    )
