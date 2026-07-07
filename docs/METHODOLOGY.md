# Methodology

This document describes how Notebook Observatory samples, collects, and
processes public computational notebooks, and — importantly — the **limitations**
of that design. Read it before drawing strong conclusions from the data.

## 1. The sampling problem

There is no API that enumerates *all* public notebooks on GitHub. Two hard
constraints shape everything:

1. **The Search API caps any query at 1000 returnable results** (100 per page ×
   10 pages), regardless of `total_count`.
2. **Rate limits.** An authenticated token allows 5000 core requests/hour and
   only **30 search requests/minute**.

We therefore cannot census the population directly. Instead, each day's
collection is treated as a **stratified sample** of the ecosystem, and we record
exactly which stratum each observation came from so downstream analysis can
segment or weight accordingly.

## 2. Stratified, rotating sampling design

`collectors/sampler.py` builds a **deterministic daily plan** from the run date.
Determinism means the schedule is reproducible and auditable; rotation means we
do not observe the same slice every day. Each stratum is one GitHub search query
scoped to `language:"Jupyter Notebook"`:

| Stratum | Purpose |
|---|---|
| **Star buckets** (`1..5`, `6..25`, `26..100`, `101..500`, `501..2000`, `>2000`) | Popularity strata. All six are covered across **any two consecutive days** (three disjoint buckets per day), countering the head-heavy bias of a naive "most starred" query. |
| **Recent-push frontier** (`pushed:>date`) | The active edge of the ecosystem. |
| **Creation-year windows** (`created:YEAR`) | Age stratification; the year rotates across the notebook era (2015→present). |
| **Rotating topics** (`topic:...`) | 14 ecosystem topics (data-science, ML, bioinformatics, finance, NLP, …) rotate daily. |
| **Repository-size buckets** (`size:...`) | Project-scale stratification. |

A per-day random seed (derived from the date) shuffles page offsets so repeated
runs of the same stratum reach different result windows within the 1000-result
cap, and shuffles stratum order so no stratum is systematically served first
when the request budget runs low.

## 3. Collection

`collectors/notebook_collector.py`:

1. Pages through each stratum's search results into a **de-duplicated repository
   pool** (by `full_name`).
2. For each sampled repo, fetches the **git tree** and locates `.ipynb` blobs
   (excluding `.ipynb_checkpoints/` and files over `NBOBS_MAX_NB_BYTES`).
3. Samples up to `NBOBS_MAX_NB_PER_REPO` notebooks per repo — a **diversity
   guard** so a single notebook-heavy repo cannot dominate a day.
4. Downloads raw content, recording full provenance (repo, stratum, stars, size,
   timestamps).

The whole run respects hard budget caps (`NBOBS_MAX_CORE_REQUESTS`,
`NBOBS_MAX_SEARCH_REQUESTS`) so it always stays within the hourly quota. The
seed run consumed only 538 core + 7 search requests to collect 391 notebooks.

## 4. Robust parsing

Notebooks are parsed as **plain JSON**, not via `nbformat`, because public
notebooks are frequently malformed, truncated, or use legacy schemas. Every
accessor is defensive; a bad notebook yields a partial typed record with
`parse_ok=False` rather than aborting the run. Both the modern (`source`) and
legacy nbformat-3 (`input`, `worksheets`) layouts are handled.

## 5. Library detection

A data-driven registry (`detection/libraries.yaml`) maps top-level import module
names to canonical libraries and categories. Imports are extracted per code cell
via `ast` with a regex fallback for cells containing shell magics — so an
`import` inside a cell that also runs `!pip install` is still recovered.

## 6. Aggregation and storage

Per-notebook rows roll up into a **daily snapshot** (metric means/medians,
structural aggregates, Python-version and nbformat distributions) and a
**long-format library-adoption** table (adoption = share of *parsed* notebooks
importing each library). Storage is **append-only and idempotent per date**:
re-running a date replaces only that date's rows, so history for other dates is
immutable by construction.

---

## Limitations and biases

**These matter — please read.**

- **Not a census.** Results describe a *sample*, not the full population of
  public notebooks. Absolute counts reflect the daily budget, not ecosystem
  size. Trends and *relative* comparisons are the intended use.
- **Search-coverage bias.** GitHub's search index favors repositories with some
  signal (activity, stars, topics). Truly obscure or zero-star notebooks are
  under-represented. The `long_tail` and `recent_push` strata mitigate but do
  not eliminate this.
- **Sampling variance.** With a few hundred notebooks per day, day-to-day
  metric means carry sampling noise. Interpret short-run wiggles cautiously;
  the value compounds as the longitudinal record lengthens.
- **`.ipynb`-only.** marimo notebooks (`.py`), Quarto (`.qmd`), R Markdown, and
  Colab-only notebooks not committed as `.ipynb` are under-counted. marimo/Quarto
  usage is detected only when *imported* inside an `.ipynb`.
- **Reproducibility is a documentary proxy.** We never execute notebooks. The
  reproducibility score measures whether a notebook *looks* reproducible
  (declared kernel/version, clean in-order execution counts), not whether it
  actually re-runs. See [METRICS.md](METRICS.md).
- **Imports ≠ usage.** A detected import means the library is imported, not that
  it is central to the notebook.
- **Default-branch snapshot.** We read the default branch at collection time;
  notebooks on other branches or historical states are not captured.

## Roadmap

- **Enrichment:** optional PyPI/BigQuery download-volume context for detected libraries.
- **Analysis:** notebook search & similarity, topic modeling, embedding explorer.
- **Forecasting & change detection** over the longitudinal series.
- **Distribution:** a public read API, an RSS feed of daily deltas, monthly
  reports, and an annual *State of Computational Notebooks* report.
- **Coverage:** first-class marimo/Quarto file support beyond import detection.
