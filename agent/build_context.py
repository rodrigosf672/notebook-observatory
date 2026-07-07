"""Regenerate ``observatory_context.json`` from the live datasets.

The agent Space is grounded in a compact JSON snapshot of the observatory's
datasets rather than the full Parquet (so it stays small and CPU-cheap). This
script rebuilds that snapshot. Run it after a collection/backfill, then push the
refreshed ``observatory_context.json`` to the Space.

Usage:
    python agent/build_context.py            # writes agent/observatory_context.json
    python agent/build_context.py --out X     # custom output path
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

_KEY_LIBS = [
    "numpy",
    "pandas",
    "matplotlib",
    "scikit_learn",
    "pytorch",
    "tensorflow",
    "keras",
    "transformers",
    "seaborn",
    "plotly",
    "scipy",
    "ipywidgets",
]


def build_context(datasets_dir: Path) -> dict:
    """Build the grounding-context dict from the stored datasets."""
    snap = pd.read_parquet(datasets_dir / "daily_snapshots.parquet")
    adopt = pd.read_parquet(datasets_dir / "library_adoption.parquet")
    if "collection_type" not in snap.columns:
        snap["collection_type"] = "daily"
    if "collection_type" not in adopt.columns:
        adopt["collection_type"] = "daily"

    cohort = snap[snap["collection_type"] == "cohort"].sort_values("run_date")
    daily = snap[snap["collection_type"] == "daily"].sort_values("run_date")
    co_ad = adopt[adopt["collection_type"] == "cohort"].copy()
    da_ad = adopt[adopt["collection_type"] == "daily"]

    ctx: dict = {"generated_from": "Notebook Observatory datasets"}

    def _num(row: pd.Series, col: str) -> float | None:
        v = row.get(col)
        return None if v is None or pd.isna(v) else round(float(v), 3)

    def _structure_and_quality(row: pd.Series) -> dict:
        """Extract structural stats, quality indices, and Python-version share."""
        metric_cols = [c for c in row.index if c.endswith("_mean")]
        struct_cols = [
            "mean_total_cells",
            "mean_code_cells",
            "mean_markdown_cells",
            "mean_total_lines",
            "mean_outputs",
            "pct_with_output",
            "pct_executed_in_order",
            "pct_with_widgets",
            "pct_with_python_version",
        ]
        pyver = {
            c.replace("pyver_", "").replace("_pct", "").replace("_", "."): _num(row, c)
            for c in row.index
            if c.startswith("pyver_") and c.endswith("_pct") and _num(row, c)
        }
        return {
            "quality_indices_mean": {c[:-5]: _num(row, c) for c in metric_cols},
            "structure": {c: _num(row, c) for c in struct_cols if c in row.index},
            "python_version_share_pct": pyver,
        }

    if not daily.empty:
        latest_date = daily["run_date"].iloc[-1]
        latest_row = daily.sort_values("run_date").iloc[-1]
        top = (
            da_ad[da_ad["run_date"] == latest_date]
            .sort_values("adoption_pct", ascending=False)
            .head(20)[["library", "category", "adoption_pct", "notebook_count"]]
        )
        ctx["daily_census"] = {
            "latest_date": latest_date,
            "notebooks_collected": int(daily["notebooks_collected"].iloc[-1]),
            "notebooks_parsed": int(daily["notebooks_parsed"].iloc[-1]),
            "n_daily_snapshots": int(daily["run_date"].nunique()),
            "top_libraries": top.to_dict(orient="records"),
            **_structure_and_quality(latest_row),
        }

    if not cohort.empty:
        co_ad = co_ad.assign(year=co_ad["run_date"].str[:4].astype(int))
        piv = (
            co_ad[co_ad["library"].isin(_KEY_LIBS)]
            .pivot(index="year", columns="library", values="adoption_pct")
            .round(1)
        )
        # Per-creation-year trends for a few structural / quality indicators, so
        # the agent can answer "how did notebook structure/quality change by
        # vintage" — not just library adoption.
        cohort_y = cohort.assign(year=cohort["run_date"].str[:4].astype(int)).sort_values("year")
        trend_cols = [
            "mean_total_cells",
            "mean_code_cells",
            "mean_markdown_cells",
            "pct_executed_in_order",
            "pct_with_widgets",
            "reproducibility_score_mean",
            "narrative_index_mean",
            "complexity_score_mean",
        ]
        structure_by_year = {
            col: {
                int(r["year"]): _num(r, col)
                for _, r in cohort_y.iterrows()
                if _num(r, col) is not None
            }
            for col in trend_cols
            if col in cohort_y.columns
        }
        ctx["cohorts"] = {
            "span": f"{cohort['run_date'].min()[:4]}-{cohort['run_date'].max()[:4]}",
            "n_cohorts": int(cohort["run_date"].nunique()),
            "notebooks_per_cohort": int(cohort["notebooks_collected"].iloc[0]),
            "library_adoption_pct_by_creation_year": {
                lib: {
                    int(y): (None if pd.isna(v) else float(v)) for y, v in piv[lib].items()
                }
                for lib in piv.columns
            },
            "structure_and_quality_by_creation_year": structure_by_year,
        }
    return ctx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    parser.add_argument("--datasets", type=Path, default=here.parent / "datasets")
    parser.add_argument("--out", type=Path, default=here / "observatory_context.json")
    args = parser.parse_args()
    ctx = build_context(args.datasets)
    args.out.write_text(json.dumps(ctx, indent=2), encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
