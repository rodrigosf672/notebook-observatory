"""Per-notebook derived metrics.

Each metric is a documented function of a parsed :class:`NotebookFeatures`
record and its detected libraries. Every score is normalized to ``[0, 1]``
(unless noted as a raw count) so metrics are comparable, averageable across a
daily sample, and interpretable on a dashboard.

Design principles
-----------------
* **Transparent.** Every formula is a simple, inspectable combination of
  observed features — no opaque weighting.
* **Bounded.** Scores saturate rather than diverge, so a single pathological
  notebook cannot dominate a daily mean.
* **Documented.** Each metric's docstring states exactly what it rewards, which
  is mirrored in ``docs/METRICS.md``.

The metrics deliberately measure *observable properties of the notebook
document*, not code quality or correctness (which cannot be assessed from
static content). ``docs/METRICS.md`` states these limitations explicitly.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from ..detection.libraries import DetectionResult, detect_libraries
from ..parsers.notebook_parser import NotebookFeatures


def _saturating(value: float, scale: float) -> float:
    """Map a non-negative ``value`` into ``[0, 1)`` via ``1 - exp(-value/scale)``.

    ``scale`` is the value at which the score reaches ~0.63. Useful for turning
    unbounded counts (lines, cells) into bounded scores that grow quickly at
    first and saturate.
    """
    if value <= 0:
        return 0.0
    return round(1.0 - math.exp(-value / scale), 4)


def _clip01(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


@dataclass
class NotebookMetrics:
    """The derived-metric vector for one notebook. All scores are in [0, 1]."""

    notebook_size_index: float = 0.0
    narrative_index: float = 0.0
    reproducibility_score: float = 0.0
    visualization_density: float = 0.0
    interactive_widget_score: float = 0.0
    scientific_computing_score: float = 0.0
    ml_usage_score: float = 0.0
    complexity_score: float = 0.0
    educational_score: float = 0.0
    documentation_density: float = 0.0

    def to_row(self) -> dict[str, float]:
        return asdict(self)


def notebook_size_index(f: NotebookFeatures) -> float:
    """How large the notebook is, by total cells and lines (saturating).

    Rewards substantial notebooks; saturates so a 5000-line outlier does not
    swamp the daily mean. Reference scale: ~40 cells / ~400 lines.
    """
    by_cells = _saturating(f.total_cells, scale=40)
    by_lines = _saturating(f.total_lines, scale=400)
    return _clip01(0.5 * by_cells + 0.5 * by_lines)


def narrative_index(f: NotebookFeatures) -> float:
    """How much prose (markdown) the notebook carries relative to its cells.

    Rewards notebooks that interleave explanation with code. Computed as the
    markdown share of all content cells, blended with a saturating markdown-line
    volume so a notebook with many short markdown cells and one with rich prose
    both score well.
    """
    content_cells = f.markdown_cells + f.code_cells
    md_share = (f.markdown_cells / content_cells) if content_cells else 0.0
    md_volume = _saturating(f.markdown_lines, scale=60)
    return _clip01(0.6 * md_share + 0.4 * md_volume)


def reproducibility_score(f: NotebookFeatures) -> float:
    """Proxy for how reproducible the notebook's execution appears.

    This is a *documentary* proxy, not a guarantee of re-execution. It rewards:

    * declared kernel / language metadata (someone can know how to run it),
    * a pinned-ish Python version present,
    * clean, in-order execution counts (no gaps, no out-of-order re-runs),
    * presence of imports (the environment is at least declared in-notebook).

    Notebooks with no executed cells score low on the execution component
    because there is no evidence the notebook ran.
    """
    has_kernel = 1.0 if f.kernel_name else 0.0
    has_pyver = 1.0 if f.python_major_minor else 0.0
    if f.executed_code_cells == 0:
        exec_order = 0.0
    elif f.fully_executed_in_order:
        exec_order = 1.0
    else:
        # Penalize by the fraction of executed cells that are anomalous.
        anomaly_frac = f.execution_order_anomalies / max(1, f.executed_code_cells)
        exec_order = max(0.0, 1.0 - anomaly_frac) * (0.6 if f.has_execution_gaps else 1.0)
    has_imports = 1.0 if f.import_count > 0 else 0.0
    return _clip01(0.20 * has_kernel + 0.20 * has_pyver + 0.45 * exec_order + 0.15 * has_imports)


def visualization_density(f: NotebookFeatures, det: DetectionResult) -> float:
    """How visual the notebook is: plotting-library use + rendered images.

    Blends whether a plotting library is imported with the saturating count of
    image outputs actually rendered in the document.
    """
    uses_plotting = 1.0 if det.has_category("plotting") else 0.0
    image_volume = _saturating(f.image_outputs, scale=4)
    return _clip01(0.5 * uses_plotting + 0.5 * image_volume)


def interactive_widget_score(f: NotebookFeatures, det: DetectionResult) -> float:
    """Degree of interactivity: widget libraries, widget state, widget outputs."""
    uses_widgets = 1.0 if det.has_category("interactive") else 0.0
    has_state = 1.0 if f.has_widget_state else 0.0
    widget_outputs = _saturating(f.widget_output_count, scale=2)
    return _clip01(0.4 * uses_widgets + 0.3 * has_state + 0.3 * widget_outputs)


def scientific_computing_score(f: NotebookFeatures, det: DetectionResult) -> float:
    """Presence of the scientific-computing stack (array + scientific families)."""
    array = 1.0 if det.has_category("array") else 0.0
    scientific = 1.0 if det.has_category("scientific") else 0.0
    data_io = 1.0 if det.has_category("data_io") else 0.0
    return _clip01(0.5 * array + 0.35 * scientific + 0.15 * data_io)


def ml_usage_score(f: NotebookFeatures, det: DetectionResult) -> float:
    """Presence of ML / deep-learning frameworks."""
    ml = 1.0 if det.has_category("ml") else 0.0
    dl = 1.0 if det.has_category("deep_learning") else 0.0
    llm = 1.0 if det.has_category("llm") else 0.0
    return _clip01(0.4 * ml + 0.4 * dl + 0.2 * llm)


def complexity_score(f: NotebookFeatures, det: DetectionResult) -> float:
    """Overall notebook complexity from size, import breadth, and outputs.

    Combines saturating code volume, distinct-import breadth, and output
    richness. A short notebook importing one library scores low; a large one
    pulling many libraries with many outputs scores high.
    """
    code_volume = _saturating(f.code_lines, scale=300)
    import_breadth = _saturating(f.import_count, scale=8)
    output_richness = _saturating(f.total_outputs, scale=15)
    category_breadth = _saturating(len(det.categories), scale=3)
    return _clip01(
        0.35 * code_volume
        + 0.25 * import_breadth
        + 0.20 * output_richness
        + 0.20 * category_breadth
    )


def educational_score(f: NotebookFeatures) -> float:
    """How tutorial-like the notebook is: prose-rich and moderately sized.

    Rewards a healthy markdown-to-code balance (explanation alongside code) and
    penalizes both no-prose notebooks and notebooks that are pure prose with no
    runnable code.
    """
    if f.code_cells == 0 or f.markdown_cells == 0:
        balance = 0.0
    else:
        ratio = f.markdown_cells / f.code_cells
        # Peak reward near a 1:1 markdown:code balance, decaying away from it.
        balance = math.exp(-abs(math.log(ratio + 1e-9)))
    md_volume = _saturating(f.markdown_lines, scale=50)
    return _clip01(0.6 * balance + 0.4 * md_volume)


def documentation_density(f: NotebookFeatures) -> float:
    """Fraction of content that is documentation (markdown lines share).

    Simple, direct: markdown lines divided by total lines. Distinct from the
    narrative index (which is cell-based and volume-blended).
    """
    if f.total_lines == 0:
        return 0.0
    return _clip01(f.markdown_lines / f.total_lines)


def compute_metrics(f: NotebookFeatures, det: DetectionResult | None = None) -> NotebookMetrics:
    """Compute the full metric vector for one parsed notebook.

    Args:
        f: Parsed notebook features.
        det: Detected libraries; computed from ``f.imports`` if not provided.

    Returns:
        A :class:`NotebookMetrics`.
    """
    det = det if det is not None else detect_libraries(f.imports)
    return NotebookMetrics(
        notebook_size_index=notebook_size_index(f),
        narrative_index=narrative_index(f),
        reproducibility_score=reproducibility_score(f),
        visualization_density=visualization_density(f, det),
        interactive_widget_score=interactive_widget_score(f, det),
        scientific_computing_score=scientific_computing_score(f, det),
        ml_usage_score=ml_usage_score(f, det),
        complexity_score=complexity_score(f, det),
        educational_score=educational_score(f),
        documentation_density=documentation_density(f),
    )
