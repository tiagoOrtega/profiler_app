"""
DataLens — Snowflake-native persistence layer for Streamlit in Snowflake.

Replaces file-based JSON storage with Snowflake tables in DATALENS.METADATA.
All writes use Snowpark create_dataframe; all reads use session.sql().
"""

import json
from typing import Optional

DEFAULT_DB     = "DATALENS"
DEFAULT_SCHEMA = "METADATA"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _db(session) -> str:
    try:
        import streamlit as st
        return st.session_state.get("meta_db", DEFAULT_DB)
    except Exception:
        return DEFAULT_DB


def _sc(session) -> str:
    try:
        import streamlit as st
        return st.session_state.get("meta_schema", DEFAULT_SCHEMA)
    except Exception:
        return DEFAULT_SCHEMA


def profile_key(database: str, schema: str, table: str) -> str:
    return f"{database.upper()}__{schema.upper()}__{table.upper()}"


def _tbl(session, name: str) -> str:
    return f"{_db(session)}.{_sc(session)}.{name}"


# ── Initialization ────────────────────────────────────────────────────────────

def initialize(session) -> None:
    """Create DATALENS.METADATA schema and all tables if they do not exist."""
    db = _db(session)
    sc = _sc(session)

    session.sql(f"CREATE DATABASE IF NOT EXISTS {db}").collect()
    session.sql(f"CREATE SCHEMA IF NOT EXISTS {db}.{sc}").collect()

    ddls = [
        f"""CREATE TABLE IF NOT EXISTS {db}.{sc}.PROFILE_RESULTS (
            PROFILE_KEY   VARCHAR(500) NOT NULL,
            DATABASE_NAME VARCHAR(255),
            SCHEMA_NAME   VARCHAR(255),
            TABLE_NAME    VARCHAR(255),
            PROFILE_JSON  VARCHAR,
            CREATED_AT    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            CONSTRAINT PK_PROF PRIMARY KEY (PROFILE_KEY)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {db}.{sc}.RELATIONSHIP_RESULTS (
            PROFILE_KEY VARCHAR(500) NOT NULL,
            RESULT_JSON VARCHAR,
            CREATED_AT  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            CONSTRAINT PK_REL PRIMARY KEY (PROFILE_KEY)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {db}.{sc}.CORRELATION_RESULTS (
            PROFILE_KEY VARCHAR(500) NOT NULL,
            RESULT_JSON VARCHAR,
            CREATED_AT  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            CONSTRAINT PK_CORR PRIMARY KEY (PROFILE_KEY)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {db}.{sc}.CLUSTERING_RESULTS (
            PROFILE_KEY VARCHAR(500) NOT NULL,
            RESULT_JSON VARCHAR,
            CREATED_AT  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            CONSTRAINT PK_CLUS PRIMARY KEY (PROFILE_KEY)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {db}.{sc}.COLUMN_COLORS (
            PROFILE_KEY VARCHAR(500) NOT NULL,
            COLUMN_NAME VARCHAR(255) NOT NULL,
            COLOR       VARCHAR(50),
            CONSTRAINT PK_COLORS PRIMARY KEY (PROFILE_KEY, COLUMN_NAME)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {db}.{sc}.APP_SETTINGS (
            SETTING_KEY   VARCHAR(255) NOT NULL,
            SETTING_VALUE VARCHAR,
            UPDATED_AT    TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            CONSTRAINT PK_APP_SETTINGS PRIMARY KEY (SETTING_KEY)
        )""",
        f"""CREATE TABLE IF NOT EXISTS {db}.{sc}.TREND_RESULTS (
            PROFILE_KEY VARCHAR(500) NOT NULL,
            RESULT_JSON VARCHAR,
            CREATED_AT  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            CONSTRAINT PK_TREND PRIMARY KEY (PROFILE_KEY)
        )""",
    ]
    for ddl in ddls:
        session.sql(ddl).collect()


# ── Profile ───────────────────────────────────────────────────────────────────

def _dq(s: str) -> str:
    """Escape a string for use inside $$ dollar-quoted SQL literals."""
    return s.replace("$$", "$ $")


def save_profile(session, database: str, schema: str, table: str, profile: dict) -> None:
    key  = profile_key(database, schema, table)
    tbl  = _tbl(session, "PROFILE_RESULTS")
    session.sql(f"DELETE FROM {tbl} WHERE PROFILE_KEY = '{key}'").collect()
    session.sql(f"""
        INSERT INTO {tbl} (PROFILE_KEY, DATABASE_NAME, SCHEMA_NAME, TABLE_NAME, PROFILE_JSON)
        SELECT '{key}', '{database.upper()}', '{schema.upper()}', '{table.upper()}',
               $${_dq(json.dumps(profile))}$$
    """).collect()


def load_profile(session, database: str, schema: str, table: str) -> Optional[dict]:
    key = profile_key(database, schema, table)
    row = session.sql(
        f"SELECT PROFILE_JSON FROM {_tbl(session, 'PROFILE_RESULTS')} WHERE PROFILE_KEY = '{key}'"
    ).first()
    return json.loads(row[0]) if row and row[0] else None


def list_profiles(session) -> list:
    rows = session.sql(
        f"SELECT PROFILE_JSON FROM {_tbl(session, 'PROFILE_RESULTS')} ORDER BY CREATED_AT DESC"
    ).collect()
    out = []
    for r in rows:
        try:
            out.append(json.loads(r[0]))
        except Exception:
            pass
    return out


def delete_profile(session, database: str, schema: str, table: str) -> None:
    key = profile_key(database, schema, table)
    for tname in ("PROFILE_RESULTS", "RELATIONSHIP_RESULTS",
                  "CORRELATION_RESULTS", "CLUSTERING_RESULTS", "TREND_RESULTS"):
        session.sql(
            f"DELETE FROM {_tbl(session, tname)} WHERE PROFILE_KEY = '{key}'"
        ).collect()
    session.sql(
        f"DELETE FROM {_tbl(session, 'COLUMN_COLORS')} WHERE PROFILE_KEY = '{key}'"
    ).collect()


# ── Generic result store ──────────────────────────────────────────────────────

def save_result(
    session, result_table: str,
    database: str, schema: str, table: str,
    data: dict,
) -> None:
    key = profile_key(database, schema, table)
    tbl = _tbl(session, result_table)
    session.sql(f"DELETE FROM {tbl} WHERE PROFILE_KEY = '{key}'").collect()
    session.sql(f"""
        INSERT INTO {tbl} (PROFILE_KEY, RESULT_JSON)
        SELECT '{key}', $${_dq(json.dumps(data))}$$
    """).collect()


def load_result(
    session, result_table: str,
    database: str, schema: str, table: str,
) -> Optional[dict]:
    key = profile_key(database, schema, table)
    row = session.sql(
        f"SELECT RESULT_JSON FROM {_tbl(session, result_table)} WHERE PROFILE_KEY = '{key}'"
    ).first()
    return json.loads(row[0]) if row and row[0] else None


# ── Column colors ─────────────────────────────────────────────────────────────

def save_column_colors(
    session, database: str, schema: str, table: str, colors: dict
) -> None:
    key = profile_key(database, schema, table)
    tbl = _tbl(session, "COLUMN_COLORS")
    for col_name, color in colors.items():
        sc = col_name.replace("'", "''")
        sv = color.replace("'", "''")
        session.sql(f"""
            MERGE INTO {tbl} AS t
            USING (SELECT '{key}' AS pk, '{sc}' AS cn, '{sv}' AS c) AS s
              ON t.PROFILE_KEY = s.pk AND t.COLUMN_NAME = s.cn
            WHEN MATCHED     THEN UPDATE SET COLOR = s.c
            WHEN NOT MATCHED THEN INSERT (PROFILE_KEY, COLUMN_NAME, COLOR)
                                  VALUES (s.pk, s.cn, s.c)
        """).collect()


def load_column_colors(
    session, database: str, schema: str, table: str
) -> dict:
    key  = profile_key(database, schema, table)
    rows = session.sql(
        f"SELECT COLUMN_NAME, COLOR FROM {_tbl(session, 'COLUMN_COLORS')} "
        f"WHERE PROFILE_KEY = '{key}'"
    ).collect()
    return {r[0]: r[1] for r in rows}


# ── Result merge ──────────────────────────────────────────────────────────────

def merge_result(
    session, result_table: str,
    database: str, schema: str, table: str,
    updates: dict,
) -> None:
    """Load existing result JSON, apply updates, and re-save."""
    current = load_result(session, result_table, database, schema, table) or {}
    current.update(updates)
    save_result(session, result_table, database, schema, table, current)


# ── App settings ──────────────────────────────────────────────────────────────

def save_app_setting(session, key: str, value: str) -> None:
    tbl = _tbl(session, "APP_SETTINGS")
    k   = key.replace("'", "''")
    v   = str(value).replace("'", "''") if value is not None else ""
    session.sql(f"""
        MERGE INTO {tbl} AS t
        USING (SELECT '{k}' AS sk, '{v}' AS sv) AS s
          ON t.SETTING_KEY = s.sk
        WHEN MATCHED     THEN UPDATE SET SETTING_VALUE = s.sv,
                                         UPDATED_AT    = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT (SETTING_KEY, SETTING_VALUE)
                              VALUES (s.sk, s.sv)
    """).collect()


def load_app_setting(session, key: str, default: Optional[str] = None) -> Optional[str]:
    k   = key.replace("'", "''")
    row = session.sql(
        f"SELECT SETTING_VALUE FROM {_tbl(session, 'APP_SETTINGS')} "
        f"WHERE SETTING_KEY = '{k}'"
    ).first()
    return row[0] if row else default


def load_all_app_settings(session) -> dict:
    rows = session.sql(
        f"SELECT SETTING_KEY, SETTING_VALUE FROM {_tbl(session, 'APP_SETTINGS')}"
    ).collect()
    return {r[0]: r[1] for r in rows}
