"""Tests for the append-only storage layer and daily aggregation."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from notebook_observatory.analytics.aggregate import aggregate_day
from notebook_observatory.analytics.records import build_observation
from notebook_observatory.collectors.notebook_collector import CollectedNotebook
from notebook_observatory.config import Paths
from notebook_observatory.storage.datasets import DatasetStore


def _make_paths(tmp_path: Path) -> Paths:
    return Paths(
        repo_root=tmp_path,
        datasets=tmp_path / "datasets",
        observations=tmp_path / "datasets" / "observations",
        site=tmp_path / "site",
        cache=tmp_path / ".cache",
    )


def _fake_collected(raw: str, stratum: str = "stars:small") -> CollectedNotebook:
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
        strategy="star_bucket_small",
        stratum=stratum,
    )


def _observations_df(fixtures: Path, run_date: str) -> pd.DataFrame:
    rows = []
    for name in ["good.ipynb", "messy.ipynb"]:
        raw = (fixtures / name).read_text(encoding="utf-8")
        rows.append(build_observation(_fake_collected(raw), run_date))
    return pd.DataFrame(rows)


@pytest.fixture
def fixtures() -> Path:
    return Path(__file__).parent / "fixtures"


def test_write_and_read_observations_roundtrip(tmp_path: Path, fixtures: Path) -> None:
    store = DatasetStore(_make_paths(tmp_path))
    df = _observations_df(fixtures, "2026-07-01")
    store.write_observations(df, "2026-07-01")
    back = store.read_observations("2026-07-01")
    assert len(back) == len(df)
    assert (back["run_date"] == "2026-07-01").all()


def test_snapshot_append_is_idempotent_per_date(tmp_path: Path, fixtures: Path) -> None:
    store = DatasetStore(_make_paths(tmp_path))
    df = _observations_df(fixtures, "2026-07-01")
    snap, _adopt = aggregate_day(df, "2026-07-01")
    store.append_daily_snapshot(snap)
    store.append_daily_snapshot(snap)  # re-run same date
    snaps = store.read_daily_snapshots()
    assert (snaps["run_date"] == "2026-07-01").sum() == 1


def test_history_is_preserved_across_dates(tmp_path: Path, fixtures: Path) -> None:
    store = DatasetStore(_make_paths(tmp_path))
    for d in ["2026-07-01", "2026-07-02", "2026-07-03"]:
        df = _observations_df(fixtures, d)
        snap, adopt = aggregate_day(df, d)
        store.write_observations(df, d)
        store.append_daily_snapshot(snap)
        store.append_library_adoption(adopt)
    snaps = store.read_daily_snapshots()
    assert sorted(snaps["run_date"].unique()) == ["2026-07-01", "2026-07-02", "2026-07-03"]
    # re-running an old date must not drop the others.
    df = _observations_df(fixtures, "2026-07-01")
    snap, _ = aggregate_day(df, "2026-07-01")
    store.append_daily_snapshot(snap)
    assert store.read_daily_snapshots()["run_date"].nunique() == 3


def test_all_observations_concat(tmp_path: Path, fixtures: Path) -> None:
    store = DatasetStore(_make_paths(tmp_path))
    for d in ["2026-07-01", "2026-07-02"]:
        store.write_observations(_observations_df(fixtures, d), d)
    allobs = store.read_observations()
    assert allobs["run_date"].nunique() == 2


def test_duckdb_rebuild_and_query(tmp_path: Path, fixtures: Path) -> None:
    store = DatasetStore(_make_paths(tmp_path))
    df = _observations_df(fixtures, "2026-07-01")
    snap, adopt = aggregate_day(df, "2026-07-01")
    store.write_observations(df, "2026-07-01")
    store.append_daily_snapshot(snap)
    store.append_library_adoption(adopt)
    store.rebuild_duckdb()
    out = store.query("SELECT COUNT(*) AS n FROM observations")
    assert int(out["n"].iloc[0]) == len(df)


def test_cohort_collection_type_threads_through(tmp_path: Path, fixtures: Path) -> None:
    # Build observations tagged as a cohort and confirm the label reaches the
    # snapshot and adoption tables.
    from notebook_observatory.analytics.records import build_observation

    rows = []
    for name in ["good.ipynb", "messy.ipynb"]:
        raw = (fixtures / name).read_text(encoding="utf-8")
        rows.append(build_observation(_fake_collected(raw), "2018-01-01", collection_type="cohort"))
    df = pd.DataFrame(rows)
    assert (df["collection_type"] == "cohort").all()
    snap, adopt = aggregate_day(df, "2018-01-01")
    assert snap.iloc[0]["collection_type"] == "cohort"
    assert (adopt["collection_type"] == "cohort").all()


def test_daily_is_default_collection_type(fixtures: Path) -> None:
    from notebook_observatory.analytics.records import build_observation

    raw = (fixtures / "good.ipynb").read_text(encoding="utf-8")
    row = build_observation(_fake_collected(raw), "2026-07-07")
    assert row["collection_type"] == "daily"


def test_aggregate_snapshot_fields(tmp_path: Path, fixtures: Path) -> None:
    df = _observations_df(fixtures, "2026-07-01")
    snap, adopt = aggregate_day(df, "2026-07-01")
    row = snap.iloc[0]
    assert row["notebooks_collected"] == 2
    assert row["notebooks_parsed"] == 2
    assert 0.0 <= row["reproducibility_score_mean"] <= 1.0
    # adoption long-format has one row per known library.
    assert (adopt["run_date"] == "2026-07-01").all()
    assert adopt["adoption_pct"].max() <= 100.0
