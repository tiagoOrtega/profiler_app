"""
DataLens -- Streamlit in Snowflake entry point.

Router: initialises the session and metadata once, then delegates to the
selected page via st.navigation() so the home page displays as "DataLens"
rather than the auto-generated filename label.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
import pandas as pd

st.set_page_config(
    page_title="DataLens",
    page_icon=":mag:",
    layout="wide",
    initial_sidebar_state="expanded",
)

from sis_session import get_session
try:
    session = get_session()
except RuntimeError as _e:
    st.error(str(_e))
    st.stop()

import sis_persistence as persistence

if "meta_initialized" not in st.session_state:
    try:
        persistence.initialize(session)
        st.session_state["meta_initialized"] = True
    except Exception as e:
        st.error(f"Could not initialize metadata schema: {e}")
        st.info(
            "Run **setup_datalens_metadata.sql** in a Snowflake worksheet to "
            "create the DATALENS.METADATA schema, then refresh this page."
        )
        st.stop()

if "settings_loaded" not in st.session_state:
    try:
        saved = persistence.load_all_app_settings(session)
        for k, v in saved.items():
            if k not in st.session_state:
                st.session_state[k] = v
        st.session_state["settings_loaded"] = True
    except Exception:
        pass


# ── Home / Dashboard page ─────────────────────────────────────────────────────

def _home():
    st.title("DataLens")
    st.caption("Multi-platform data intelligence — native Snowflake")

    try:
        profiles = persistence.list_profiles(session)
    except Exception as e:
        st.error(f"Could not load profiles: {e}")
        profiles = []

    if profiles:
        total_rows = sum(p.get("row_count", 0) for p in profiles)
        total_cols = sum(p.get("column_count", 0) for p in profiles)
        alert_tbls = sum(
            1 for p in profiles
            if any(c.get("null_rate", 0) > 0.5 for c in p.get("columns", []))
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Profiled Tables",  len(profiles))
        m2.metric("Total Rows",       f"{total_rows:,}")
        m3.metric("Total Columns",    total_cols)
        m4.metric("Tables with Alerts", alert_tbls)

        st.divider()

        rows = []
        for p in profiles:
            alert_count = sum(
                1 for c in p.get("columns", [])
                if c.get("null_rate", 0) > 0.5 or c.get("error")
            )
            rows.append({
                "Database": p.get("database", ""),
                "Schema":   p.get("schema", ""),
                "Table":    p.get("table", ""),
                "Rows":     f"{p.get('row_count', 0):,}",
                "Columns":  p.get("column_count", 0),
                "Alerts":   alert_count,
                "Profiled": p.get("profiled_at", "")[:19].replace("T", " "),
            })

        df = pd.DataFrame(rows)
        selection = st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
        )

        if selection.selection.rows:
            idx     = selection.selection.rows[0]
            profile = profiles[idx]
            st.session_state["sel_db"]     = profile.get("database")
            st.session_state["sel_schema"] = profile.get("schema")
            st.session_state["sel_table"]  = profile.get("table")
            st.info(
                f"Selected **{profile.get('database')}.{profile.get('schema')}"
                f".{profile.get('table')}** — open **Report** in the sidebar."
            )

    else:
        st.info(
            "No tables profiled yet. Open **Profile** in the sidebar to run your first profile."
        )
        st.markdown("""
**Getting started:**
1. Open **Configuration** to verify your Cortex model preference
2. Open **Profile** — pick a Database / Schema / Table and click **Run Profile**
3. Return here to see the results, then open **Report** for full details
""")


# ── Navigation ────────────────────────────────────────────────────────────────

pg = st.navigation([
    st.Page(_home,                      title="DataLens"),
    st.Page("pages/1_Profile.py",       title="Profile"),
    st.Page("pages/2_Report.py",        title="Report"),
    st.Page("pages/3_Relationships.py", title="Relationships"),
    st.Page("pages/4_Clustering.py",    title="Clustering"),
    st.Page("pages/7_Trends.py",        title="Trends"),
    st.Page("pages/8_Catalog.py",       title="Catalog"),
    st.Page("pages/9_Summary.py",        title="Business Report"),
    st.Page("pages/10_Geolocation.py",  title="Geolocation"),
    st.Page("pages/5_Configuration.py", title="Configuration"),
    st.Page("pages/6_Help.py",          title="Help"),
])
pg.run()
