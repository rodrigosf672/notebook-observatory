"""Plotly figure builders for the dashboard.

Every function takes tidy DataFrames (from the storage layer) and returns a
Plotly :class:`~plotly.graph_objects.Figure` styled for a dark, mobile-friendly
dashboard. Figures are converted to embeddable ``<div>`` fragments by the build
module; none of these functions performs I/O.

Chart choices are driven by the shape of each question, not novelty:

* **time series / stacked area** for growth and adoption over time,
* **horizontal bar** for rankings (readable labels on mobile),
* **treemap** for the library-category composition (part-to-whole),
* **grouped bar** for Python-version distribution,
* **line** for metric trends.

When only a single day of data exists, time-series panels degrade gracefully to
a labelled single-point/annotated view rather than an empty axis.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# Dark theme palette (colour-blind-aware qualitative set).
PALETTE = [
    "#4C9AFF",
    "#57D9A3",
    "#FFC400",
    "#FF7452",
    "#B37FEB",
    "#00B8D9",
    "#F76707",
    "#79E2F2",
    "#8777D9",
    "#36B37E",
]
CATEGORY_COLORS = {
    "array": "#4C9AFF",
    "scientific": "#00B8D9",
    "ml": "#FFC400",
    "deep_learning": "#FF7452",
    "plotting": "#57D9A3",
    "interactive": "#B37FEB",
    "notebook_tech": "#F76707",
    "llm": "#8777D9",
    "data_io": "#79E2F2",
}

_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, system-ui, sans-serif", size=13, color="#E6EDF3"),
    margin=dict(l=50, r=20, t=50, b=45),
    hovermode="closest",
    colorway=PALETTE,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
)


def _apply(fig: go.Figure, title: str) -> go.Figure:
    fig.update_layout(title=dict(text=title, x=0.01, xanchor="left"), **_LAYOUT)
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.06)", zeroline=False)
    fig.update_yaxes(gridcolor="rgba(255,255,255,0.06)", zeroline=False)
    return fig


def ecosystem_size_timeseries(snapshots: pd.DataFrame) -> go.Figure:
    """Notebooks collected & parsed per run date (sampling volume over time)."""
    fig = go.Figure()
    df = snapshots.sort_values("run_date")
    fig.add_trace(
        go.Scatter(
            x=df["run_date"],
            y=df["notebooks_collected"],
            name="Collected",
            mode="lines+markers",
            line=dict(width=2.5, color=PALETTE[0]),
            fill="tozeroy",
            fillcolor="rgba(76,154,255,0.12)",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=df["run_date"],
            y=df["notebooks_parsed"],
            name="Parsed OK",
            mode="lines+markers",
            line=dict(width=2, color=PALETTE[1], dash="dot"),
        )
    )
    return _apply(fig, "Daily sample volume")


def library_adoption_ranking(adoption: pd.DataFrame, top_n: int = 15) -> go.Figure:
    """Horizontal bar of the most-adopted libraries on the latest run date."""
    latest = adoption["run_date"].max()
    df = (
        adoption[adoption["run_date"] == latest]
        .sort_values("adoption_pct", ascending=True)
        .tail(top_n)
    )
    colors = [CATEGORY_COLORS.get(c, "#8899A6") for c in df["category"]]
    fig = go.Figure(
        go.Bar(
            x=df["adoption_pct"],
            y=df["library"],
            orientation="h",
            marker_color=colors,
            hovertemplate="%{y}: %{x:.1f}%<extra></extra>",
            text=df["adoption_pct"].map(lambda v: f"{v:.0f}%"),
            textposition="auto",
        )
    )
    fig.update_layout(**{**_LAYOUT, "margin": dict(l=110, r=20, t=50, b=45)})
    fig.update_layout(title=dict(text=f"Top libraries — {latest}", x=0.01))
    fig.update_xaxes(title="% of parsed notebooks", gridcolor="rgba(255,255,255,0.06)")
    fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
    return fig


def library_adoption_trends(adoption: pd.DataFrame, libraries: list[str]) -> go.Figure:
    """Stacked area of selected libraries' adoption over time."""
    fig = go.Figure()
    df = adoption[adoption["library"].isin(libraries)].sort_values("run_date")
    for i, lib in enumerate(libraries):
        sub = df[df["library"] == lib]
        if sub.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=sub["run_date"],
                y=sub["adoption_pct"],
                name=lib,
                mode="lines",
                stackgroup="one",
                line=dict(width=0.5, color=PALETTE[i % len(PALETTE)]),
            )
        )
    return _apply(fig, "Library adoption over time (stacked %)")


def category_treemap(adoption: pd.DataFrame) -> go.Figure:
    """Treemap of library-category composition on the latest run date."""
    latest = adoption["run_date"].max()
    df = adoption[adoption["run_date"] == latest]
    grp = df.groupby("category", as_index=False)["notebook_count"].sum()
    grp = grp[grp["notebook_count"] > 0]
    fig = px.treemap(
        grp,
        path=["category"],
        values="notebook_count",
        color="category",
        color_discrete_map=CATEGORY_COLORS,
    )
    fig.update_layout(**{**_LAYOUT, "margin": dict(l=10, r=10, t=50, b=10)})
    fig.update_layout(title=dict(text=f"Library-category composition — {latest}", x=0.01))
    fig.update_traces(hovertemplate="%{label}: %{value} notebooks<extra></extra>")
    return fig


def python_version_distribution(snapshots: pd.DataFrame) -> go.Figure:
    """Grouped bar of Python-version share on the latest run date."""
    latest = snapshots.sort_values("run_date").iloc[-1]
    pyver_cols = [c for c in snapshots.columns if c.startswith("pyver_") and c.endswith("_pct")]
    rows = []
    for c in pyver_cols:
        val = latest.get(c)
        if pd.notna(val) and val:
            ver = c.replace("pyver_", "").replace("_pct", "").replace("_", ".")
            rows.append((ver, float(val)))
    rows.sort(key=lambda r: [int(x) for x in r[0].split(".") if x.isdigit()] or [0])
    fig = go.Figure(
        go.Bar(
            x=[r[0] for r in rows],
            y=[r[1] for r in rows],
            marker_color=PALETTE[0],
            text=[f"{r[1]:.0f}%" for r in rows],
            textposition="auto",
            hovertemplate="Python %{x}: %{y:.1f}%<extra></extra>",
        )
    )
    fig = _apply(fig, f"Python version share — {latest['run_date']}")
    fig.update_yaxes(title="% of notebooks declaring a version")
    return fig


def metric_trends(snapshots: pd.DataFrame, metrics: list[str]) -> go.Figure:
    """Line chart of selected daily metric means over time."""
    fig = go.Figure()
    df = snapshots.sort_values("run_date")
    for i, m in enumerate(metrics):
        col = f"{m}_mean"
        if col not in df.columns:
            continue
        label = m.replace("_", " ").title()
        fig.add_trace(
            go.Scatter(
                x=df["run_date"],
                y=df[col],
                name=label,
                mode="lines+markers",
                line=dict(width=2, color=PALETTE[i % len(PALETTE)]),
            )
        )
    fig = _apply(fig, "Derived metric trends (daily mean)")
    fig.update_yaxes(range=[0, 1], title="score (0–1)")
    return fig


def structural_trends(snapshots: pd.DataFrame) -> go.Figure:
    """Dual-axis-style view of mean cells and markdown ratio over time."""
    fig = go.Figure()
    df = snapshots.sort_values("run_date")
    fig.add_trace(
        go.Scatter(
            x=df["run_date"],
            y=df["mean_total_cells"],
            name="Mean cells / notebook",
            mode="lines+markers",
            line=dict(width=2, color=PALETTE[2]),
        )
    )
    if "documentation_density_mean" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["run_date"],
                y=df["documentation_density_mean"] * 100,
                name="Doc density (%)",
                mode="lines+markers",
                line=dict(width=2, color=PALETTE[4]),
                yaxis="y2",
            )
        )
    layout = {**_LAYOUT}
    fig.update_layout(**layout)
    fig.update_layout(
        title=dict(text="Structure & documentation over time", x=0.01),
        yaxis=dict(title="mean cells", gridcolor="rgba(255,255,255,0.06)"),
        yaxis2=dict(title="doc %", overlaying="y", side="right", range=[0, 100], showgrid=False),
    )
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.06)")
    return fig


def reproducibility_gauge(snapshots: pd.DataFrame) -> go.Figure:
    """Indicator of the latest reproducibility-score mean vs the previous run."""
    df = snapshots.sort_values("run_date")
    latest = float(df["reproducibility_score_mean"].iloc[-1])
    prev = float(df["reproducibility_score_mean"].iloc[-2]) if len(df) > 1 else latest
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number+delta",
            value=round(latest * 100, 1),
            number={"suffix": "%"},
            delta={"reference": round(prev * 100, 1), "suffix": "%"},
            gauge={
                "axis": {"range": [0, 100], "tickcolor": "#E6EDF3"},
                "bar": {"color": PALETTE[1]},
                "bgcolor": "rgba(255,255,255,0.04)",
                "steps": [
                    {"range": [0, 40], "color": "rgba(255,116,82,0.25)"},
                    {"range": [40, 70], "color": "rgba(255,196,0,0.20)"},
                    {"range": [70, 100], "color": "rgba(87,217,163,0.20)"},
                ],
            },
        )
    )
    fig.update_layout(**{**_LAYOUT, "margin": dict(l=20, r=20, t=50, b=10)})
    fig.update_layout(title=dict(text="Mean reproducibility score", x=0.01))
    return fig
