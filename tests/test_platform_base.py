"""Tests for platforms/base.py — BasePlatform interface contract."""

import pytest

from platforms.base import BasePlatform


class TestAbstractness:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BasePlatform()

    def test_concrete_subclass_must_implement_all_abstract_methods(self):
        """A partially-implemented subclass should also raise."""
        class Partial(BasePlatform):
            dialect = "partial"
            def fetch_one(self, sql, params=None): pass
            # Missing all other abstract methods
        with pytest.raises(TypeError):
            Partial()


class _Concrete(BasePlatform):
    """Minimal concrete implementation for testing default methods."""
    dialect = "test"
    def fetch_one(self, sql, params=None): return (1,)
    def fetch_all(self, sql, params=None): return [(1,)]
    def get_tables(self, db, schema): return []
    def get_columns(self, db, schema, table): return []
    def table_ref(self, db, schema, table): return f'"{db}"."{schema}"."{table}"'
    def categorize_type(self, dt): return None
    def basic_stats_sql(self, tr, qc): return f"SELECT 0,0 FROM {tr}"
    def numeric_stats_sql(self, tr, qc): return f"SELECT NULL FROM {tr}"
    def string_stats_sql(self, tr, qc): return f"SELECT NULL FROM {tr}"
    def date_stats_sql(self, tr, qc): return f"SELECT NULL FROM {tr}"


class TestConcreteDefaults:
    def test_get_table_comment_returns_none(self):
        c = _Concrete()
        assert c.get_table_comment("db", "sc", "t") is None

    def test_skew_kurt_sql_returns_none(self):
        c = _Concrete()
        assert c.skew_kurt_sql("tbl", '"col"') is None

    def test_corr_sql_returns_none(self):
        c = _Concrete()
        assert c.corr_sql("tbl", ["a", "b"]) is None

    def test_quote_col_double_quote(self):
        c = _Concrete()
        assert c.quote_col("my_col") == '"my_col"'

    def test_test_connection_success(self):
        c = _Concrete()
        result = c.test_connection()
        assert result["ok"] is True

    def test_test_connection_failure(self):
        class Broken(_Concrete):
            def fetch_one(self, sql, params=None):
                raise RuntimeError("boom")
        result = Broken().test_connection()
        assert result["ok"] is False
        assert "boom" in result["error"]
