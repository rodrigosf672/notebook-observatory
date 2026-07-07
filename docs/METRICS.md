# Derived metrics

Each notebook is reduced to **ten interpretable indices**, all in `[0, 1]`
(higher = more of the named property). They are defined in
[`analytics/metrics.py`](../src/notebook_observatory/analytics/metrics.py) and
are deliberately **transparent** — every score is a simple, inspectable
combination of observed features, with no opaque weighting.

Two helper shapes recur:

- **Saturating count → score:** `s(v; scale) = 1 − exp(−v / scale)`. Turns an
  unbounded count into a bounded score that grows fast then saturates at
  ~0.63 when `v = scale`. Prevents a single huge notebook from dominating a
  daily mean.
- **Clip:** all scores are clipped to `[0, 1]`.

| Metric | What it rewards | Formula (sketch) |
|---|---|---|
| **Notebook Size Index** | Substantial notebooks | `0.5·s(cells; 40) + 0.5·s(lines; 400)` |
| **Narrative Index** | Prose interleaved with code | `0.6·(md_cells / content_cells) + 0.4·s(md_lines; 60)` |
| **Reproducibility Score** | *Looks* reproducible | `0.20·has_kernel + 0.20·has_pyver + 0.45·exec_order + 0.15·has_imports` |
| **Visualization Density** | Plotting + rendered images | `0.5·uses_plotting + 0.5·s(images; 4)` |
| **Interactive Widget Score** | Widgets, widget state/outputs | `0.4·uses_widget_lib + 0.3·has_widget_state + 0.3·s(widget_outputs; 2)` |
| **Scientific Computing Score** | Array + scientific + data-IO stack | `0.5·array + 0.35·scientific + 0.15·data_io` |
| **ML Usage Score** | ML / deep-learning / LLM frameworks | `0.4·ml + 0.4·deep_learning + 0.2·llm` |
| **Complexity Score** | Size + import breadth + outputs + category breadth | `0.35·s(code_lines;300) + 0.25·s(imports;8) + 0.20·s(outputs;15) + 0.20·s(categories;3)` |
| **Educational Score** | Tutorial-like markdown/code balance | `0.6·balance + 0.4·s(md_lines; 50)`, `balance` peaks at 1:1 md:code |
| **Documentation Density** | Share of lines that are markdown | `markdown_lines / total_lines` |

### The reproducibility `exec_order` term

- `0` if no code cells were executed (no evidence it ran),
- `1` if execution counts are exactly `1..N` in cell order,
- otherwise `1 − (anomalies / executed_cells)`, further ×0.6 if there are gaps.

This captures whether the notebook was run cleanly top-to-bottom — a signal that
it *can* be — without ever claiming it actually re-executes.

---

## Daily aggregation

For each metric, the [daily snapshot](SCHEMA.md) stores the **mean** and
**median** across that day's successfully-parsed notebooks
(`<metric>_mean`, `<metric>_median`). Library adoption is the **percentage of
parsed notebooks** importing each library — computed over parsed notebooks so
parse failures do not deflate adoption.

---

## Limitations

- **Metrics measure the document, not the code.** They cannot assess
  correctness, efficiency, or scientific validity.
- **Reproducibility is documentary, not executable** (see above and
  [METHODOLOGY.md](METHODOLOGY.md)).
- **Weights are reasoned defaults, not calibrated constants.** They are chosen
  for interpretability. Because the formulas are transparent and the raw
  features are all stored in the observations dataset, anyone can recompute
  metrics under different weights — then run `nbobs aggregate` to rebuild
  snapshots without re-collecting.
- **Comparisons are most meaningful *within* the observatory over time**, not as
  absolute quality judgments of individual notebooks.
