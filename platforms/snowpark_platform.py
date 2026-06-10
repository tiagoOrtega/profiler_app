"""Snowflake Snowpark platform — BasePlatform adapter for Streamlit in Snowflake.

Wraps a ``snowflake.snowpark.Session`` so all existing profiling, clustering and
relationship-detection logic runs unchanged inside SiS.
"""

from typing import List, Optional

from .base import BasePlatform


_NUMERIC = {
    "NUMBER", "DECIMAL", "NUMERIC", "INT", "INTEGER", "BIGINT",
    "SMALLINT", "TINYINT", "BYTEINT", "FLOAT", "FLOAT4", "FLOAT8",
    "DOUBLE", "DOUBLE PRECISION", "REAL",
}
_STRING = {
    "VARCHAR", "CHAR", "CHARACTER", "STRING", "TEXT",
    "NVARCHAR", "NCHAR", "NVARCHAR2", "CHAR VARYING",
}
_DATE = {
    "DATE", "TIME", "TIMESTAMP", "TIMESTAMP_LTZ",
    "TIMESTAMP_NTZ", "TIMESTAMP_TZ", "DATETIME",
}


class SnowparkPlatform(BasePlatform):
    """BasePlatform backed by a Snowpark Session (Streamlit in Snowflake)."""

    dialect = "snowflake"

    def __init__(self, session):
        """session — active ``snowflake.snowpark.Session``."""
        self._session = session

    # ── Execution ──────────────────────────────────────────────────────────────

    def fetch_one(self, sql: str, params=None):
        row = self._session.sql(sql).first()
        return tuple(row) if row else None

    def fetch_all(self, sql: str, params=None) -> list:
        return [tuple(r) for r in self._session.sql(sql).collect()]

    def test_connection(self) -> dict:
        try:
            row = self.fetch_one(
                "SELECT CURRENT_USER(), CURRENT_WAREHOUSE(), CURRENT_DATABASE()"
            )
            return {"ok": True, "user": row[0], "warehouse": row[1], "database": row[2]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── Metadata ───────────────────────────────────────────────────────────────

    def get_databases(self) -> List[str]:
        rows = self.fetch_all("SHOW DATABASES")
        return [r[1] for r in rows]

    def get_schemas(self, database: str) -> List[str]:
        rows = self.fetch_all(f"""
            SELECT SCHEMA_NAME
            FROM "{database}".INFORMATION_SCHEMA.SCHEMATA
            WHERE SCHEMA_NAME NOT IN ('INFORMATION_SCHEMA', 'PUBLIC')
            ORDER BY SCHEMA_NAME
        """)
        return [r[0] for r in rows]

    def get_tables(self, database: str, schema: str) -> List[str]:
        rows = self.fetch_all(f"""
            SELECT TABLE_NAME
            FROM "{database}".INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = '{schema.upper()}'
              AND TABLE_TYPE   = 'BASE TABLE'
            ORDER BY TABLE_NAME
        """)
        return [r[0] for r in rows]

    def get_columns(self, database: str, schema: str, table: str) -> list:
        return self.fetch_all(f"""
            SELECT COLUMN_NAME, DATA_TYPE, COMMENT
            FROM "{database}".INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = '{schema.upper()}'
              AND TABLE_NAME   = '{table.upper()}'
            ORDER BY ORDINAL_POSITION
        """)

    def get_table_comment(self, database: str, schema: str, table: str) -> Optional[str]:
        row = self.fetch_one(f"""
            SELECT COMMENT
            FROM "{database}".INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = '{schema.upper()}'
              AND TABLE_NAME   = '{table.upper()}'
        """)
        return (row[0] or None) if row else None

    # ── SQL builders ───────────────────────────────────────────────────────────

    def table_ref(self, database: str, schema: str, table: str) -> str:
        return f'"{database}"."{schema}"."{table}"'

    def categorize_type(self, data_type: str) -> Optional[str]:
        bt = data_type.upper().split("(")[0].strip()
        if bt in _NUMERIC: return "numeric"
        if bt in _STRING:  return "string"
        if bt in _DATE:    return "date"
        return None

    def basic_stats_sql(self, table_ref: str, q_col: str) -> str:
        return f"""
            SELECT COUNT(*) - COUNT({q_col}), COUNT(DISTINCT {q_col})
            FROM {table_ref}
        """

    def numeric_stats_sql(self, table_ref: str, q_col: str) -> str:
        return f"""
            SELECT
                MIN({q_col})::FLOAT, MAX({q_col})::FLOAT,
                AVG({q_col}::FLOAT), MEDIAN({q_col}::FLOAT),
                STDDEV({q_col}::FLOAT), VARIANCE({q_col}::FLOAT),
                PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY {q_col}::FLOAT),
                PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {q_col}::FLOAT)
            FROM {table_ref}
            WHERE {q_col} IS NOT NULL
        """

    def skew_kurt_sql(self, table_ref: str, q_col: str) -> str:
        return f"""
            SELECT SKEW({q_col}::FLOAT), KURTOSIS({q_col}::FLOAT)
            FROM {table_ref}
            WHERE {q_col} IS NOT NULL
        """

    def string_stats_sql(self, table_ref: str, q_col: str) -> str:
        return f"""
            SELECT MIN(LENGTH({q_col})), MAX(LENGTH({q_col})), AVG(LENGTH({q_col}))
            FROM {table_ref}
            WHERE {q_col} IS NOT NULL
        """

    def date_stats_sql(self, table_ref: str, q_col: str) -> str:
        return f"""
            SELECT MIN({q_col})::VARCHAR, MAX({q_col})::VARCHAR
            FROM {table_ref}
            WHERE {q_col} IS NOT NULL
        """

    def corr_sql(self, table_ref: str, col_names: List[str]) -> Optional[str]:
        n = len(col_names)
        exprs = []
        for i in range(n):
            for j in range(n):
                if i == j:
                    exprs.append("1.0")
                else:
                    qi = f'"{col_names[i]}"'
                    qj = f'"{col_names[j]}"'
                    exprs.append(f"CORR({qi}::FLOAT, {qj}::FLOAT)")
        return f"SELECT {', '.join(exprs)} FROM {table_ref}"
