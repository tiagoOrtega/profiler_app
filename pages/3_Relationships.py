"""DataLens -- Relationships page. Detect FK-like relationships across profiled tables."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import tempfile
import streamlit as st
import pandas as pd


from sis_session import get_session
try:
    session = get_session()
except RuntimeError as _e:
    st.error(str(_e))
    st.stop()

import sis_persistence as persistence
from platforms.snowpark_platform import SnowparkPlatform
from relationships import RelationshipDetector
import dataclasses

platform = SnowparkPlatform(session)

st.title("Relationship Detection")
st.caption(
    "Detects FK-like column containment across profiled tables using the EXCEPT operator. "
    "Columns with matching names are tested for referential integrity."
)

try:
    profiles = persistence.list_profiles(session)
except Exception as e:
    st.error(f"Could not load profiles: {e}")
    st.stop()

if len(profiles) < 2:
    st.warning("At least 2 profiled tables are required. Go to **Profile** to add more.")
    st.stop()

options = [f"{p.get('database')}.{p.get('schema')}.{p.get('table')}" for p in profiles]

sel_key = (
    f"{st.session_state.get('sel_db')}.{st.session_state.get('sel_schema')}"
    f".{st.session_state.get('sel_table')}"
    if st.session_state.get("sel_db") else None
)
default_idx = options.index(sel_key) if sel_key and sel_key in options else 0

source    = st.selectbox("Source table", options, index=default_idx, key="rel_source")
src_parts = source.split(".", 2)
if len(src_parts) != 3:
    st.stop()
db, schema, table = src_parts

other_tables     = [o for o in options if o != source]
selected_targets = st.multiselect(
    "Test against (leave empty = all profiled tables)",
    other_tables,
    key="rel_targets",
)

col_btn, col_cache = st.columns([1, 3])
with col_btn:
    run_rel = st.button("Detect Relationships", type="primary", key="run_rel_btn")

rel_data = persistence.load_result(session, "RELATIONSHIP_RESULTS", db, schema, table)

if rel_data:
    col_cache.caption(f"Cached result: {len(rel_data.get('relationships', []))} candidates")

if run_rel:
    with st.spinner(f"Detecting relationships for {source} ..."):
        if selected_targets:
            target_keys = set()
            for t in selected_targets:
                pts = t.split(".", 2)
                if len(pts) == 3:
                    target_keys.add(f"{pts[0]}__{pts[1]}__{pts[2]}".upper())
            src_key = f"{db}__{schema}__{table}".upper()
            filtered_profiles = [
                p for p in profiles
                if f"{p.get('database')}__{p.get('schema')}__{p.get('table')}".upper()
                in (target_keys | {src_key})
            ]
        else:
            filtered_profiles = profiles

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            for p in filtered_profiles:
                key = f"{p['database']}__{p['schema']}__{p['table']}".upper()
                (tmp / f"{key}.json").write_text(json.dumps(p))

            detector = RelationshipDetector(platform, tmp)
            results  = detector.detect(db, schema, table)

        rel_list = [dataclasses.asdict(r) for r in results]
        rel_data = {"relationships": rel_list}
        persistence.save_result(session, "RELATIONSHIP_RESULTS", db, schema, table, rel_data)

    st.success(f"Found {len(rel_list)} candidate relationships.")

if rel_data:
    rels = rel_data.get("relationships", [])
    if not rels:
        st.info("No matching columns found. Try profiling more related tables.")
    else:
        pass_ct = sum(1 for r in rels if r["status"] == "PASS")
        warn_ct = sum(1 for r in rels if r["status"] == "WARN")
        fail_ct = sum(1 for r in rels if r["status"] == "FAIL")

        s1, s2, s3 = st.columns(3)
        s1.metric("PASS (perfect match)", pass_ct)
        s2.metric("WARN (>=90% match)",   warn_ct)
        s3.metric("FAIL (<90% match)",    fail_ct)

        st.divider()

        status_filter = st.multiselect(
            "Filter by status", ["PASS", "WARN", "FAIL"],
            default=["PASS", "WARN", "FAIL"], key="rel_status_filter"
        )
        status_icon = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL"}
        visible = [r for r in rels if r["status"] in status_filter]

        rows = [
            {
                "Status":        r["status"],
                "Source Column": r["source_column"],
                "Target Table":  f"{r['target_db']}.{r['target_schema']}.{r['target_table']}",
                "Target Column": r["target_column"],
                "Match %":       f"{r['match_pct']:.1%}",
                "Orphans":       r["orphans"],
                "Src Distinct":  r["src_distinct"],
                "Tgt Distinct":  r["tgt_distinct"],
            }
            for r in visible
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
else:
    st.info("Select a source table and click **Detect Relationships** to start.")
