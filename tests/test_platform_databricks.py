"""Tests for platforms/databricks_platform.py — mocked databricks-sql-connector."""

from unittest.mock import MagicMock, patch, call

import pytest

from platforms.databricks_platform import DatabricksPlatform


@pytest.fixture()
def platform():
    cfg = MagicMock()
    cfg.host = "host.azuredatabricks.net"
    cfg.http_path = "/sql/1.0/warehouses/abc"
    cfg.token = "dapi123"
    cfg.catalog = "main"
    cfg.schema = "retail"
    return DatabricksPlatform(cfg)


@pytest.fixture()
def mock_conn_ctx(platform):
    """Context manager that patches the Databricks SQL connect call."""
    mock_cursor = MagicMock()
    mock_conn_inst = MagicMock()
    mock_conn_inst.cursor.return_value.__enter__ = lambda s: mock_cursor
    mock_conn_inst.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_conn_inst.__enter__ = lambda s: mock_conn_inst
    mock_conn_inst.__exit__ = MagicMock(return_value=False)
    with patch("platforms.databricks_platform.DatabricksPlatform._new_conn",
               return_value=mock_conn_inst):
        yield platform, mock_cursor


# ── table_ref ─────────────────────────────────────────────────────────────────

class TestTableRef:
    def test_backtick_quoting(self, platform):
        assert platform.table_ref("main", "retail", "orders") == "`main`.`retail`.`orders`"

    def test_preserves_case(self, platform):
        assert platform.table_ref("Main", "Retail", "Orders") == "`Main`.`Retail`.`Orders`"


# ── quote_col ─────────────────────────────────────────────────────────────────

class TestQuoteCol:
    def test_backtick(self, platform):
        assert platform.quote_col("my_col") == "`my_col`"


# ── categorize_type ───────────────────────────────────────────────────────────

class TestCategorizeType:
    @pytest.mark.parametrize("dtype,expected", [
        ("INT", "numeric"),
        ("INTEGER", "numeric"),
        ("BIGINT", "numeric"),
        ("FLOAT", "numeric"),
        ("DOUBLE", "numeric"),
        ("DECIMAL(10,2)", "numeric"),
        ("STRING", "string"),
        ("VARCHAR", "string"),
        ("CHAR", "string"),
        ("BINARY", "string"),
        ("DATE", "date"),
        ("TIMESTAMP", "date"),
        ("TIMESTAMP_NTZ", "date"),
        ("ARRAY<STRING>", "string"),  # fallback
        ("MAP<STRING,INT>", "string"),  # fallback
    ])
    def test_type_mapping(self, platform, dtype, expected):
        assert platform.categorize_type(dtype) == expected

    def test_int_affinity(self, platform):
        assert platform.categorize_type("BIGINT") == "numeric"

    def test_float_affinity(self, platform):
        assert platform.categorize_type("FLOAT") == "numeric"


# ── SQL builders ──────────────────────────────────────────────────────────────

class TestSQLBuilders:
    def test_basic_stats_sql(self, platform):
        sql = platform.basic_stats_sql("`main`.`retail`.`t`", "`col`")
        assert "COUNT(*) - COUNT" in sql
        assert "COUNT(DISTINCT" in sql

    def test_numeric_stats_has_percentile(self, platform):
        sql = platform.numeric_stats_sql("`t`", "`col`")
        assert "PERCENTILE" in sql
        assert "STDDEV" in sql
        assert "VARIANCE" in sql

    def test_skew_kurt_sql_present(self, platform):
        sql = platform.skew_kurt_sql("`t`", "`col`")
        assert sql is not None
        assert "SKEWNESS" in sql
        assert "KURTOSIS" in sql

    def test_string_stats_uses_length(self, platform):
        sql = platform.string_stats_sql("`t`", "`col`")
        assert "LENGTH" in sql

    def test_date_stats_casts_to_string(self, platform):
        sql = platform.date_stats_sql("`t`", "`col`")
        assert "STRING" in sql

    def test_corr_sql_uses_backtick_columns(self, platform):
        sql = platform.corr_sql("`main`.`retail`.`t`", ["a", "b"])
        assert "`a`" in sql
        assert "`b`" in sql
        assert "CORR" in sql

    def test_corr_sql_pair_count_3x3(self, platform):
        sql = platform.corr_sql("`t`", ["x", "y", "z"])
        # 6 off-diagonal CORR calls
        assert sql.count("CORR") == 6


# ── test_connection ───────────────────────────────────────────────────────────

class TestTestConnection:
    def test_success(self, mock_conn_ctx):
        p, mock_cursor = mock_conn_ctx
        mock_cursor.fetchone.return_value = ("user@domain.com", "main", "retail")
        result = p.test_connection()
        assert result["ok"] is True
        assert "user@domain.com" in result["user"]

    def test_failure_import_error(self, platform):
        with patch.object(platform, "_new_conn", side_effect=ImportError("no module")):
            result = platform.test_connection()
        assert result["ok"] is False
        assert "no module" in result["error"]


# ── get_columns: skips partition headers ─────────────────────────────────────

class TestGetColumns:
    def test_skips_partition_header_rows(self, mock_conn_ctx):
        p, mock_cursor = mock_conn_ctx
        mock_cursor.fetchall.return_value = [
            ("id", "INT", "primary key"),
            ("name", "STRING", None),
            ("# Partition Information", "", ""),
            ("# col_name", "data_type", "comment"),
        ]
        cols = p.get_columns("main", "retail", "t")
        names = [c[0] for c in cols]
        assert "id" in names and "name" in names
        assert "# Partition Information" not in names

    def test_comment_preserved(self, mock_conn_ctx):
        p, mock_cursor = mock_conn_ctx
        mock_cursor.fetchall.return_value = [("amount", "DECIMAL", "sale amount")]
        cols = p.get_columns("main", "retail", "t")
        assert cols[0][2] == "sale amount"

    def test_none_comment_normalised(self, mock_conn_ctx):
        p, mock_cursor = mock_conn_ctx
        mock_cursor.fetchall.return_value = [("id", "INT", None)]
        cols = p.get_columns("main", "retail", "t")
        assert cols[0][2] is None
