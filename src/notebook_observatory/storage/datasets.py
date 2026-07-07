"""Append-only longitudinal storage.

The observatory's value comes from *never losing history*. This module is the
sole writer of the ``datasets/`` tree and guarantees:

* **Per-notebook observations** are written to a date-partitioned Parquet
  dataset: ``datasets/observations/date=YYYY-MM-DD/observations.parquet``. Each
  run writes exactly one partition; re-running a date overwrites only that
  date's partition (idempotent per date), never another day's data.
* **Daily snapshots** are appended to a single ``datasets/daily_snapshots.parquet``
  (and a mirrored CSV). Re-running a date replaces that date's row in place —
  the rest of history is untouched.
* **Library adoption** is appended to ``datasets/library_adoption.parquet``
  (+ CSV) with the same per-date idempotency.
* A **DuckDB** database (``datasets/observatory.duckdb``) exposes SQL views over
  the Parquet files for ad-hoc querying and for the dashboard build.

Idempotency (replace-by-date rather than blind append) means a re-run — e.g. a
retried GitHub Actions job — cannot create duplicate rows, while genuinely new
dates always accumulate. History for other dates is guaranteed immutable by
construction: writes only ever touch the current run date's partition/rows.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from ..config import PATHS, Paths
from ..logging_utils import get_logger

logger = get_logger(__name__)


class DatasetStore:
    """Append-only reader/writer for the observatory datasets."""

    def __init__(self, paths: Paths | None = None) -> None:
        self.paths = paths or PATHS
        self.paths.ensure()

    # ------------------------------------------------------------------ #
    # Per-notebook observations (date-partitioned Parquet)
    # ------------------------------------------------------------------ #
    def partition_path(self, run_date: str) -> Path:
        return self.paths.observations / f"date={run_date}" / "observations.parquet"

    def write_observations(self, observations: pd.DataFrame, run_date: str) -> Path:
        """Write one date's per-notebook observations (idempotent per date)."""
        part = self.partition_path(run_date)
        part.parent.mkdir(parents=True, exist_ok=True)
        df = observations.copy()
        if "run_date" not in df.columns:
            df.insert(0, "run_date", run_date)
        df.to_parquet(part, index=False)
        logger.info("Wrote %d observations to %s", len(df), part)
        return part

    def read_observations(self, run_date: str | None = None) -> pd.DataFrame:
        """Read observations for one date, or all dates when ``run_date`` is None."""
        if run_date is not None:
            part = self.partition_path(run_date)
            if not part.exists():
                return pd.DataFrame()
            return pd.read_parquet(part)
        parts = sorted(self.paths.observations.glob("date=*/observations.parquet"))
        if not parts:
            return pd.DataFrame()
        return pd.concat((pd.read_parquet(p) for p in parts), ignore_index=True)

    # ------------------------------------------------------------------ #
    # Append-by-date tables (snapshots, adoption)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _upsert_by_date(existing: pd.DataFrame, new: pd.DataFrame, key: str) -> pd.DataFrame:
        """Replace rows whose ``key`` matches values in ``new``, then append.

        This makes a re-run of the same date idempotent while preserving all
        other history.
        """
        if existing.empty:
            combined = new.copy()
        else:
            keys = set(new[key].unique())
            kept = existing[~existing[key].isin(keys)]
            combined = pd.concat([kept, new], ignore_index=True)
        # Deterministic ordering by the date key for readable CSVs/diffs.
        return combined.sort_values(key).reset_index(drop=True)

    def _append_table(
        self, new: pd.DataFrame, parquet_path: Path, csv_path: Path, key: str = "run_date"
    ) -> None:
        existing = pd.read_parquet(parquet_path) if parquet_path.exists() else pd.DataFrame()
        combined = self._upsert_by_date(existing, new, key)
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(parquet_path, index=False)
        combined.to_csv(csv_path, index=False)
        logger.info("Updated %s (%d total rows).", parquet_path.name, len(combined))

    def append_daily_snapshot(self, snapshot: pd.DataFrame) -> None:
        """Append (or replace by date) one daily snapshot row."""
        self._append_table(
            snapshot, self.paths.daily_snapshots_parquet, self.paths.daily_snapshots_csv
        )

    def append_library_adoption(self, adoption: pd.DataFrame) -> None:
        """Append (or replace by date) one day's library-adoption rows.

        Uniqueness is per (run_date), so all of a date's library rows are
        replaced together on re-run.
        """
        self._append_table(
            adoption, self.paths.library_adoption_parquet, self.paths.library_adoption_csv
        )

    # ------------------------------------------------------------------ #
    # Readers
    # ------------------------------------------------------------------ #
    def read_daily_snapshots(self) -> pd.DataFrame:
        p = self.paths.daily_snapshots_parquet
        return pd.read_parquet(p) if p.exists() else pd.DataFrame()

    def read_library_adoption(self) -> pd.DataFrame:
        p = self.paths.library_adoption_parquet
        return pd.read_parquet(p) if p.exists() else pd.DataFrame()

    # ------------------------------------------------------------------ #
    # DuckDB view layer
    # ------------------------------------------------------------------ #
    def rebuild_duckdb(self) -> Path:
        """(Re)build the DuckDB database with views over the Parquet datasets.

        The database stores *views*, not copies, so it stays small and always
        reflects the current Parquet files. Safe to delete and rebuild anytime.
        """
        db_path = self.paths.duckdb_file
        if db_path.exists():
            db_path.unlink()
        con = duckdb.connect(str(db_path))
        try:
            obs_glob = str(self.paths.observations / "date=*/observations.parquet")
            snap = self.paths.daily_snapshots_parquet
            adopt = self.paths.library_adoption_parquet

            if any(self.paths.observations.glob("date=*/observations.parquet")):
                con.execute(
                    f"CREATE VIEW observations AS SELECT * FROM read_parquet('{obs_glob}', "
                    "union_by_name=true, filename=true)"
                )
            if snap.exists():
                con.execute(f"CREATE VIEW daily_snapshots AS SELECT * FROM read_parquet('{snap}')")
            if adopt.exists():
                con.execute(
                    f"CREATE VIEW library_adoption AS SELECT * FROM read_parquet('{adopt}')"
                )
            logger.info("Rebuilt DuckDB at %s", db_path)
        finally:
            con.close()
        return db_path

    def query(self, sql: str) -> pd.DataFrame:
        """Run a read-only SQL query against the DuckDB view layer."""
        con = duckdb.connect(str(self.paths.duckdb_file), read_only=True)
        try:
            return con.execute(sql).fetchdf()
        finally:
            con.close()
