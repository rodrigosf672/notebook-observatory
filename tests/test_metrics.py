"""Tests for derived per-notebook metrics."""

from __future__ import annotations

from pathlib import Path

from notebook_observatory.analytics.metrics import compute_metrics
from notebook_observatory.detection.libraries import detect_libraries
from notebook_observatory.parsers.notebook_parser import parse_notebook

FIXTURES = Path(__file__).parent / "fixtures"


def _metrics(name: str):
    f = parse_notebook((FIXTURES / name).read_text(encoding="utf-8"))
    return compute_metrics(f), f


def test_all_metrics_bounded_unit_interval() -> None:
    m, _ = _metrics("good.ipynb")
    for field, value in m.to_row().items():
        assert 0.0 <= value <= 1.0, f"{field}={value} out of [0,1]"


def test_reproducibility_rewards_clean_execution() -> None:
    good, _ = _metrics("good.ipynb")
    messy, _ = _metrics("messy.ipynb")
    # good.ipynb is fully executed in order with kernel + python version.
    assert good.reproducibility_score > messy.reproducibility_score


def test_narrative_and_documentation_reward_markdown() -> None:
    good, _ = _metrics("good.ipynb")  # has markdown
    messy, _ = _metrics("messy.ipynb")  # no markdown
    assert good.narrative_index > messy.narrative_index
    assert good.documentation_density > messy.documentation_density


def test_visualization_density_reflects_plotting_and_images() -> None:
    good, _ = _metrics("good.ipynb")  # matplotlib + 1 image
    assert good.visualization_density > 0.0


def test_ml_usage_detects_deep_learning() -> None:
    messy, _ = _metrics("messy.ipynb")  # imports torch
    assert messy.ml_usage_score > 0.0


def test_metrics_accept_explicit_detection() -> None:
    f = parse_notebook((FIXTURES / "good.ipynb").read_text(encoding="utf-8"))
    det = detect_libraries(f.imports)
    m1 = compute_metrics(f, det)
    m2 = compute_metrics(f)  # detection computed internally
    assert m1.to_row() == m2.to_row()


def test_empty_notebook_metrics_are_zero() -> None:
    f = parse_notebook("{}")
    m = compute_metrics(f)
    assert m.reproducibility_score == 0.0
    assert m.notebook_size_index == 0.0
