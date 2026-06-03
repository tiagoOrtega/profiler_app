"""Databricks SQL platform connector — requires databricks-sql-connector."""

from typing import List, Optional

from .base import BasePlatform


_NUMERIC = {
    "INT","INTEGER","BIGINT","SMALLINT","TINYINT","BYTE","SHORT","LONG",
    "FLOAT","DOUBLE","DECIMAL","NUMERIC","REAL",
}
_STRING = {
    "STRING","VARCHAR","CHAR","NCHAR","NVARCHAR","TEXT","BINARY","BYTES",
}
_DATE = {
    "DATE","TIMESTAMP","TIMESTAMP_NTZ","TIMESTAMP_LTZ","INTERVAL",
}


class DatabricksPlatform(BasePlatform):
    dialect = "databricks"

    def __init__(self, config):
        self.host      = config.host
        self.http_path = config.http_path
        self.token     = config.token
        self.catalog   = config.catalog or "main"
        self.schema    = config.schema  or "default"

    def _new_conn(self):
        from databricks import sql as dbsql          # lazy import
        return dbsql.connect(
            server_hostname=self.host,
            http_path=self.http_path,
            access_token=self.token,
        )

    # ── Execution ──────────────────────────────────────────────────────────────

    def fetch_one(self, sql: str, params=None):
        with self._new_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                row = cur.fetchone()
                return tuple(row) if row else None

    def fetch_all(self, sql: str, params=None) -> list:
        with self._new_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall() or []
                return [tuple(r) for r in rows]

    def test_connection(self) -> dict:
        try:
            row = self.fetch_one(
                "SELECT current_user(), current_catalog(), current_database()"
            )
            return {"ok": True, "user": row[0], "warehouse": "SQL Warehouse",
                    "database": f"{row[1]}.{row[2]}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── Metadata ───────────────────────────────────────────────────────────────

    def get_tables(self, catalog: str, schema: str) -> List[str]:
        rows = self.fetch_all(f"SHOW TABLES IN `{catalog}`.`{schema}`")
        # (database, tableName, isTemporary)
        return [r[1] for r in rows if not r[2]]

    def get_columns(self, catalog: str, schema: str, table: str) -> list:
        rows = self.fetch_all(f"DESCRIBE TABLE `{catalog}`.`{schema}`.`{table}`")
        # (col_name, data_type, comment)
        return [
            (r[0], r[1] or "STRING", r[2] or None)
            for r in rows
            if r[0] and not r[0].startswith("#")
        ]

    def get_table_comment(self, catalog: str, schema: str, table: str) -> Optional[str]:
        try:
            rows = self.fetch_all(
                f"DESCRIBE TABLE EXTENDED `{catalog}`.`{schema}`.`{table}`"
            )
            for r in rows:
                if r[0] == "Comment" and r[1]:
                    return r[1]
        except Exception:
            pass
        return None

    def get_schemas(self, catalog: str) -> List[str]:
        rows = self.fetch_all(f"SHOW SCHEMAS IN `{catalog}`")
        return [r[0] for r in rows]

    # ── SQL builders ───────────────────────────────────────────────────────────

    def table_ref(self, catalog: str, schema: str, table: str) -> str:
        return f"`{catalog}`.`{schema}`.`{table}`"

    def categorize_type(self, data_type: str) -> Optional[str]:
        bt = data_type.upper().split("(")[0].split("<")[0].strip()
        if bt in _NUMERIC: return "numeric"
        if bt in _DATE:    return "date"
        if bt in _STRING:  return "string"
        if "INT" in bt or "FLOAT" in bt or "DOUBLE" in bt: return "numeric"
        if "DATE" in bt or "TIME" in bt:                   return "date"
        return "string"

    def quote_col(self, name: str) -> str:
        return f"`{name}`"

    def basic_stats_sql(self, table_ref: str, q_col: str) -> str:
        return f"""
            SELECT COUNT(*) - COUNT({q_col}), COUNT(DISTINCT {q_col})
            FROM {table_ref}
        """

    def numeric_stats_sql(self, table_ref: str, q_col: str) -> str:
        return f"""
            SELECT
                MIN(CAST({q_col} AS DOUBLE)),
                MAX(CAST({q_col} AS DOUBLE)),
                AVG(CAST({q_col} AS DOUBLE)),
                PERCENTILE({q_col}, 0.5),
                STDDEV(CAST({q_col} AS DOUBLE)),
                VARIANCE(CAST({q_col} AS DOUBLE)),
                PERCENTILE({q_col}, 0.25),
                PERCENTILE({q_col}, 0.75)
            FROM {table_ref}
            WHERE {q_col} IS NOT NULL
        """

    def skew_kurt_sql(self, table_ref: str, q_col: str) -> Optional[str]:
        return f"""
            SELECT
                SKEWNESS(CAST({q_col} AS DOUBLE)),
                KURTOSIS(CAST({q_col} AS DOUBLE))
            FROM {table_ref}
            WHERE {q_col} IS NOT NULL
        """

    def string_stats_sql(self, table_ref: str, q_col: str) -> str:
        return f"""
            SELECT
                MIN(LENGTH(CAST({q_col} AS STRING))),
                MAX(LENGTH(CAST({q_col} AS STRING))),
                AVG(LENGTH(CAST({q_col} AS STRING)))
            FROM {table_ref}
            WHERE {q_col} IS NOT NULL
        """

    def date_stats_sql(self, table_ref: str, q_col: str) -> str:
        return f"""
            SELECT
                CAST(MIN({q_col}) AS STRING),
                CAST(MAX({q_col}) AS STRING)
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
                    qi = f"`{col_names[i]}`"
                    qj = f"`{col_names[j]}`"
                    exprs.append(f"CORR(CAST({qi} AS DOUBLE), CAST({qj} AS DOUBLE))")
        return f"SELECT {', '.join(exprs)} FROM {table_ref}"
