"""Tests for platforms/sqlite_platform.py — uses a real in-memory SQLite DB."""

import os
import sqlite3
import tempfile

import pytest

from platforms.sqlite_platform import SQLitePlatform


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE orders (
            order_id   INTEGER,
            amount     REAL,
            status     TEXT,
            created_at TEXT
        );
        INSERT INTO orders VALUES (1, 99.99, 'COMPLETE', '2023-01-01');
        INSERT INTO orders VALUES (2, 50.00, 'PENDING',  '2023-02-01');
        INSERT INTO orders VALUES (3, NULL,  'FAILED',   '2023-03-01');
    """)
    conn.commit(); conn.close()
    return path


@pytest.fixture()
def platform(db_path):
    return SQLitePlatform(db_path)


# ── Connection ────────────────────────────────────────────────────────────────

class TestTestConnection:
    def test_success(self, platform):
        result = platform.test_connection()
        assert result["ok"] is True
        assert "sqlite" in result["warehouse"]

    def test_failure_missing_file(self, tmp_path):
        p = SQLitePlatform(str(tmp_path / "missing.db"))
        result = p.test_connection()
        assert result["ok"] is False

    def test_expanduser_path(self):
        # Ensure relative/home paths are resolved
        p = SQLitePlatform("./sample.db")
        assert "sample.db" in p.db_path


# ── Metadata ──────────────────────────────────────────────────────────────────

class TestGetTables:
    def test_returns_user_tables(self, platform):
        tables = platform.get_tables("", "")
        assert "orders" in tables

    def test_excludes_sqlite_internals(self, platform):
        tables = platform.get_tables("", "")
        assert all(not t.startswith("sqlite_") for t in tables)

    def test_empty_database(self, tmp_path):
        empty = str(tmp_path / "empty.db")
        sqlite3.connect(empty).close()
        p = SQLitePlatform(empty)
        assert p.get_tables("", "") == []


class TestGetColumns:
    def test_returns_all_columns(self, platform):
        cols = platform.get_columns("", "", "orders")
        names = [c[0] for c in cols]
        assert set(names) == {"order_id", "amount", "status", "created_at"}

    def test_returns_three_tuple(self, platform):
        cols = platform.get_columns("", "", "orders")
        assert all(len(c) == 3 for c in cols)

    def test_comment_is_none(self, platform):
        cols = platform.get_columns("", "", "orders")
        assert all(c[2] is None for c in cols)

    def test_unknown_type_defaults_to_text(self, tmp_path):
        path = str(tmp_path / "t.db")
        conn = sqlite3.connect(path); conn.execute("CREATE TABLE t (x BLOB)"); conn.commit(); conn.close()
        p = SQLitePlatform(path)
        cols = p.get_columns("", "", "t")
        assert cols[0][1] == "BLOB"


class TestGetSchemas:
    def test_returns_main(self, platform):
        assert platform.get_schemas("") == ["main"]


class TestTableRef:
    def test_ignores_database_and_schema(self, platform):
        assert platform.table_ref("any_db", "any_schema", "my_table") == '"my_table"'


# ── Type categorization ───────────────────────────────────────────────────────

class TestCategorizeType:
    @pytest.mark.parametrize("dtype,expected", [
        ("INTEGER", "numeric"),
        ("INT", "numeric"),
        ("BIGINT", "numeric"),
        ("REAL", "numeric"),
        ("DOUBLE", "numeric"),
        ("FLOAT", "numeric"),
        ("NUMERIC", "numeric"),
        ("DECIMAL(10,2)", "numeric"),
        ("TEXT", "string"),
        ("VARCHAR(100)", "string"),
        ("CHAR", "string"),
        ("NVARCHAR", "string"),
        ("CLOB", "string"),
        ("DATE", "date"),
        ("DATETIME", "date"),
        ("TIMESTAMP", "date"),
        ("UNKNOWN_TYPE", "string"),   # fallback
    ])
    def test_type_mapping(self, platform, dtype, expected):
        assert platform.categorize_type(dtype) == expected

    def test_affinity_int_suffix(self, platform):
        assert platform.categorize_type("SMALLINT") == "numeric"

    def test_affinity_real_prefix(self, platform):
        assert platform.categorize_type("REAL") == "numeric"


# ── SQL builders ─────────────────────────────────────────────────────────────

class TestSQLBuilders:
    def test_basic_stats_sql_runnable(self, platform, db_path):
        sql = platform.basic_stats_sql('"orders"', '"amount"')
        row = platform.fetch_one(sql)
        null_count, distinct = row
        assert null_count == 1      # one NULL
        assert distinct == 2        # 99.99 and 50.0

    def test_numeric_stats_sql_runnable(self, platform):
        sql = platform.numeric_stats_sql('"orders"', '"amount"')
        row = platform.fetch_one(sql)
        assert row is not None
        mn, mx = row[0], row[1]
        assert mn == pytest.approx(50.0)
        assert mx == pytest.approx(99.99)

    def test_string_stats_sql_runnable(self, platform):
        sql = platform.string_stats_sql('"orders"', '"status"')
        row = platform.fetch_one(sql)
        assert row is not None
        min_len, max_len, avg_len = row
        assert min_len == 6    # FAILED
        assert max_len == 8    # COMPLETE

    def test_date_stats_sql_runnable(self, platform):
        sql = platform.date_stats_sql('"orders"', '"created_at"')
        row = platform.fetch_one(sql)
        assert row[0] == "2023-01-01"
        assert row[1] == "2023-03-01"

    def test_corr_sql_returns_none(self, platform):
        assert platform.corr_sql('"orders"', ["amount", "order_id"]) is None

    def test_skew_kurt_sql_returns_none(self, platform):
        assert platform.skew_kurt_sql('"orders"', '"amount"') is None

    def test_quote_col_double_quotes(self, platform):
        assert platform.quote_col("my_col") == '"my_col"'


# ── fetch_one / fetch_all ─────────────────────────────────────────────────────

class TestFetchMethods:
    def test_fetch_one_returns_tuple(self, platform):
        row = platform.fetch_one("SELECT 1")
        assert row == (1,)

    def test_fetch_one_returns_none_for_empty(self, platform):
        row = platform.fetch_one("SELECT 1 WHERE 1=0")
        assert row is None

    def test_fetch_all_returns_list_of_tuples(self, platform):
        rows = platform.fetch_all("SELECT order_id FROM orders ORDER BY order_id")
        assert rows == [(1,), (2,), (3,)]

    def test_fetch_all_empty(self, platform):
        rows = platform.fetch_all("SELECT 1 WHERE 1=0")
        assert rows == []


# ── Integration: DataProfiler with SQLite ─────────────────────────────────────

class TestDataProfilerIntegration:
    def test_profile_table_all_columns(self, platform):
        from profiling import DataProfiler
        profiler = DataProfiler(platform)
        profile = profiler.profile_table("", "", "orders")
        assert profile.row_count == 3
        assert profile.column_count == 4
        assert profile.platform == "sqlite"

    def test_profile_numeric_column(self, platform):
        from profiling import DataProfiler
        profile = DataProfiler(platform).profile_table("", "", "orders")
        amount_col = next(c for c in profile.columns if c.name == "amount")
        assert amount_col.null_count == 1
        assert amount_col.null_rate == pytest.approx(1/3, rel=1e-3)
        assert amount_col.min_val == pytest.approx(50.0)
        assert amount_col.max_val == pytest.approx(99.99)
        assert amount_col.mean == pytest.approx(74.995)

    def test_profile_string_column(self, platform):
        from profiling import DataProfiler
        profile = DataProfiler(platform).profile_table("", "", "orders")
        status_col = next(c for c in profile.columns if c.name == "status")
        assert status_col.min_length == 6
        assert status_col.max_length == 8

    def test_profile_date_column(self, tmp_path):
        """A column declared DATE is profiled on the date path."""
        path = str(tmp_path / "dated.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE events (id INTEGER, event_date DATE)")
        conn.executemany("INSERT INTO events VALUES (?, ?)",
                         [(1, "2023-01-01"), (2, "2023-06-15"), (3, "2024-12-31")])
        conn.commit(); conn.close()
        p = SQLitePlatform(path)
        from profiling import DataProfiler
        profile = DataProfiler(p).profile_table("", "", "events")
        dt_col = next(c for c in profile.columns if c.name == "event_date")
        assert dt_col.min_date == "2023-01-01"
        assert dt_col.max_date == "2024-12-31"

    def test_zero_row_table(self, tmp_path):
        path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE empty (id INTEGER, val REAL)")
        conn.commit(); conn.close()
        p = SQLitePlatform(path)
        from profiling import DataProfiler
        profile = DataProfiler(p).profile_table("", "", "empty")
        assert profile.row_count == 0
        for col in profile.columns:
            assert col.mean is None

    def test_column_error_does_not_abort(self, tmp_path):
        """A bad column must not stop the other columns from profiling."""
        path = str(tmp_path / "bad.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE t (ok INTEGER, bad BLOB)")
        conn.execute("INSERT INTO t VALUES (1, X'0102')")
        conn.commit(); conn.close()
        p = SQLitePlatform(path)
        from profiling import DataProfiler
        # Should not raise; bad column gets error field
        profile = DataProfiler(p).profile_table("", "", "t")
        assert profile.column_count == 2
