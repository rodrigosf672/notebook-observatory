"""Extensible, data-driven library detection.

The detection registry lives in :file:`libraries.yaml` beside this module. Each
canonical library declares the top-level import module names that indicate its
use and a category for family-level roll-ups. Detection is therefore a pure
lookup from the parser's extracted imports to canonical libraries — extending
coverage means editing the YAML, never the code.

Public API
----------
* :func:`get_registry` — load (and cache) the registry.
* :func:`detect_libraries` — map a set of imported modules to canonical
  libraries and categories present in a notebook.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..logging_utils import get_logger

logger = get_logger(__name__)

_REGISTRY_PATH = Path(__file__).resolve().parent / "libraries.yaml"


@dataclass(frozen=True)
class LibrarySpec:
    """One canonical library and how to recognize it."""

    canonical: str
    modules: frozenset[str]
    category: str
    pypi: str | None = None


@dataclass
class Registry:
    """The loaded detection registry with a reverse module -> library index."""

    libraries: dict[str, LibrarySpec] = field(default_factory=dict)
    _module_index: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for spec in self.libraries.values():
            for module in spec.modules:
                self._module_index[module] = spec.canonical

    @property
    def categories(self) -> set[str]:
        return {s.category for s in self.libraries.values()}

    def library_for_module(self, module: str) -> str | None:
        return self._module_index.get(module)


@dataclass
class DetectionResult:
    """Libraries and categories detected in a single notebook."""

    libraries: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)

    def has(self, canonical: str) -> bool:
        return canonical in self.libraries

    def has_category(self, category: str) -> bool:
        return category in self.categories


@lru_cache(maxsize=1)
def get_registry(path: str | None = None) -> Registry:
    """Load and cache the detection registry.

    Args:
        path: Optional override path to a registry YAML (used in tests).

    Returns:
        A :class:`Registry`.
    """
    reg_path = Path(path) if path else _REGISTRY_PATH
    raw: dict[str, Any] = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
    libs: dict[str, LibrarySpec] = {}
    for canonical, spec in (raw.get("libraries") or {}).items():
        modules = frozenset(spec.get("modules", []))
        libs[canonical] = LibrarySpec(
            canonical=canonical,
            modules=modules,
            category=spec.get("category", "other"),
            pypi=spec.get("pypi"),
        )
    logger.debug("Loaded detection registry: %d libraries.", len(libs))
    return Registry(libraries=libs)


def detect_libraries(imports: Iterable[str], registry: Registry | None = None) -> DetectionResult:
    """Map imported top-level modules to canonical libraries and categories.

    Args:
        imports: Top-level module names extracted from a notebook.
        registry: Optional registry (defaults to the cached global one).

    Returns:
        A :class:`DetectionResult` with sorted, de-duplicated lists.
    """
    reg = registry or get_registry()
    found_libs: set[str] = set()
    found_cats: set[str] = set()
    for module in imports:
        canonical = reg.library_for_module(module)
        if canonical:
            found_libs.add(canonical)
            found_cats.add(reg.libraries[canonical].category)
    return DetectionResult(
        libraries=sorted(found_libs),
        categories=sorted(found_cats),
    )


def all_known_libraries(registry: Registry | None = None) -> list[str]:
    """Return every canonical library name in the registry (sorted)."""
    reg = registry or get_registry()
    return sorted(reg.libraries)
