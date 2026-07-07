"""Tests for import extraction and library detection."""

from __future__ import annotations

from notebook_observatory.detection.imports import extract_imports
from notebook_observatory.detection.libraries import (
    all_known_libraries,
    detect_libraries,
    get_registry,
)


def test_extract_imports_ast_and_aliases() -> None:
    cells = [
        "import numpy as np\nimport pandas as pd",
        "from sklearn.linear_model import LinearRegression",
        "import matplotlib.pyplot as plt",
    ]
    found = extract_imports(cells)
    assert {"numpy", "pandas", "sklearn", "matplotlib"}.issubset(found)


def test_extract_imports_regex_fallback_on_magics() -> None:
    # A cell that is not valid Python on its own (shell magic) still yields imports.
    cells = ["!pip install torch\nimport torch\n%matplotlib inline"]
    found = extract_imports(cells)
    assert "torch" in found


def test_extract_imports_ignores_relative_imports() -> None:
    found = extract_imports(["from . import utils\nfrom ..pkg import thing"])
    assert "utils" not in found
    assert "pkg" not in found


def test_detect_libraries_maps_modules_to_canonical() -> None:
    res = detect_libraries(["torch", "sklearn", "numpy", "os"])
    assert res.has("pytorch")
    assert res.has("scikit_learn")
    assert res.has("numpy")
    assert res.has_category("deep_learning")
    assert res.has_category("ml")
    assert res.has_category("array")


def test_langchain_multiple_module_names() -> None:
    for mod in ["langchain", "langchain_core", "langchain_community"]:
        assert detect_libraries([mod]).has("langchain")


def test_registry_is_consistent() -> None:
    reg = get_registry()
    libs = all_known_libraries()
    assert len(libs) == len(set(libs))  # no duplicates
    # every library maps at least one module and has a category.
    for name in libs:
        spec = reg.libraries[name]
        assert spec.modules
        assert spec.category in reg.categories


def test_unknown_modules_detected_as_nothing() -> None:
    res = detect_libraries(["totally_made_up_module", "os", "sys"])
    assert res.libraries == []
    assert res.categories == []
