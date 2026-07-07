"""Build the static dashboard site from the stored datasets.

Renders a single self-contained ``site/index.html`` (plus a copied stylesheet
and the CSV downloads) using Jinja2 and the Plotly figures. Plotly's JS is
loaded from the CDN by default but can be vendored for fully-offline builds.

The build is deterministic given the datasets and performs no network calls
(other than the browser later fetching the Plotly CDN script). It degrades
gracefully when only a single day of data exists.
"""

from __future__ import annotations

import datetime as dt
import shutil
from pathlib import Path
from typing import Any

import plotly.io as pio
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import PATHS, Paths
from ..logging_utils import get_logger
from ..storage.datasets import DatasetStore
from . import figures as F

logger = get_logger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"

# Libraries/metrics highlighted in the multi-series charts.
_TREND_LIBRARIES = [
    "pandas",
    "numpy",
    "matplotlib",
    "scikit_learn",
    "pytorch",
    "tensorflow",
    "plotly",
    "seaborn",
    "marimo",
    "ipywidgets",
]
_TREND_METRICS = [
    "reproducibility_score",
    "narrative_index",
    "complexity_score",
    "visualization_density",
    "ml_usage_score",
]


def _fig_div(fig: Any) -> str:
    """Convert a Plotly figure to an embeddable, responsive div (no full HTML)."""
    html: str = pio.to_html(
        fig,
        include_plotlyjs=False,
        full_html=False,
        config={"displayModeBar": False, "responsive": True},
    )
    return html


def _stat_cards(snapshots: Any, adoption: Any, repo_url: str) -> list[dict[str, str]]:
    latest = snapshots.sort_values("run_date").iloc[-1]
    latest_date = latest["run_date"]
    total_collected = int(snapshots["notebooks_collected"].sum())
    latest_adopt = adoption[adoption["run_date"] == latest_date]
    top = (
        latest_adopt.sort_values("adoption_pct", ascending=False).iloc[0]
        if len(latest_adopt)
        else None
    )
    cards = [
        {
            "value": f"{total_collected:,}",
            "label": "Notebooks observed (all time)",
            "sub": f"+{int(latest['notebooks_collected'])} today",
        },
        {
            "value": f"{int(latest['notebooks_parsed'])}",
            "label": "Parsed in latest run",
            "sub": f"{latest['parse_success_rate'] * 100:.0f}% parse success",
        },
        {
            "value": f"{latest.get('reproducibility_score_mean', 0) * 100:.0f}%",
            "label": "Mean reproducibility",
            "sub": "",
        },
        {
            "value": (top["library"] if top is not None else "—"),
            "label": "Most-used library",
            "sub": (f"{top['adoption_pct']:.0f}% of notebooks" if top is not None else ""),
        },
    ]
    return cards


def build_site(paths: Paths | None = None, repo_url: str | None = None) -> Path:
    """Render the static dashboard into ``site/``.

    Args:
        paths: Filesystem layout (defaults to the package config).
        repo_url: Canonical repository URL for links.

    Returns:
        Path to the generated ``index.html``.

    Raises:
        RuntimeError: if no daily snapshots exist yet (nothing to render).
    """
    paths = paths or PATHS
    repo_url = repo_url or "https://github.com/rodrigosf672/notebook-observatory"
    store = DatasetStore(paths)

    snapshots = store.read_daily_snapshots()
    adoption = store.read_library_adoption()
    if snapshots.empty:
        raise RuntimeError("No daily snapshots found; run a collection first.")

    paths.site.mkdir(parents=True, exist_ok=True)

    # Build figures.
    figs = {
        "ecosystem": _fig_div(F.ecosystem_size_timeseries(snapshots)),
        "pyversions": _fig_div(F.python_version_distribution(snapshots)),
        "treemap": _fig_div(F.category_treemap(adoption)),
        "ranking": _fig_div(F.library_adoption_ranking(adoption)),
        "adoption_trend": _fig_div(F.library_adoption_trends(adoption, _TREND_LIBRARIES)),
        "repro_gauge": _fig_div(F.reproducibility_gauge(snapshots)),
        "structural": _fig_div(F.structural_trends(snapshots)),
        "metric_trends": _fig_div(F.metric_trends(snapshots, _TREND_METRICS)),
    }

    # Library table (latest day, top 20).
    latest_date = snapshots.sort_values("run_date").iloc[-1]["run_date"]
    latest_adopt = (
        adoption[adoption["run_date"] == latest_date]
        .sort_values("adoption_pct", ascending=False)
        .head(20)
    )
    library_table = [
        {
            "library": r["library"],
            "category": r["category"],
            "adoption_pct": float(r["adoption_pct"]),
            "notebook_count": int(r["notebook_count"]),
            "color": F.CATEGORY_COLORS.get(r["category"], "#8899A6"),
        }
        for _, r in latest_adopt.iterrows()
    ]

    # Copy CSV downloads into the site so Pages can serve them directly.
    for src in (paths.daily_snapshots_csv, paths.library_adoption_csv):
        if src.exists():
            shutil.copy2(src, paths.site / src.name)

    css = (_TEMPLATE_DIR / "style.css").read_text(encoding="utf-8")

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
    )
    template = env.get_template("index.html.j2")
    html = template.render(
        inline_css=css,
        plotly_src=PLOTLY_CDN,
        repo_url=repo_url,
        generated_at=dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC"),
        latest_date=latest_date,
        n_days=int(snapshots["run_date"].nunique()),
        stat_cards=_stat_cards(snapshots, adoption, repo_url),
        figures=figs,
        library_table=library_table,
        data_links={
            "snapshots_csv": paths.daily_snapshots_csv.name,
            "adoption_csv": paths.library_adoption_csv.name,
        },
    )
    out = paths.site / "index.html"
    out.write_text(html, encoding="utf-8")
    # .nojekyll so GitHub Pages serves files starting with underscores etc.
    (paths.site / ".nojekyll").write_text("", encoding="utf-8")
    logger.info("Dashboard written to %s (%d KB)", out, len(html) // 1024)
    return out
