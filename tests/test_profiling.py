"""Tests for profiling.py — DataProfiler with real SQLite and mock platforms."""

import sqlite3
from unittest.mock import MagicMock, call

import pytest

from profiling import ColumnProfile, DataProfiler, TableProfile, _safe_float
from platforms.sqlite_platform import SQLitePlatform


# ── _safe_float ───────────────────────────────────────────────────────────────

class TestSafeFloat:
    def test_converts_number(self):
        assert _safe_float(3.14) == pytest.approx(3.14)

    def test_converts_string_number(self):
        assert _safe_float("2.5") == pytest.approx(2.5)

    def test_returns_none_for_none(self):
        assert _safe_float(None) is None


# ── ColumnProfile dataclass ───────────────────────────────────────────────────

class TestColumnProfile:
    def test_required_fields(self):
        col = ColumnProfile("id", "INTEGER", 100, 0, 0.0, 100, 1.0)
        assert col.name == "id"
        assert col.error is None
        assert col.comment is None

    def test_all_optional_fields_default_none(self):
        col = ColumnProfile("c", "TEXT", 10, 0, 0.0, 5, 0.5)
        for field in ("min_val", "max_val", "mean", "median", "std_dev",
                      "variance", "skewness", "kurtosis", "q1", "q3",
                      "min_length", "max_length", "avg_length",
                      "min_date", "max_date"):
            assert getattr(col, field) is None


# ── TableProfile dataclass ────────────────────────────────────────────────────

class TestTableProfile:
    def test_defaults(self):
        tp = TableProfile("D", "S", "T", 10, 1, [])
        assert tp.platform == "snowflake"
        assert tp.comment is None
        assert tp.profiled_at != ""


# ── DataProfiler — mock platform ─────────────────────────────────────────────

class TestDataProfilerWithMock:
    @pytest.fixture()
    def mock_platform(self):
        p = MagicMock()
        p.dialect = "mock"
        p.table_ref.return_value = '"DB"."SC"."T"'
        p.get_columns.return_value = []
        p.get_table_comment.return_value = "A comment"
        p.fetch_one.return_value = (0,)   # row count
        return p

    def test_get_tables_delegates_to_platform(self, mock_platform):
        mock_platform.get_tables.return_value = ["T1", "T2"]
        profiler = DataProfiler(mock_platform)
        tables = profiler.get_tables("DB", "SC")
        mock_platform.get_tables.assert_called_once_with("DB", "SC")
        assert tables == ["T1", "T2"]

    def test_profile_table_empty_columns(self, mock_platform):
        mock_platform.fetch_one.return_value = (42,)
        profile = DataProfiler(mock_platform).profile_table("DB", "SC", "T")
        assert profile.row_count == 42
        assert profile.columns == []
        assert profile.comment == "A comment"
        assert profile.platform == "mock"

    def test_column_error_sets_error_field(self, mock_platform):
        """A column whose stats query fails must be recorded with an error."""
        mock_platform.get_columns.return_value = [("bad_col", "NUMBER", None)]
        mock_platform.categorize_type.return_value = "numeric"
        mock_platform.basic_stats_sql.return_value = "BAD SQL"
        mock_platform.fetch_one.side_effect = [
            (100,),           # row count fetch
            RuntimeError("division by zero"),  # basic_stats fails
        ]
        profile = DataProfiler(mock_platform).profile_table("DB", "SC", "T")
        assert profile.column_count == 1
        col = profile.columns[0]
        assert col.error is not None
        assert "division" in col.error.lower() or "RuntimeError" in col.error

    def test_zero_row_table_skips_stats(self, mock_platform):
        mock_platform.get_columns.return_value = [("id", "NUMBER", None)]
        mock_platform.categorize_type.return_value = "numeric"
        mock_platform.basic_stats_sql.return_value = "B"
        mock_platform.fetch_one.side_effect = [
            (0,),           # row count = 0
            (0, 0),         # basic_stats
        ]
        profile = DataProfiler(mock_platform).profile_table("DB", "SC", "T")
        col = profile.columns[0]
        assert col.mean is None     # stats were skipped

    def test_skew_kurt_skipped_when_none(self, mock_platform):
        mock_platform.get_columns.return_value = [("amount", "NUMBER", None)]
        mock_platform.categorize_type.return_value = "numeric"
        mock_platform.basic_stats_sql.return_value = "B"
        mock_platform.numeric_stats_sql.return_value = "N"
        mock_platform.skew_kurt_sql.return_value = None  # not supported
        mock_platform.fetch_one.side_effect = [
            (10,),                      # row count
            (0, 10),                    # basic_stats
            (1.0, 9.0, 5.0, 5.0, 2.0, 4.0, 3.0, 7.0),  # numeric_stats
        ]
        profile = DataProfiler(mock_platform).profile_table("DB", "SC", "T")
        col = profile.columns[0]
        assert col.mean == pytest.approx(5.0)
        assert col.skewness is None     # skew_kurt_sql returned None

    def test_skew_kurt_skipped_when_stddev_zero(self, mock_platform):
        mock_platform.get_columns.return_value = [("c", "NUMBER", None)]
        mock_platform.categorize_type.return_value = "numeric"
        mock_platform.basic_stats_sql.return_value = "B"
        mock_platform.numeric_stats_sql.return_value = "N"
        mock_platform.skew_kurt_sql.return_value = "SK"
        mock_platform.fetch_one.side_effect = [
            (10,),
            (0, 1),
            (5.0, 5.0, 5.0, 5.0, 0.0, 0.0, 5.0, 5.0),  # stddev=0
        ]
        profile = DataProfiler(mock_platform).profile_table("DB", "SC", "T")
        col = profile.columns[0]
        assert col.skewness is None  # not computed when stddev=0

    def test_skew_kurt_exception_silently_ignored(self, mock_platform):
        mock_platform.get_columns.return_value = [("c", "NUMBER", None)]
        mock_platform.categorize_type.return_value = "numeric"
        mock_platform.basic_stats_sql.return_value = "B"
        mock_platform.numeric_stats_sql.return_value = "N"
        mock_platform.skew_kurt_sql.return_value = "SK"
        mock_platform.fetch_one.side_effect = [
            (10,),
            (0, 10),
            (1.0, 9.0, 5.0, 5.0, 2.0, 4.0, 3.0, 7.0),
            RuntimeError("100051: Division by zero"),  # skew throws
        ]
        # Must not raise
        profile = DataProfiler(mock_platform).profile_table("DB", "SC", "T")
        col = profile.columns[0]
        assert col.skewness is None


# ── DataProfiler — real SQLite ────────────────────────────────────────────────

@pytest.fixture()
def sqlite_db(tmp_path):
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sales (
            id         INTEGER,
            revenue    REAL,
            product    TEXT,
            sale_date  TEXT,
            flag       INTEGER
        );
        INSERT INTO sales VALUES (1, 100.0, 'Widget', '2023-01-10', 1);
        INSERT INTO sales VALUES (2, 200.0, 'Gadget', '2023-06-15', 0);
        INSERT INTO sales VALUES (3, NULL,  'Widget', '2023-12-01', 1);
    """)
    conn.commit(); conn.close()
    return path


class TestDataProfilerSQLite:
    def test_full_profile_structure(self, sqlite_db):
        p = SQLitePlatform(sqlite_db)
        profile = DataProfiler(p).profile_table("", "", "sales")
        assert profile.table == "sales"
        assert profile.row_count == 3
        assert len(profile.columns) == 5

    def test_null_rate_computed(self, sqlite_db):
        p = SQLitePlatform(sqlite_db)
        profile = DataProfiler(p).profile_table("", "", "sales")
        rev = next(c for c in profile.columns if c.name == "revenue")
        assert rev.null_count == 1
        assert rev.null_rate == pytest.approx(1/3, rel=1e-3)

    def test_uniqueness_rate(self, sqlite_db):
        p = SQLitePlatform(sqlite_db)
        profile = DataProfiler(p).profile_table("", "", "sales")
        id_col = next(c for c in profile.columns if c.name == "id")
        assert id_col.uniqueness_rate == pytest.approx(1.0)

    def test_boolean_column_treated_as_numeric(self, sqlite_db):
        p = SQLitePlatform(sqlite_db)
        profile = DataProfiler(p).profile_table("", "", "sales")
        flag_col = next(c for c in profile.columns if c.name == "flag")
        # INTEGER → numeric path
        assert flag_col.min_val in (0.0, 1.0)
