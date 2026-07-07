<h1 align="center">📓 Notebook Observatory</h1>

<p align="center">
  <em>A continuously updated public observatory of the computational notebook ecosystem.</em><br>
  <strong>Our World in Data, for Jupyter notebooks.</strong>
</p>

<p align="center">
  <a href="https://github.com/rodrigosf672/notebook-observatory/actions/workflows/daily.yml"><img alt="Daily collection" src="https://github.com/rodrigosf672/notebook-observatory/actions/workflows/daily.yml/badge.svg"></a>
  <a href="https://github.com/rodrigosf672/notebook-observatory/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/rodrigosf672/notebook-observatory/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://rodrigosf672.github.io/notebook-observatory/"><img alt="Dashboard" src="https://img.shields.io/badge/dashboard-live-brightgreen"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

---

## What is this?

**Notebook Observatory** performs an automated **daily census** of public
computational notebooks on GitHub. Every day it:

1. **collects** a diverse, statistically-stratified sample of public `.ipynb` files,
2. **parses** each notebook into a rich structured feature set,
3. **detects** the libraries it uses,
4. **computes** a suite of interpretable derived metrics,
5. **appends** the results to an ever-growing longitudinal dataset,
6. **rebuilds** an interactive dashboard, and
7. **publishes** everything to GitHub Pages —

with **no manual intervention**. The repository becomes more valuable every
day as observations accumulate.

> 🔗 **Live dashboard:** https://rodrigosf672.github.io/notebook-observatory/

### Questions it is built to answer

- How fast is the notebook ecosystem growing, and which technologies are growing with it?
- Which plotting libraries dominate notebooks? How is **marimo**, **anywidget**, or **ipywidgets** adoption evolving?
- How much Markdown do notebooks contain? How reproducible are they?
- Which Python versions are most common? How has notebook complexity changed over time?

---

## Snapshot from the seed run

The repository ships with a **real** first daily snapshot (not synthetic
placeholder data). From **390 successfully parsed** public notebooks sampled on
the seed date:

| Library | Category | Adoption |
|---|---|---:|
| numpy | array | 60.8% |
| matplotlib | plotting | 51.3% |
| pandas | array | 40.5% |
| scikit-learn | ml | 23.3% |
| pytorch | deep learning | 20.3% |
| seaborn | plotting | 15.4% |
| tensorflow | deep learning | 9.0% |

See [`datasets/`](datasets/) for the full machine-readable data and the
[live dashboard](https://rodrigosf672.github.io/notebook-observatory/) for the
interactive version.

---

## Architecture

```
             ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
 GitHub API  │  collectors  │────▶│   parsers    │────▶│  detection   │
────────────▶│  sample +    │     │  .ipynb →    │     │ imports →    │
             │  download    │     │  features    │     │  libraries   │
             └──────────────┘     └──────────────┘     └──────┬───────┘
                                                              │
        ┌──────────────┐     ┌──────────────┐     ┌──────────▼───────┐
        │  dashboard   │◀────│   storage    │◀────│    analytics     │
        │ static site  │     │ append-only  │     │ metrics +        │
        │ (Pages)      │     │ Parquet/DuckDB│    │ daily aggregation│
        └──────────────┘     └──────────────┘     └──────────────────┘
```

Every stage is an independently testable module under
[`src/notebook_observatory/`](src/notebook_observatory/):

| Module | Responsibility |
|---|---|
| `github_client.py` | Rate-limited, retrying, caching GitHub REST client |
| `pypi_client.py` | PyPI package-metadata enrichment |
| `collectors/` | Diverse daily sampling strategy + notebook retrieval |
| `parsers/` | Robust `.ipynb` → structured feature extraction |
| `detection/` | Extensible, YAML-driven library detection registry |
| `analytics/` | Per-notebook derived metrics + daily aggregation |
| `storage/` | Append-only Parquet / DuckDB / CSV longitudinal store |
| `dashboard/` | Static, dark-mode, mobile-friendly site generation |
| `pipeline.py` | End-to-end orchestration |
| `cli.py` | `nbobs` command-line entry point |

---

## Data pipeline

### 1. Sampling (`collectors/sampler.py`)

There is no API that enumerates *all* public notebooks, and GitHub search caps
any query at 1000 results. Each day is therefore treated as a **stratified
sample** drawn from complementary strata, chosen **deterministically from the
run date** so the schedule is reproducible yet rotates over time:

- **star buckets** (long-tail → head) — all six covered across any two days,
- **recent-push frontier** — the activity edge of the ecosystem,
- **creation-year windows** — age stratification,
- **rotating topics** — `data-science`, `machine-learning`, `bioinformatics`, …,
- **repository-size buckets**.

This maximizes diversity and avoids re-collecting the same notebooks daily. See
[`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) for the full design and its
statistical caveats.

### 2. Parsing (`parsers/notebook_parser.py`)

Notebooks are parsed as **plain JSON** (not via `nbformat`) for robustness
against the messy reality of public notebooks — version skew, missing fields,
outright corruption. A malformed notebook yields a partial, typed record with
`parse_ok=False`, never an exception. Extracted features include cell counts by
type, kernel/language/Python version, nbformat version, execution counts and
**order anomalies**, output presence/images/largest output, line counts,
markdown-to-code ratio, imports, widgets, tags, and extensions.

### 3. Detection (`detection/libraries.yaml`)

A **data-driven registry** maps import module names → canonical libraries and
categories. Extending coverage is a YAML edit, not a code change.

### 4. Metrics (`analytics/metrics.py`)

Ten interpretable, bounded `[0,1]` indices per notebook — Notebook Size,
Narrative, Reproducibility, Visualization Density, Interactive Widget,
Scientific Computing, ML Usage, Complexity, Educational, Documentation Density.
Full formulas and their **limitations** are in
[`docs/METRICS.md`](docs/METRICS.md).

### 5. Storage (`storage/datasets.py`)

**History is never overwritten.** Per-notebook observations are written to a
date-partitioned Parquet dataset; daily snapshots and library-adoption tables
are appended (idempotent per date — a re-run replaces only that date's rows). A
DuckDB database exposes SQL views for ad-hoc querying. Schema:
[`docs/SCHEMA.md`](docs/SCHEMA.md).

---

## Quick start

```bash
# Install (editable, with dev tools)
pip install -e ".[dev]"

# Set a GitHub token for a realistic rate limit (5000 req/hr vs 60 anonymous)
export GITHUB_TOKEN=ghp_...

# Run one collection for today (UTC)
nbobs collect

# Rebuild the dashboard from stored data
nbobs dashboard

# Do both
nbobs all

# Re-aggregate a stored date (e.g. after changing a metric) without re-collecting
nbobs aggregate --date 2026-07-07
```

The generated site lands in [`site/`](site/); open `site/index.html` locally.

### Configuration

All tunables are environment variables (see
[`config.py`](src/notebook_observatory/config.py)):

| Variable | Default | Meaning |
|---|---:|---|
| `GITHUB_TOKEN` | — | GitHub PAT (or `GH_TOKEN`) |
| `NBOBS_TARGET_NOTEBOOKS` | 400 | Notebooks to fully parse per run |
| `NBOBS_REPO_SAMPLE` | 120 | Repositories to sample per run |
| `NBOBS_MAX_NB_PER_REPO` | 5 | Diversity guard per repo |
| `NBOBS_MAX_CORE_REQUESTS` | 4000 | Safety cap on core API calls/run |
| `NBOBS_LOG_LEVEL` | INFO | Logging verbosity |

---

## Automation

[`.github/workflows/daily.yml`](.github/workflows/daily.yml) runs every day at
06:17 UTC (and on manual dispatch): install → collect → aggregate → rebuild
dashboard → commit datasets & site → deploy Pages. It uses the built-in
`GITHUB_TOKEN` (no secrets to configure) and handles failures gracefully.
[`ci.yml`](.github/workflows/ci.yml) runs lint + type-check + tests on every push.

---

## Datasets

| File | Format | Description |
|---|---|---|
| `datasets/observations/date=*/observations.parquet` | Parquet | Every parsed notebook, one row each (partitioned by date) |
| `datasets/daily_snapshots.parquet` / `.csv` | Parquet + CSV | One row per day: sample sizes, metric means/medians, distributions |
| `datasets/library_adoption.parquet` / `.csv` | Parquet + CSV | Long-format per-library adoption per day |
| `datasets/observatory.duckdb` | DuckDB | SQL views over all of the above |

```python
import duckdb
con = duckdb.connect("datasets/observatory.duckdb", read_only=True)
con.sql("SELECT run_date, library, adoption_pct FROM library_adoption ORDER BY adoption_pct DESC LIMIT 10")
```

---

## Engineering quality

Strong typing (`mypy --strict`), linting & formatting (`ruff`), a full `pytest`
suite (parser incl. malformed/legacy notebooks, detection, metrics, storage
idempotency, sampler determinism), structured logging, retry logic, HTTP
caching with conditional requests, rate limiting, and centralized configuration.

---

## Limitations

This is a **sample-based** observatory, and the metrics measure *observable
document properties*, not code quality or correctness. Key caveats — GitHub
search coverage limits, sampling bias, the documentary (not executable) nature
of the reproducibility proxy — are stated plainly in
[`docs/METHODOLOGY.md`](docs/METHODOLOGY.md) and
[`docs/METRICS.md`](docs/METRICS.md). Read them before drawing strong
conclusions.

---

## Roadmap

Notebook search & similarity · topic modeling · embedding explorer · trend
forecasting · change detection · a public API · RSS feed · monthly reports · an
annual *State of Computational Notebooks* report. See
[`docs/METHODOLOGY.md`](docs/METHODOLOGY.md#roadmap).

---

## License

MIT — see [LICENSE](LICENSE). Built to become a featured, community-maintained
observatory of the Jupyter ecosystem. Contributions welcome.
