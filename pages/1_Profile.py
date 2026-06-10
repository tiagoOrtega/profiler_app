"""DataLens -- Profile page. Select a table and run full column-level profiling."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dataclasses
import streamlit as st


from sis_session import get_session
try:
    session = get_session()
except RuntimeError as _e:
    st.error(str(_e))
    st.stop()

import sis_persistence as persistence
from platforms.snowpark_platform import SnowparkPlatform
from profiling import DataProfiler

platform = SnowparkPlatform(session)

st.title("Run Profile")
st.caption("Select a Database, Schema, and Table to profile.")


@st.cache_data(ttl=120, show_spinner=False)
def _databases(_session):
    return SnowparkPlatform(_session).get_databases()

@st.cache_data(ttl=120, show_spinner=False)
def _schemas(_session, db):
    return SnowparkPlatform(_session).get_schemas(db)

@st.cache_data(ttl=120, show_spinner=False)
def _tables(_session, db, schema):
    return SnowparkPlatform(_session).get_tables(db, schema)


try:
    databases = _databases(session)
except Exception as e:
    st.error(f"Could not list databases: {e}")
    st.stop()

col1, col2, col3 = st.columns(3)

with col1:
    db = st.selectbox("Database", databases, key="prof_db",
                      index=databases.index(st.session_state.get("sel_db", databases[0]))
                      if st.session_state.get("sel_db") in databases else 0)

schemas = _schemas(session, db) if db else []
with col2:
    schema = st.selectbox("Schema", schemas, key="prof_schema",
                          index=schemas.index(st.session_state.get("sel_schema", schemas[0] if schemas else ""))
                          if schemas and st.session_state.get("sel_schema") in schemas else 0)

tables = _tables(session, db, schema) if db and schema else []
with col3:
    table = st.selectbox("Table", tables, key="prof_table",
                         index=tables.index(st.session_state.get("sel_table", tables[0] if tables else ""))
                         if tables and st.session_state.get("sel_table") in tables else 0)

if db and schema and table:
    existing = persistence.load_profile(session, db, schema, table)
    if existing:
        st.info(
            f"Last profiled: **{existing.get('profiled_at', '')[:19].replace('T', ' ')}** -- "
            f"{existing.get('row_count', 0):,} rows, {existing.get('column_count', 0)} columns"
        )

st.divider()

col_btn, _ = st.columns([1, 3])
with col_btn:
    run = st.button("Run Profile", type="primary", disabled=not (db and schema and table),
                    use_container_width=True)

if run and db and schema and table:
    with st.spinner(f"Profiling {db}.{schema}.{table} ..."):
        try:
            profiler     = DataProfiler(platform)
            result       = profiler.profile_table(db, schema, table)
            profile_dict = dataclasses.asdict(result)
            persistence.save_profile(session, db, schema, table, profile_dict)

            st.session_state["sel_db"]     = db
            st.session_state["sel_schema"] = schema
            st.session_state["sel_table"]  = table

            st.success(
                f"Profiled **{db}.{schema}.{table}** -- "
                f"{result.row_count:,} rows, {result.column_count} columns"
            )
            errors = [c for c in profile_dict["columns"] if c.get("error")]
            nulls  = [c for c in profile_dict["columns"] if (c.get("null_rate") or 0) > 0.5]
            if errors:
                st.warning(f"{len(errors)} column(s) had profiling errors (see Report > Columns).")
            if nulls:
                st.warning(f"{len(nulls)} column(s) have >50% null rate.")
            st.balloons()
        except Exception as exc:
            st.error(f"Profiling failed: {exc}")

elif run:
    st.warning("Select a database, schema, and table first.")

st.divider()
st.subheader("Recently Profiled")

try:
    profiles = persistence.list_profiles(session)
except Exception:
    profiles = []

if profiles:
    import pandas as pd
    rows = [
        {
            "Table":    f"{p.get('database')}.{p.get('schema')}.{p.get('table')}",
            "Rows":     f"{p.get('row_count', 0):,}",
            "Columns":  p.get("column_count", 0),
            "Profiled": p.get("profiled_at", "")[:19].replace("T", " "),
        }
        for p in profiles[:10]
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
else:
    st.caption("No profiles yet.")
