"""Extract imported top-level module names from notebook code.

Two strategies are combined for robustness:

1. **AST parsing** (:mod:`ast`) — accurate for syntactically valid Python.
   Handles ``import a.b.c``, ``from a.b import c``, aliases, and relative
   imports (which are ignored, being intra-project).
2. **Regex fallback** — many notebook cells contain shell magics (``!pip``),
   line magics (``%matplotlib``), or fragments that are not valid modules in
   isolation, so AST parsing of the whole concatenated source can fail. When it
   does, a conservative regex recovers ``import``/``from`` statements line by
   line.

Only the *top-level* package of each import is returned (``import
sklearn.linear_model`` -> ``sklearn``), which is what the library registry keys
on.
"""

from __future__ import annotations

import ast
import re

# Matches "import x", "import x.y as z", "from x.y import ..." at line start.
_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+([a-zA-Z_][\w.]*)|from\s+([a-zA-Z_][\w.]*)\s+import)",
    re.MULTILINE,
)

# Lines that are notebook magics or shell escapes; stripped before AST parse.
_MAGIC_RE = re.compile(r"^\s*[%!].*$", re.MULTILINE)


def _top_level(module: str) -> str:
    return module.split(".", 1)[0]


def extract_imports_ast(source: str) -> set[str] | None:
    """Return top-level imported modules via AST, or ``None`` if parsing fails."""
    cleaned = _MAGIC_RE.sub("", source)
    try:
        tree = ast.parse(cleaned)
    except (SyntaxError, ValueError):
        return None
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(_top_level(alias.name))
        # level > 0 -> relative import (intra-project); skip.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(_top_level(node.module))
    return found


def extract_imports_regex(source: str) -> set[str]:
    """Recover top-level imports with a line-oriented regex (fallback path)."""
    found: set[str] = set()
    for m in _IMPORT_RE.finditer(source):
        module = m.group(1) or m.group(2)
        if module:
            found.add(_top_level(module))
    return found


def extract_imports(code_sources: list[str]) -> set[str]:
    """Extract the union of top-level imports across a list of code-cell sources.

    Each cell is parsed independently so that one unparseable cell does not
    discard imports from the rest of the notebook.

    Args:
        code_sources: The source text of each code cell.

    Returns:
        Set of top-level module names imported anywhere in the notebook.
    """
    all_imports: set[str] = set()
    for src in code_sources:
        via_ast = extract_imports_ast(src)
        if via_ast is None:
            all_imports |= extract_imports_regex(src)
        else:
            all_imports |= via_ast
            # Also run the regex to catch imports inside try/except or funcs that
            # AST already found — union is harmless and improves recall on
            # partially valid cells.
            all_imports |= extract_imports_regex(src)
    # Drop obviously non-module tokens.
    return {m for m in all_imports if m and m.isidentifier()}
