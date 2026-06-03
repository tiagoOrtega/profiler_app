"""Tests for platforms/snowflake_platform.py — SQL builders and metadata (mocked)."""

from unittest.mock import MagicMock, patch, call

import pytest

from platforms.snowflake_platform import SnowflakePlatform


@pytest.fixture()
def mock_sf_conn():
    """Return a SnowflakePlatform whose internal SnowflakeConnection is mocked."""
    cfg = MagicMock()
    cfg.account = "acc"; cfg.user = "u"; cfg.password = "p"
    cfg.warehouse = "wh"; cfg.database = "db"; cfg.schema = "sc"; cfg.role = None
    with patch("platforms.snowflake_platform.SnowflakeConnection") as mock_cls:
        mock_inst = MagicMock()
        mock_cls.return_value = mock_inst
        platform = SnowflakePlatform(cfg)
        platform._sf = mock_inst
        yield platform, mock_inst


# ── table_ref ─────────────────────────────────────────────────────────────────

class TestTableRef:
    def test_double_quotes(self, mock_sf_conn):
        p, _ = mock_sf_conn
        assert p.table_ref("DB", "SC", "T") == '"DB"."SC"."T"'

    def test_preserves_case(self, mock_sf_conn):
        p, _ = mock_sf_conn
        assert p.table_ref("MyDb", "MySchema", "MyTable") == '"MyDb"."MySchema"."MyTable"'


# ── categorize_type ───────────────────────────────────────────────────────────

class TestCategorizeType:
    @pytest.fixture(autouse=True)
    def platform(self, mock_sf_conn):
        self.p, _ = mock_sf_conn

    @pytest.mark.parametrize("dtype,expected", [
        ("NUMBER", "numeric"),
        ("DECIMAL(18,2)", "numeric"),
        ("INT", "numeric"),
        ("BIGINT", "numeric"),
        ("FLOAT", "numeric"),
        ("DOUBLE PRECISION", "numeric"),
        ("VARCHAR", "string"),
        ("VARCHAR(255)", "string"),
        ("CHAR", "string"),
        ("TEXT", "string"),
        ("NVARCHAR", "string"),
        ("DATE", "date"),
        ("TIMESTAMP", "date"),
        ("TIMESTAMP_LTZ", "date"),
        ("TIMESTAMP_NTZ", "date"),
        ("DATETIME", "date"),
        ("VARIANT", None),
        ("ARRAY", None),
        ("OBJECT", None),
    ])
    def test_type_mapping(self, dtype, expected):
        assert self.p.categorize_type(dtype) == expected


# ── SQL builders (format / content) ──────────────────────────────────────────

class TestSQLBuilders:
    @pytest.fixture(autouse=True)
    def platform(self, mock_sf_conn):
        self.p, _ = mock_sf_conn

    def test_basic_stats_contains_null_count_and_distinct(self):
        sql = self.p.basic_stats_sql('"DB"."SC"."T"', '"col"')
        assert "COUNT(*) - COUNT" in sql
        assert "COUNT(DISTINCT" in sql

    def test_numeric_stats_contains_all_aggregates(self):
        sql = self.p.numeric_stats_sql('"DB"."SC"."T"', '"col"')
        for fn in ("MIN", "MAX", "AVG", "MEDIAN", "STDDEV", "VARIANCE",
                   "PERCENTILE_CONT(0.25)", "PERCENTILE_CONT(0.75)"):
            assert fn in sql

    def test_numeric_stats_casts_to_float(self):
        sql = self.p.numeric_stats_sql('"DB"."SC"."T"', '"col"')
        assert "::FLOAT" in sql or "::FLOAT" in sql

    def test_skew_kurt_sql_contains_skew_and_kurtosis(self):
        sql = self.p.skew_kurt_sql('"DB"."SC"."T"', '"col"')
        assert "SKEW" in sql
        assert "KURTOSIS" in sql

    def test_string_stats_contains_length(self):
        sql = self.p.string_stats_sql('"DB"."SC"."T"', '"col"')
        assert sql.count("LENGTH") == 3

    def test_date_stats_casts_to_varchar(self):
        sql = self.p.date_stats_sql('"DB"."SC"."T"', '"col"')
        assert "VARCHAR" in sql or "::VARCHAR" in sql

    def test_corr_sql_pair_count(self):
        sql = self.p.corr_sql('"DB"."SC"."T"', ["a", "b", "c"])
        # 3×3 = 9 expressions
        assert sql.count("CORR") == 6   # 3 diagonals use "1.0" not CORR

    def test_corr_sql_diagonal_is_one(self):
        sql = self.p.corr_sql('"DB"."SC"."T"', ["x", "y"])
        assert "1.0" in sql

    def test_quote_col_double_quotes(self):
        assert self.p.quote_col("my_col") == '"my_col"'


# ── Delegation to SnowflakeConnection ─────────────────────────────────────────

class TestDelegation:
    def test_fetch_one_delegates(self, mock_sf_conn):
        p, mock_conn = mock_sf_conn
        mock_conn.fetch_one.return_value = (42,)
        result = p.fetch_one("SELECT 42")
        mock_conn.fetch_one.assert_called_once_with("SELECT 42", None)
        assert result == (42,)

    def test_fetch_all_delegates(self, mock_sf_conn):
        p, mock_conn = mock_sf_conn
        mock_conn.fetch_all.return_value = [(1,), (2,)]
        result = p.fetch_all("SELECT id FROM t")
        mock_conn.fetch_all.assert_called_once()
        assert result == [(1,), (2,)]


# ── test_connection ───────────────────────────────────────────────────────────

class TestTestConnection:
    def test_success(self, mock_sf_conn):
        p, mock_conn = mock_sf_conn
        mock_conn.fetch_one.return_value = ("USER1", "WH1", "DB1")
        result = p.test_connection()
        assert result["ok"] is True
        assert result["user"] == "USER1"

    def test_failure(self, mock_sf_conn):
        p, mock_conn = mock_sf_conn
        mock_conn.fetch_one.side_effect = Exception("auth failed")
        result = p.test_connection()
        assert result["ok"] is False
        assert "auth failed" in result["error"]


# ── get_tables / get_columns / get_table_comment ──────────────────────────────

class TestMetadata:
    def test_get_tables_upper_schema(self, mock_sf_conn):
        p, mock_conn = mock_sf_conn
        mock_conn.fetch_all.return_value = [("ORDERS",), ("CUSTOMERS",)]
        tables = p.get_tables("DB", "public")
        sql = mock_conn.fetch_all.call_args[0][0]
        assert "PUBLIC" in sql     # schema uppercased

    def test_get_table_comment_returns_none_when_null(self, mock_sf_conn):
        p, mock_conn = mock_sf_conn
        mock_conn.fetch_one.return_value = (None,)
        result = p.get_table_comment("DB", "SC", "T")
        assert result is None

    def test_get_table_comment_returns_string(self, mock_sf_conn):
        p, mock_conn = mock_sf_conn
        mock_conn.fetch_one.return_value = ("A great table",)
        assert p.get_table_comment("DB", "SC", "T") == "A great table"

    def test_get_schemas_calls_information_schema(self, mock_sf_conn):
        p, mock_conn = mock_sf_conn
        mock_conn.fetch_all.return_value = [("PUBLIC",), ("RETAIL",)]
        schemas = p.get_schemas("DB")
        sql = mock_conn.fetch_all.call_args[0][0]
        assert "SCHEMATA" in sql
