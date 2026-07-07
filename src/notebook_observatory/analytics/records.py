"""Assemble a flat per-notebook observation row.

A single observation joins three things:

1. **Provenance** from the collector (which repo, stratum, stars, run date).
2. **Features** from the parser.
3. **Detection + metrics** derived from those features.

The output is a plain ``dict`` (one row) ready to be concatenated into a pandas
DataFrame and written to the partitioned Parquet dataset.
"""

from __future__ import annotations

from typing import Any

from ..collectors.notebook_collector import CollectedNotebook
from ..detection.libraries import all_known_libraries, detect_libraries
from ..parsers.notebook_parser import parse_notebook
from .metrics import compute_metrics


def build_observation(
    collected: CollectedNotebook, run_date: str, collection_type: str = "daily"
) -> dict[str, Any]:
    """Parse one collected notebook and build its full observation row.

    Args:
        collected: The downloaded notebook + provenance.
        run_date: ISO date string for the collection run (for a creation-year
            cohort this is ``YEAR-01-01``).
        collection_type: ``"daily"`` for the live daily census or ``"cohort"``
            for a historical creation-year backfill. Recorded on every row so
            the two collection modes can be filtered apart downstream.

    Returns:
        A flat dict combining provenance, features, detection flags, and metrics.
    """
    features = parse_notebook(collected.raw)
    detection = detect_libraries(features.imports)
    metrics = compute_metrics(features, detection)

    row: dict[str, Any] = {"run_date": run_date, "collection_type": collection_type}
    # Provenance (excludes the raw payload).
    row.update(collected.provenance())
    # Features (lists flattened to ;-joined strings via to_row()).
    row.update(features.to_row())
    # Metrics.
    row.update(metrics.to_row())

    # Detection: a stable set of boolean columns, one per known library, plus a
    # joined string and category flags. Stable columns make longitudinal
    # library-adoption analysis a straight column mean.
    detected = set(detection.libraries)
    for lib in all_known_libraries():
        row[f"lib_{lib}"] = lib in detected
    row["detected_libraries"] = ";".join(detection.libraries)
    row["detected_categories"] = ";".join(detection.categories)
    row["detected_library_count"] = len(detection.libraries)

    return row
