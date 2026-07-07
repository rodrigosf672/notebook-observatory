"""Tests for the static dashboard build.

The critical regression guarded here: Jinja2 autoescape must NOT escape the
Plotly figure HTML or the inline CSS (otherwise every chart renders as literal
`&#34;plotly-graph-div&#34;` code text instead of an interactive plot), while
still escaping values that come from collected data.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from notebook_observatory.analytics.aggregate import aggregate_day
from notebook_observatory.analytics.records import build_observation
from notebook_observatory.collectors.notebook_collector import CollectedNotebook
from notebook_observatory.config import Paths
from notebook_observatory.dashboard.build import build_site
from notebook_observatory.storage.datasets import DatasetStore

FIXTURES = Path(__file__).parent / "fixtures"


def _paths(tmp_path: Path) -> Paths:
    return Paths(
        repo_root=tmp_path,
        datasets=tmp_path / "datasets",
        observations=tmp_path / "datasets" / "observations",
        site=tmp_path / "site",
        cache=tmp_path / ".cache",
    )


def _collected(raw: str, stratum: str) -> CollectedNotebook:
    return CollectedNotebook(
        repo_full_name="owner/repo",
        path="nb.ipynb",
        ref="main",
        size_bytes=len(raw),
        raw=raw,
        repo_stars=10,
        repo_size_kb=100,
        repo_created_at="2020-01-01T00:00:00Z",
        repo_pushed_at="2026-01-01T00:00:00Z",
        strategy="s",
        stratum=stratum,
    )


@pytest.fixture
def built_site(tmp_path: Path) -> str:
    store = DatasetStore(_paths(tmp_path))
    raw = (FIXTURES / "good.ipynb").read_text(encoding="utf-8")
    # Two dates so the trend charts have >1 point.
    for d, ct in [("2020-01-01", "cohort"), ("2026-07-07", "daily")]:
        df = pd.DataFrame(
            [build_observation(_collected(raw, "stars:small"), d, collection_type=ct)]
        )
        snap, adopt = aggregate_day(df, d)
        store.write_observations(df, d)
        store.append_daily_snapshot(snap)
        store.append_library_adoption(adopt)
    out = build_site(paths=_paths(tmp_path))
    return out.read_text(encoding="utf-8")


def test_plotly_divs_are_not_escaped(built_site: str) -> None:
    # The interactive markers must appear verbatim, never HTML-escaped.
    assert 'class="plotly-graph-div"' in built_site
    assert "Plotly.newPlot" in built_site
    assert "&#34;plotly-graph-div&#34;" not in built_site


def test_inline_css_is_not_escaped(built_site: str) -> None:
    # A real CSS rule survives; no HTML entities leak into the <style> block.
    style = built_site.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "{" in style and "}" in style
    assert "&gt;" not in style
    assert "&#34;" not in style


def test_data_values_still_present(built_site: str) -> None:
    # The library table renders real detected libraries.
    assert "numpy" in built_site
    assert "Notebook Observatory" in built_site


def test_no_unrendered_template_tags(built_site: str) -> None:
    assert "{{" not in built_site
    assert "{%" not in built_site


def test_agent_section_present(built_site: str) -> None:
    # The Ask-the-observatory section and its embed must render.
    assert 'id="ask"' in built_site
    assert "Ask the observatory" in built_site
    assert "hf.space" in built_site  # the agent iframe embed URL


def test_removed_sections_absent(built_site: str) -> None:
    assert "Ecosystem overview" not in built_site
    assert "Structure &amp; quality" not in built_site
