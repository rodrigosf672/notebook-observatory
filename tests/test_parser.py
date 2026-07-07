"""Tests for the notebook parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from notebook_observatory.parsers.notebook_parser import parse_notebook

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_good_notebook_structure() -> None:
    f = parse_notebook(_read("good.ipynb"))
    assert f.parse_ok
    assert f.total_cells == 5
    assert f.code_cells == 3
    assert f.markdown_cells == 2
    assert f.nbformat == 4
    assert f.kernel_name == "python3"
    assert f.language == "python"
    assert f.python_major_minor == "3.11"


def test_good_notebook_execution_in_order() -> None:
    f = parse_notebook(_read("good.ipynb"))
    assert f.executed_code_cells == 3
    assert f.max_execution_count == 3
    assert f.execution_order_anomalies == 0
    assert f.fully_executed_in_order is True
    assert f.has_execution_gaps is False


def test_good_notebook_outputs_and_imports() -> None:
    f = parse_notebook(_read("good.ipynb"))
    assert f.image_outputs == 1
    assert f.has_any_output is True
    assert {"numpy", "pandas", "matplotlib"}.issubset(set(f.imports))
    assert "setup" in f.distinct_cell_tags


def test_messy_notebook_execution_anomalies() -> None:
    f = parse_notebook(_read("messy.ipynb"))
    assert f.parse_ok
    # Execution counts encountered in cell order: [5, 2] -> one inversion.
    assert f.execution_order_anomalies == 1
    assert f.has_execution_gaps is True
    assert f.fully_executed_in_order is False
    # torch import survives despite a shell-magic line in the same cell.
    assert "torch" in f.imports


def test_legacy_nbformat3_worksheets() -> None:
    f = parse_notebook(_read("legacy_nbformat3.ipynb"))
    assert f.parse_ok
    assert f.nbformat == 3
    assert f.code_cells == 1
    assert "scipy" in f.imports


def test_malformed_notebook_is_soft_failure() -> None:
    f = parse_notebook(_read("malformed.ipynb"))
    assert f.parse_ok is False
    assert f.parse_error is not None
    # Partial record is still typed and complete.
    assert f.total_cells == 0


def test_empty_and_nonobject_inputs() -> None:
    assert parse_notebook("{}").parse_ok is True
    assert parse_notebook("[]").parse_ok is False
    assert parse_notebook("null").parse_ok is False


def test_to_row_is_json_safe() -> None:
    import json

    f = parse_notebook(_read("good.ipynb"))
    row = f.to_row()
    # lists flattened to strings; whole row JSON-serializable.
    assert isinstance(row["imports"], str)
    json.dumps(row)


@pytest.mark.parametrize("name", ["good.ipynb", "messy.ipynb", "legacy_nbformat3.ipynb"])
def test_markdown_ratio_is_finite(name: str) -> None:
    import math

    f = parse_notebook(_read(name))
    assert math.isfinite(f.markdown_to_code_ratio)
