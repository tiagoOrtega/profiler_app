"""SQLite platform connector — local file database, no server required."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

from .base import BasePlatform


_NUMERIC = {
    "INTEGER","INT","TINYINT","SMALLINT","MEDIUMINT","BIGINT",
    "INT2","INT8","REAL","DOUBLE","DOUBLE PRECISION","FLOAT",
    "NUMERIC","DECIMAL","NUMBER",
}
_STRING = {
    "TEXT","CHARACTER","VARCHAR","VARYING CHARACTER",
    "NCHAR","NATIVE CHARACTER","NVARCHAR","CLOB","BLOB",
}
_DATE = {"DATE","DATETIME","TIMESTAMP","TIME"}


class SQLitePlatform(BasePlatform):
    dialect = "sqlite"

    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path).expanduser().resolve())

    @contextmanager
    def _cur(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()
            conn.close()

    # ── Execution ──────────────────────────────────────────────────────────────

    def fetch_one(self, sql: str, params=None):
        with self._cur() as cur:
            cur.execute(sql, params or [])
            row = cur.fetchone()
            return tuple(row) if row else None

    def fetch_all(self, sql: str, params=None) -> list:
        with self._cur() as cur:
            cur.execute(sql, params or [])
            return [tuple(r) for r in cur.fetchall()]

    def test_connection(self) -> dict:
        try:
            p = Path(self.db_path)
            if not p.exists():
                return {"ok": False, "error": f"Database file not found: {self.db_path}"}
            row = self.fetch_one("SELECT sqlite_version()")
            return {"ok": True, "user": "local", "warehouse": "sqlite",
                    "database": p.name, "version": row[0] if row else "?"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ── Metadata ───────────────────────────────────────────────────────────────

    # SQLite has no concept of database/schema — treat the file as the db.
    def get_tables(self, database: str, schema: str) -> List[str]:
        rows = self.fetch_all(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [r[0] for r in rows]

    def get_columns(self, database: str, schema: str, table: str) -> list:
        rows = self.fetch_all(f'PRAGMA table_info("{table}")')
        # (cid, name, type, notnull, dflt_value, pk)
        return [(r[1], r[2] or "TEXT", None) for r in rows]

    def get_schemas(self, database: str) -> List[str]:
        # SQLite has no schemas; return a synthetic one
        return ["main"]

    # ── SQL builders ───────────────────────────────────────────────────────────

    def table_ref(self, database: str, schema: str, table: str) -> str:
        return f'"{table}"'   # SQLite: just the table name

    def categorize_type(self, data_type: str) -> Optional[str]:
        bt = data_type.upper().split("(")[0].strip()
        if bt in _NUMERIC: return "numeric"
        if bt in _DATE:    return "date"
        if bt in _STRING:  return "string"
        # SQLite affinity rules
        if "INT" in bt:                               return "numeric"
        if any(k in bt for k in ("CHAR","CLOB","TEXT")): return "string"
        if any(k in bt for k in ("REAL","FLOA","DOUB")): return "numeric"
        if any(k in bt for k in ("DATE","TIME")):     return "date"
        if any(k in bt for k in ("NUM","DEC")):       return "numeric"
        return "string"   # SQLite default affinity

    def basic_stats_sql(self, table_ref: str, q_col: str) -> str:
        return f"""
            SELECT
                SUM(CASE WHEN {q_col} IS NULL THEN 1 ELSE 0 END),
                COUNT(DISTINCT {q_col})
            FROM {table_ref}
        """

    def numeric_stats_sql(self, table_ref: str, q_col: str) -> str:
        # SQLite lacks MEDIAN, PERCENTILE_CONT, STDDEV.
        # Approximate population stddev: SQRT(E[x²] - E[x]²)
        return f"""
            SELECT
                MIN(CAST({q_col} AS REAL)),
                MAX(CAST({q_col} AS REAL)),
                AVG(CAST({q_col} AS REAL)),
                NULL,
                SQRT(MAX(0.0,
                    AVG(CAST({q_col} AS REAL) * CAST({q_col} AS REAL)) -
                    AVG(CAST({q_col} AS REAL)) * AVG(CAST({q_col} AS REAL))
                )),
                MAX(0.0,
                    AVG(CAST({q_col} AS REAL) * CAST({q_col} AS REAL)) -
                    AVG(CAST({q_col} AS REAL)) * AVG(CAST({q_col} AS REAL))
                ),
                NULL,
                NULL
            FROM {table_ref}
            WHERE {q_col} IS NOT NULL
        """

    # skew_kurt_sql → returns None (unsupported)

    def string_stats_sql(self, table_ref: str, q_col: str) -> str:
        return f"""
            SELECT
                MIN(LENGTH(CAST({q_col} AS TEXT))),
                MAX(LENGTH(CAST({q_col} AS TEXT))),
                AVG(LENGTH(CAST({q_col} AS TEXT)))
            FROM {table_ref}
            WHERE {q_col} IS NOT NULL
        """

    def date_stats_sql(self, table_ref: str, q_col: str) -> str:
        return f"""
            SELECT MIN(CAST({q_col} AS TEXT)), MAX(CAST({q_col} AS TEXT))
            FROM {table_ref}
            WHERE {q_col} IS NOT NULL
        """

    def corr_sql(self, table_ref: str, col_names: List[str]) -> Optional[str]:
        # SQLite has no CORR(); the caller falls back to Python computation.
        return None
