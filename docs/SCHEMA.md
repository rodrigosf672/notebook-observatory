# Dataset schema

All datasets live under [`datasets/`](../datasets/) and are written by
[`storage/datasets.py`](../src/notebook_observatory/storage/datasets.py).
Storage is **append-only**: history is never overwritten.

## `observations/date=YYYY-MM-DD/observations.parquet`

One row per collected notebook (110 columns), partitioned by run date. This is
the atomic dataset from which all aggregates are derived.

### Provenance
| Column | Type | Description |
|---|---|---|
| `run_date` | str | Collection date (ISO). For historical cohorts this is `YEAR-01-01`. |
| `collection_type` | str | `"daily"` (live census) or `"cohort"` (creation-year backfill). |
| `repo_full_name` | str | `owner/name`. |
| `path` | str | Notebook path within the repo. |
| `ref` | str | Git ref (default branch). |
| `size_bytes` | int | Notebook size from the git tree. |
| `repo_stars` | int | Repository stargazers at collection time. |
| `repo_size_kb` | int | Repository size (KB). |
| `repo_created_at`, `repo_pushed_at` | str | Repo timestamps. |
| `strategy`, `stratum` | str | Which sampling strategy/stratum produced this row. |

### Parse status & format
| Column | Type | Description |
|---|---|---|
| `parse_ok` | bool | Whether parsing succeeded. |
| `parse_error` | str? | Error message when `parse_ok` is false. |
| `nbformat`, `nbformat_minor` | int? | Notebook format version. |
| `kernel_name`, `kernel_display_name` | str? | Kernel spec. |
| `language`, `language_version` | str? | Language info. |
| `python_version`, `python_major_minor` | str? | Extracted Python version (e.g. `3.11.4`, `3.11`). |

### Structure & text
`total_cells`, `code_cells`, `markdown_cells`, `raw_cells`, `other_cells`,
`empty_code_cells`, `total_lines`, `code_lines`, `markdown_lines`,
`avg_cell_lines`, `avg_code_cell_lines`, `markdown_to_code_ratio`,
`total_source_chars`.

### Execution
`executed_code_cells`, `max_execution_count`, `execution_order_anomalies`,
`has_execution_gaps` (bool), `fully_executed_in_order` (bool).

### Outputs
`cells_with_output`, `total_outputs`, `image_outputs`, `html_outputs`,
`stream_outputs`, `error_outputs`, `largest_output_bytes`, `has_any_output` (bool).

### Widgets & metadata
`has_widget_state` (bool), `widget_output_count`, `cell_tag_count`,
`distinct_cell_tags` (`;`-joined), `has_custom_metadata` (bool),
`extension_hints` (`;`-joined).

### Imports & metrics
`imports` (`;`-joined), `import_count`, and the ten derived metrics:
`notebook_size_index`, `narrative_index`, `reproducibility_score`,
`visualization_density`, `interactive_widget_score`,
`scientific_computing_score`, `ml_usage_score`, `complexity_score`,
`educational_score`, `documentation_density` (see [METRICS.md](METRICS.md)).

### Detection flags
One boolean column per known library — `lib_numpy`, `lib_pandas`, `lib_pytorch`,
`lib_marimo`, … — plus `detected_libraries` (`;`-joined), `detected_categories`
(`;`-joined), and `detected_library_count`. These stable columns make
longitudinal adoption analysis a straight column mean.

---

## `daily_snapshots.parquet` / `.csv`

One row per run date (51 columns). Key columns:

| Column | Description |
|---|---|
| `run_date` | ISO date (or `YEAR-01-01` for a cohort). |
| `collection_type` | `"daily"` or `"cohort"`. |
| `notebooks_collected`, `notebooks_parsed` | Sample sizes. |
| `parse_success_rate` | Parsed / collected. |
| `repos_contributing` | Distinct repos that yielded ≥1 collected notebook (≤ the run report's `repos_sampled` candidate pool). |
| `strata_sampled` | Number of distinct sampling strata represented. |
| `<metric>_mean`, `<metric>_median` | Mean & median of each of the ten metrics. |
| `mean_total_cells`, `median_total_cells`, `mean_code_cells`, `mean_markdown_cells`, `mean_total_lines`, `mean_imports`, `mean_outputs` | Structural aggregates. |
| `pct_with_output`, `pct_executed_in_order`, `pct_with_widgets` | Behavioral shares. |
| `pyver_<major>_<minor>_pct` | Python-version distribution (dynamic columns, e.g. `pyver_3_11_pct`). |
| `pct_with_python_version` | Share declaring any Python version. |
| `mean_nbformat`, `pct_nbformat_4` | Format-version mix. |

---

## `library_adoption.parquet` / `.csv`

Long format — one row per `(run_date, library)`:

| Column | Type | Description |
|---|---|---|
| `run_date` | str | ISO date. |
| `library` | str | Canonical library name. |
| `category` | str | Library category. |
| `notebook_count` | int | Parsed notebooks importing it that day. |
| `adoption_pct` | float | `100 × notebook_count / notebooks_parsed`. |

---

## `observatory.duckdb`

A DuckDB database exposing **views** (not copies) over the Parquet files:
`observations`, `daily_snapshots`, `library_adoption`. Rebuilt every run; safe
to delete and regenerate with `nbobs aggregate` or `DatasetStore.rebuild_duckdb()`.

```python
import duckdb
con = duckdb.connect("datasets/observatory.duckdb", read_only=True)
con.sql("""
  SELECT run_date, AVG(reproducibility_score) AS mean_repro
  FROM observations WHERE parse_ok GROUP BY run_date ORDER BY run_date
""")
```

---

## `last_run_report.json`

A small machine-readable summary of the most recent run (counts, strata, top
libraries, client request stats) — consumed by release notes and monitoring.
