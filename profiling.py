"""
Platform-agnostic data profiling engine.

`DataProfiler` accepts any `BasePlatform` connector and computes per-column
statistics by delegating all SQL generation to the platform.  No Snowflake-
specific code lives here — the same class drives Snowflake, SQLite and
Databricks profiles.

Column statistics by type:
  numeric  — min, max, mean, median, stddev, variance, q1, q3, skewness, kurtosis
  string   — min_length, max_length, avg_length
  date     — min_date, max_date
  all      — row_count, null_count, null_rate, distinct_count, uniqueness_rate

A per-column try/except ensures that one bad column (e.g. Snowflake error
100051 on a constant-value column) is recorded in ColumnProfile.error without
aborting the rest of the table profile.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


def _safe_float(v) -> Optional[float]:
    return float(v) if v is not None else None


@dataclass
class ColumnProfile:
    name:            str
    data_type:       str
    row_count:       int
    null_count:      int
    null_rate:       float
    distinct_count:  int
    uniqueness_rate: float
    comment:         Optional[str]   = None
    error:           Optional[str]   = None
    # Numeric stats
    min_val:  Optional[float] = None
    max_val:  Optional[float] = None
    mean:     Optional[float] = None
    median:   Optional[float] = None
    std_dev:  Optional[float] = None
    variance: Optional[float] = None
    skewness: Optional[float] = None
    kurtosis: Optional[float] = None
    q1:       Optional[float] = None
    q3:       Optional[float] = None
    # String stats
    min_length: Optional[int]   = None
    max_length: Optional[int]   = None
    avg_length: Optional[float] = None
    # Date stats
    min_date: Optional[str] = None
    max_date: Optional[str] = None


@dataclass
class TableProfile:
    database:     str
    schema:       str
    table:        str
    row_count:    int
    column_count: int
    columns:      List[ColumnProfile]
    platform:     str           = "snowflake"
    source_name:  Optional[str] = None   # user-facing label from config.source_name
    comment:      Optional[str] = None
    profiled_at:  str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DataProfiler:
    """Profile a table using any BasePlatform connector."""

    def __init__(self, conn):
        """conn — any BasePlatform subclass (SnowflakePlatform, SQLitePlatform, …)."""
        self.conn = conn

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_tables(self, database: str, schema: str) -> List[str]:
        return self.conn.get_tables(database, schema)

    def get_columns(self, database: str, schema: str, table: str) -> list:
        return self.conn.get_columns(database, schema, table)

    def profile_table(self, database: str, schema: str, table: str) -> TableProfile:
        tbl_ref  = self.conn.table_ref(database, schema, table)
        columns  = self.get_columns(database, schema, table)
        comment  = self.conn.get_table_comment(database, schema, table)
        row_row  = self.conn.fetch_one(f"SELECT COUNT(*) FROM {tbl_ref}")
        row_count = int(row_row[0]) if row_row else 0

        col_profiles: List[ColumnProfile] = []
        for col_name, data_type, col_comment in columns:
            try:
                col_profiles.append(
                    self._profile_column(tbl_ref, col_name, data_type, row_count, col_comment or None)
                )
            except Exception as exc:
                col_profiles.append(ColumnProfile(
                    name=col_name, data_type=data_type, row_count=row_count,
                    null_count=0, null_rate=0.0, distinct_count=0, uniqueness_rate=0.0,
                    comment=col_comment or None,
                    error=f"{type(exc).__name__}: {exc}",
                ))

        return TableProfile(
            database=database, schema=schema, table=table,
            row_count=row_count, column_count=len(columns),
            columns=col_profiles,
            platform=self.conn.dialect,
            comment=comment,
        )

    # ── Column profiling ───────────────────────────────────────────────────────

    def _profile_column(
        self, tbl_ref: str, col_name: str, data_type: str,
        row_count: int, comment: Optional[str] = None,
    ) -> ColumnProfile:
        q_col    = f'"{col_name}"'
        col_type = self.conn.categorize_type(data_type)

        # Basic stats — null count + distinct count
        base = self.conn.fetch_one(self.conn.basic_stats_sql(tbl_ref, q_col))
        null_count     = int(base[0]) if base and base[0] is not None else 0
        distinct_count = int(base[1]) if base and base[1] is not None else 0
        null_rate      = null_count     / row_count if row_count else 0.0
        uniqueness_rate = distinct_count / row_count if row_count else 0.0

        profile = ColumnProfile(
            name=col_name, data_type=data_type, row_count=row_count,
            null_count=null_count, null_rate=null_rate,
            distinct_count=distinct_count, uniqueness_rate=uniqueness_rate,
            comment=comment,
        )

        if row_count == 0:
            return profile

        if col_type == "numeric":
            # Query 1: safe distribution stats — SKEW/KURTOSIS excluded here because
            # they throw 100051 (Division by zero) on constant columns (stddev = 0).
            r = self.conn.fetch_one(self.conn.numeric_stats_sql(tbl_ref, q_col))
            if r:
                profile.min_val  = _safe_float(r[0])
                profile.max_val  = _safe_float(r[1])
                profile.mean     = _safe_float(r[2])
                profile.median   = _safe_float(r[3])
                profile.std_dev  = _safe_float(r[4])
                profile.variance = _safe_float(r[5])
                profile.q1       = _safe_float(r[6])
                profile.q3       = _safe_float(r[7])

            # Query 2: SKEW/KURTOSIS only when stddev > 0 and platform supports it
            skew_sql = self.conn.skew_kurt_sql(tbl_ref, q_col)
            if skew_sql and profile.std_dev and profile.std_dev > 0:
                try:
                    r2 = self.conn.fetch_one(skew_sql)
                    if r2:
                        profile.skewness = _safe_float(r2[0])
                        profile.kurtosis = _safe_float(r2[1])
                except Exception:
                    pass   # best-effort

        elif col_type == "string":
            r = self.conn.fetch_one(self.conn.string_stats_sql(tbl_ref, q_col))
            if r:
                profile.min_length = int(r[0]) if r[0] is not None else None
                profile.max_length = int(r[1]) if r[1] is not None else None
                profile.avg_length = _safe_float(r[2])

        elif col_type == "date":
            r = self.conn.fetch_one(self.conn.date_stats_sql(tbl_ref, q_col))
            if r:
                profile.min_date = str(r[0]) if r[0] is not None else None
                profile.max_date = str(r[1]) if r[1] is not None else None

        return profile
