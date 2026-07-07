"""Extract a rich, typed feature set from a single ``.ipynb`` document.

The parser reads notebooks as plain JSON rather than via ``nbformat`` so that it
is robust to the messy reality of public notebooks: version skew, missing
fields, wrong types, and outright corruption. Every accessor is defensive; a
malformed notebook yields a :class:`NotebookFeatures` with ``parse_ok=False``
and whatever partial features could be recovered, never an exception that would
abort a collection run.

The extracted feature set (see :class:`NotebookFeatures`) covers structure
(cell counts by type), provenance (kernel, language, notebook-format version,
Python version), execution state (execution counts, order anomalies),
outputs (presence, images, largest output), text volume (lines, average cell
size, markdown-to-code ratio), imports, widgets, tags, extensions, and custom
metadata — the inputs required by every derived metric.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from typing import Any

from ..detection.imports import extract_imports
from ..logging_utils import get_logger

logger = get_logger(__name__)

_PYVER_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


@dataclass
class NotebookFeatures:
    """The structured feature record for one notebook.

    All numeric fields default to 0 and all optional strings to ``None`` so a
    partially parsed notebook still produces a complete, typed row.
    """

    # --- Parse status ---
    parse_ok: bool = True
    parse_error: str | None = None

    # --- Format / provenance ---
    nbformat: int | None = None
    nbformat_minor: int | None = None
    kernel_name: str | None = None
    kernel_display_name: str | None = None
    language: str | None = None
    language_version: str | None = None
    python_version: str | None = None
    python_major_minor: str | None = None

    # --- Cell structure ---
    total_cells: int = 0
    code_cells: int = 0
    markdown_cells: int = 0
    raw_cells: int = 0
    other_cells: int = 0
    empty_code_cells: int = 0

    # --- Text volume ---
    total_lines: int = 0
    code_lines: int = 0
    markdown_lines: int = 0
    avg_cell_lines: float = 0.0
    avg_code_cell_lines: float = 0.0
    markdown_to_code_ratio: float = 0.0
    total_source_chars: int = 0

    # --- Execution ---
    executed_code_cells: int = 0
    max_execution_count: int = 0
    execution_order_anomalies: int = 0
    has_execution_gaps: bool = False
    fully_executed_in_order: bool = False

    # --- Outputs ---
    cells_with_output: int = 0
    total_outputs: int = 0
    image_outputs: int = 0
    html_outputs: int = 0
    stream_outputs: int = 0
    error_outputs: int = 0
    largest_output_bytes: int = 0
    has_any_output: bool = False

    # --- Widgets / interactivity ---
    has_widget_state: bool = False
    widget_output_count: int = 0

    # --- Metadata richness ---
    cell_tag_count: int = 0
    distinct_cell_tags: list[str] = field(default_factory=list)
    has_custom_metadata: bool = False
    extension_hints: list[str] = field(default_factory=list)

    # --- Imports (top-level module names) ---
    imports: list[str] = field(default_factory=list)
    import_count: int = 0

    def to_row(self) -> dict[str, Any]:
        """Flatten to a dict suitable for a DataFrame row (lists -> csv strings)."""
        d = asdict(self)
        d["distinct_cell_tags"] = ";".join(sorted(self.distinct_cell_tags))
        d["extension_hints"] = ";".join(sorted(self.extension_hints))
        d["imports"] = ";".join(sorted(self.imports))
        return d


def _source_to_text(source: Any) -> str:
    """Notebook ``source`` may be a list of strings or a single string."""
    if isinstance(source, list):
        return "".join(str(s) for s in source)
    if isinstance(source, str):
        return source
    return ""


def _cell_source(cell: dict[str, Any]) -> str:
    """Extract a cell's source text.

    nbformat >=4 uses ``source``; the legacy nbformat 3 schema stored code-cell
    text under ``input`` instead. Fall back to ``input`` so legacy notebooks are
    parsed correctly.
    """
    if "source" in cell:
        return _source_to_text(cell.get("source", ""))
    return _source_to_text(cell.get("input", ""))


def _count_lines(text: str) -> int:
    if not text:
        return 0
    # Count non-trailing-empty lines; a cell "a\nb\n" is 2 lines.
    stripped = text.rstrip("\n")
    if stripped == "":
        return 0
    return stripped.count("\n") + 1


def _extract_python_version(metadata: dict[str, Any]) -> tuple[str | None, str | None]:
    """Best-effort Python version from language_info / kernelspec metadata."""
    li = metadata.get("language_info", {})
    if isinstance(li, dict):
        ver = li.get("version")
        if isinstance(ver, str):
            m = _PYVER_RE.search(ver)
            if m:
                full = m.group(1)
                mm = ".".join(full.split(".")[:2])
                return full, mm
    return None, None


def _detect_extensions(notebook: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    """Detect notebook-extension / tooling hints from metadata keys."""
    hints: set[str] = set()
    known = {
        "celltoolbar": "celltoolbar",
        "toc": "toc",
        "varInspector": "varInspector",
        "hide_input": "hide_input",
        "nbextensions": "nbextensions",
        "rise": "rise",
        "widgets": "jupyter-widgets",
        "papermill": "papermill",
        "colab": "google-colab",
        "kernelspec": None,  # not an extension
    }
    for key, label in known.items():
        if label and key in metadata:
            hints.add(label)
    # Colab notebooks carry a distinctive top-level metadata block.
    if "colab" in metadata:
        hints.add("google-colab")
    return sorted(hints)


def parse_notebook(raw: str) -> NotebookFeatures:
    """Parse a raw ``.ipynb`` JSON string into :class:`NotebookFeatures`.

    Never raises for content problems: malformed notebooks return a record with
    ``parse_ok=False`` and ``parse_error`` set.
    """
    feats = NotebookFeatures()
    try:
        nb = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        feats.parse_ok = False
        feats.parse_error = f"json: {exc}"
        return feats
    if not isinstance(nb, dict):
        feats.parse_ok = False
        feats.parse_error = "top-level notebook is not an object"
        return feats

    try:
        _parse_into(nb, feats)
    except Exception as exc:
        feats.parse_ok = False
        feats.parse_error = f"{type(exc).__name__}: {exc}"
        logger.debug("Partial parse: %s", exc)
    return feats


def _parse_into(nb: dict[str, Any], feats: NotebookFeatures) -> None:
    metadata = nb.get("metadata", {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}

    # --- Format version ---
    feats.nbformat = _as_int(nb.get("nbformat"))
    feats.nbformat_minor = _as_int(nb.get("nbformat_minor"))

    # --- Kernel / language ---
    kernelspec = metadata.get("kernelspec", {}) or {}
    if isinstance(kernelspec, dict):
        feats.kernel_name = _as_str(kernelspec.get("name"))
        feats.kernel_display_name = _as_str(kernelspec.get("display_name"))
        feats.language = _as_str(kernelspec.get("language"))
    li = metadata.get("language_info", {}) or {}
    if isinstance(li, dict):
        feats.language = feats.language or _as_str(li.get("name"))
        feats.language_version = _as_str(li.get("version"))
    feats.python_version, feats.python_major_minor = _extract_python_version(metadata)

    # --- Widgets state at notebook level ---
    widgets_meta = metadata.get("widgets")
    feats.has_widget_state = bool(widgets_meta)

    # --- Custom metadata / extensions ---
    standard_keys = {"kernelspec", "language_info"}
    feats.has_custom_metadata = any(k not in standard_keys for k in metadata)
    feats.extension_hints = _detect_extensions(nb, metadata)

    # --- Cells ---
    cells = nb.get("cells")
    if cells is None:
        # nbformat 3 stored cells inside worksheets.
        worksheets = nb.get("worksheets", [])
        cells = []
        if isinstance(worksheets, list):
            for ws in worksheets:
                if isinstance(ws, dict):
                    cells.extend(ws.get("cells", []) or [])
    if not isinstance(cells, list):
        cells = []

    exec_counts: list[int] = []
    code_line_total = 0
    all_tags: set[str] = set()
    code_cell_line_counts: list[int] = []

    for cell in cells:
        if not isinstance(cell, dict):
            feats.other_cells += 1
            continue
        feats.total_cells += 1
        ctype = cell.get("cell_type")
        text = _cell_source(cell)
        lines = _count_lines(text)
        feats.total_lines += lines
        feats.total_source_chars += len(text)

        # Tags
        cmeta = cell.get("metadata", {}) or {}
        if isinstance(cmeta, dict):
            tags = cmeta.get("tags")
            if isinstance(tags, list):
                all_tags.update(str(t) for t in tags)

        if ctype == "code":
            feats.code_cells += 1
            feats.code_lines += lines
            code_line_total += lines
            code_cell_line_counts.append(lines)
            if text.strip() == "":
                feats.empty_code_cells += 1

            ec = cell.get("execution_count")
            ec_int = _as_int(ec)
            if ec_int is not None and ec_int > 0:
                feats.executed_code_cells += 1
                exec_counts.append(ec_int)

            outputs = cell.get("outputs", [])
            if isinstance(outputs, list) and outputs:
                feats.cells_with_output += 1
                _tally_outputs(outputs, feats)
        elif ctype == "markdown":
            feats.markdown_cells += 1
            feats.markdown_lines += lines
        elif ctype == "raw":
            feats.raw_cells += 1
        else:
            feats.other_cells += 1

    # --- Derived structural stats ---
    if feats.total_cells:
        feats.avg_cell_lines = round(feats.total_lines / feats.total_cells, 3)
    if feats.code_cells:
        feats.avg_code_cell_lines = round(code_line_total / feats.code_cells, 3)
    # markdown-to-code ratio by lines (0 when no code lines).
    if feats.code_lines > 0:
        feats.markdown_to_code_ratio = round(feats.markdown_lines / feats.code_lines, 4)
    elif feats.markdown_lines > 0:
        # No code at all: treat as ratio relative to a single notional code line
        # so the value stays finite and JSON-serializable (avoids inf/NaN).
        feats.markdown_to_code_ratio = float(feats.markdown_lines)

    feats.distinct_cell_tags = sorted(all_tags)
    feats.cell_tag_count = len(all_tags)
    feats.has_any_output = feats.cells_with_output > 0

    # --- Execution order analysis ---
    _analyze_execution(exec_counts, feats)

    # --- Imports ---
    code_sources = [
        _cell_source(c) for c in cells if isinstance(c, dict) and c.get("cell_type") == "code"
    ]
    imports = extract_imports(code_sources)
    feats.imports = sorted(imports)
    feats.import_count = len(imports)


def _tally_outputs(outputs: list[Any], feats: NotebookFeatures) -> None:
    for out in outputs:
        if not isinstance(out, dict):
            continue
        feats.total_outputs += 1
        otype = out.get("output_type")
        data = out.get("data", {}) if isinstance(out.get("data"), dict) else {}
        # Largest output estimate (chars of the serialized data payload).
        try:
            size = len(json.dumps(out))
        except (TypeError, ValueError):
            size = 0
        feats.largest_output_bytes = max(feats.largest_output_bytes, size)

        if otype == "stream":
            feats.stream_outputs += 1
        elif otype == "error":
            feats.error_outputs += 1

        for mime in data:
            if str(mime).startswith("image/"):
                feats.image_outputs += 1
            elif mime == "text/html":
                feats.html_outputs += 1
            if mime == "application/vnd.jupyter.widget-view+json":
                feats.widget_output_count += 1


def _analyze_execution(exec_counts: list[int], feats: NotebookFeatures) -> None:
    if not exec_counts:
        return
    feats.max_execution_count = max(exec_counts)
    # Anomalies: an "in order" notebook has strictly increasing execution counts
    # in cell order. Count inversions as anomalies.
    anomalies = sum(1 for a, b in pairwise(exec_counts) if b <= a)
    feats.execution_order_anomalies = anomalies
    # Gaps: the executed counts should be 1..N contiguous for a clean run.
    expected = list(range(1, len(exec_counts) + 1))
    feats.has_execution_gaps = sorted(exec_counts) != expected
    feats.fully_executed_in_order = exec_counts == expected


def _as_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None
