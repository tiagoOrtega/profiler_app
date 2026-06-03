"""Abstract base class every platform connector must implement."""

from abc import ABC, abstractmethod
from typing import List, Optional


class BasePlatform(ABC):
    """Minimal interface shared by all database platform connectors."""

    dialect: str = "base"   # 'snowflake' | 'sqlite' | 'databricks'

    # ── Execution ─────────────────────────────────────────────────────────────

    @abstractmethod
    def fetch_one(self, sql: str, params=None):
        """Execute *sql* and return the first row as a tuple, or None."""

    @abstractmethod
    def fetch_all(self, sql: str, params=None) -> list:
        """Execute *sql* and return all rows as a list of tuples."""

    # ── Metadata ──────────────────────────────────────────────────────────────

    @abstractmethod
    def get_tables(self, database: str, schema: str) -> List[str]:
        """Return list of base-table names in database.schema."""

    @abstractmethod
    def get_columns(self, database: str, schema: str, table: str) -> list:
        """Return list of (col_name, data_type, comment_or_None) tuples."""

    def get_table_comment(self, database: str, schema: str, table: str) -> Optional[str]:
        """Return table-level description / comment string, or None."""
        return None

    def test_connection(self) -> dict:
        """Run a cheap connectivity probe. Return {"ok": True/False, ...}."""
        try:
            row = self.fetch_one("SELECT 1")
            return {"ok": bool(row), "info": self.dialect}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── SQL builders ──────────────────────────────────────────────────────────

    @abstractmethod
    def table_ref(self, database: str, schema: str, table: str) -> str:
        """Full quoted table reference for use in SQL FROM clauses."""

    @abstractmethod
    def categorize_type(self, data_type: str) -> Optional[str]:
        """Map a column's declared type to 'numeric', 'string', 'date', or None."""

    @abstractmethod
    def basic_stats_sql(self, table_ref: str, q_col: str) -> str:
        """SQL returning (null_count, distinct_count)."""

    @abstractmethod
    def numeric_stats_sql(self, table_ref: str, q_col: str) -> str:
        """SQL returning (min, max, avg, median, stddev, variance, q1, q3)."""

    def skew_kurt_sql(self, table_ref: str, q_col: str) -> Optional[str]:
        """SQL returning (skewness, kurtosis). Return None if unsupported."""
        return None

    @abstractmethod
    def string_stats_sql(self, table_ref: str, q_col: str) -> str:
        """SQL returning (min_length, max_length, avg_length)."""

    @abstractmethod
    def date_stats_sql(self, table_ref: str, q_col: str) -> str:
        """SQL returning (min_date_str, max_date_str)."""

    def quote_col(self, name: str) -> str:
        """Quote a column identifier. Default: double-quote (ANSI SQL)."""
        return f'"{name}"'

    def corr_sql(self, table_ref: str, col_names: List[str]) -> Optional[str]:
        """SQL returning one row with n² CORR() values (row-major).
        Return None if the platform does not support this natively."""
        return None
