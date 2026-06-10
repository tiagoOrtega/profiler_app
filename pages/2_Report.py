"""DataLens -- Report page. Five-tab view: Overview / Columns / Relationships / Correlation / Clustering."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import tempfile
import streamlit as st
import pandas as pd
import plotly.graph_objects as go


from sis_session import get_session
try:
    session = get_session()
except RuntimeError as _e:
    st.error(str(_e))
    st.stop()

import sis_persistence as persistence
import sis_cortex as cortex
from platforms.snowpark_platform import SnowparkPlatform

platform = SnowparkPlatform(session)

st.title("Report")

try:
    profiles = persistence.list_profiles(session)
except Exception as e:
    st.error(f"Could not load profiles: {e}")
    st.stop()

if not profiles:
    st.info("No profiles found. Go to **Profile** to run your first profile.")
    st.stop()

options = [f"{p.get('database')}.{p.get('schema')}.{p.get('table')}" for p in profiles]
sel_key = (
    f"{st.session_state.get('sel_db')}.{st.session_state.get('sel_schema')}"
    f".{st.session_state.get('sel_table')}"
    if st.session_state.get("sel_db") else None
)
default_idx = options.index(sel_key) if sel_key and sel_key in options else 0

chosen = st.selectbox("Select profiled table", options, index=default_idx)
parts  = chosen.split(".", 2)
if len(parts) != 3:
    st.error("Invalid table selection.")
    st.stop()

db, schema, table = parts
profile = persistence.load_profile(session, db, schema, table)
if not profile:
    st.error(f"Profile not found for {chosen}.")
    st.stop()

columns_data = profile.get("columns", [])

tab_ov, tab_col, tab_rel, tab_corr = st.tabs(
    ["Overview", "Columns", "Relationships", "Correlation"]
)


# ── TAB 1: OVERVIEW ───────────────────────────────────────────────────────────

with tab_ov:
    st.subheader(f"{db}.{schema}.{table}")
    if profile.get("comment"):
        st.caption(profile["comment"])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rows",    f"{profile.get('row_count', 0):,}")
    m2.metric("Columns", profile.get("column_count", 0))
    numeric_ct = sum(1 for c in columns_data if c.get("mean") is not None)
    string_ct  = sum(1 for c in columns_data if c.get("min_length") is not None)
    date_ct    = sum(1 for c in columns_data if c.get("min_date") is not None)
    m3.metric("Numeric Columns", numeric_ct)
    m4.metric("Profiled At", profile.get("profiled_at", "")[:10])

    st.divider()
    col_left, col_right = st.columns(2)

    with col_left:
        other_ct = len(columns_data) - numeric_ct - string_ct - date_ct
        fig = go.Figure(go.Pie(
            labels=["Numeric", "String", "Date", "Other"],
            values=[numeric_ct, string_ct, date_ct, other_ct],
            hole=0.4,
            marker_colors=["#58a6ff", "#3fb950", "#d29922", "#6e7681"],
        ))
        fig.update_layout(title="Column Types", height=280, margin=dict(t=40, b=0))
        st.plotly_chart(fig, use_container_width=True)

    with col_right:
        null_rates = [c.get("null_rate", 0) for c in columns_data]
        fig2 = go.Figure(go.Histogram(x=null_rates, nbinsx=20,
                                      marker_color="#58a6ff", opacity=0.8))
        fig2.update_layout(title="Null Rate Distribution",
                           xaxis_title="Null Rate", yaxis_title="Count",
                           height=280, margin=dict(t=40, b=30))
        st.plotly_chart(fig2, use_container_width=True)

    alerts = [c for c in columns_data if (c.get("null_rate", 0) > 0.5 or c.get("error"))]
    if alerts:
        st.warning(f"{len(alerts)} column(s) with alerts")
        alert_df = pd.DataFrame([
            {"Column": c["name"], "Type": c.get("data_type", ""),
             "Null Rate": f"{c.get('null_rate', 0):.1%}", "Error": c.get("error", "")}
            for c in alerts
        ])
        st.dataframe(alert_df, use_container_width=True, hide_index=True)

    st.divider()
    with st.expander("Generate Table Description (Cortex AI)"):
        if st.button("Generate Description", key="gen_tbl_comment"):
            with st.spinner("Generating description ..."):
                desc = cortex.generate_table_comment(session, profile)
            st.text_area("Generated description", desc, height=100, key="tbl_comment_txt")
            if desc and st.button("Apply to Snowflake", key="apply_tbl_comment"):
                err = cortex.apply_table_comment(session, db, schema, table, desc)
                if err:
                    st.error(f"Apply failed: {err}")
                else:
                    st.success("Table comment updated in Snowflake.")


# ── TAB 2: COLUMNS ────────────────────────────────────────────────────────────

with tab_col:
    st.subheader("Column Details")

    ctrl1, ctrl2, ctrl3 = st.columns([2, 1, 1])
    with ctrl1:
        search = st.text_input("Search columns", placeholder="column name ...", key="col_search")
    with ctrl2:
        show_alerts = st.checkbox("Alerts only", key="col_alerts")
    with ctrl3:
        type_filter = st.selectbox("Type filter", ["All", "numeric", "string", "date"], key="col_type")

    visible = columns_data

    if search:
        visible = [c for c in visible if search.lower() in c["name"].lower()]
    if show_alerts:
        visible = [c for c in visible if (c.get("null_rate", 0) > 0.5 or c.get("error"))]
    if type_filter == "numeric":
        visible = [c for c in visible if c.get("mean") is not None]
    elif type_filter == "string":
        visible = [c for c in visible if c.get("min_length") is not None]
    elif type_filter == "date":
        visible = [c for c in visible if c.get("min_date") is not None]

    disp_rows = []
    for c in visible:
        stats = ""
        if c.get("mean") is not None:
            stats = f"min={c.get('min_val'):.2g} max={c.get('max_val'):.2g} mean={c.get('mean'):.2g}"
        elif c.get("min_length") is not None:
            stats = f"len {c.get('min_length')}-{c.get('max_length')}"
        elif c.get("min_date"):
            stats = f"{c.get('min_date')[:10]} to {c.get('max_date', '')[:10]}"
        disp_rows.append({
            "Column":   c["name"],
            "Type":     c.get("data_type", ""),
            "Nulls":    f"{c.get('null_rate', 0):.1%}",
            "Distinct": c.get("distinct_count", 0),
            "Unique%":  f"{c.get('uniqueness_rate', 0):.1%}",
            "Stats":    stats,
            "Comment":  c.get("comment") or "",
            "Alert":    "!" if (c.get("null_rate", 0) > 0.5 or c.get("error")) else "",
        })

    st.dataframe(pd.DataFrame(disp_rows), use_container_width=True, hide_index=True)

    with st.expander("Generate Column Descriptions (Cortex AI)"):
        if st.button("Generate All Column Descriptions", key="gen_col_comments"):
            with st.spinner("Generating descriptions ..."):
                suggestions = cortex.generate_column_comments(session, profile)
            if suggestions:
                edited = {}
                for col_name, desc in suggestions.items():
                    edited[col_name] = st.text_input(col_name, value=desc, key=f"col_desc_{col_name}")
                if st.button("Apply All to Snowflake", key="apply_col_comments"):
                    with st.spinner("Applying comments ..."):
                        errors = cortex.apply_column_comments(session, db, schema, table, edited)
                    if errors:
                        st.error(f"Some columns failed: {'; '.join(errors)}")
                    else:
                        st.success("All column comments applied.")


# ── TAB 3: RELATIONSHIPS ──────────────────────────────────────────────────────

with tab_rel:
    st.subheader("Relationship Detection")
    st.caption("Detects FK-like containment across all profiled tables using the EXCEPT operator.")

    rel_data = persistence.load_result(session, "RELATIONSHIP_RESULTS", db, schema, table)

    col1, _ = st.columns([1, 4])
    with col1:
        run_rel = st.button("Detect Relationships", type="primary", key="run_rel")

    if run_rel:
        all_profiles = persistence.list_profiles(session)
        if len(all_profiles) < 2:
            st.warning("Need at least 2 profiled tables to detect relationships.")
        else:
            with st.spinner("Detecting relationships ..."):
                from relationships import RelationshipDetector
                import dataclasses as dc
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp = Path(tmpdir)
                    for p in all_profiles:
                        key = f"{p['database']}__{p['schema']}__{p['table']}".upper()
                        (tmp / f"{key}.json").write_text(json.dumps(p))
                    detector = RelationshipDetector(platform, tmp)
                    results  = detector.detect(db, schema, table)
                rel_list = [dc.asdict(r) for r in results]
                rel_data = {"relationships": rel_list}
                persistence.save_result(session, "RELATIONSHIP_RESULTS", db, schema, table, rel_data)
            st.success(f"Found {len(rel_list)} candidate relationships.")

    if rel_data:
        rels = rel_data.get("relationships", [])
        if rels:
            rows = [
                {
                    "Status":       r["status"],
                    "Source Col":   r["source_column"],
                    "Target Table": f"{r['target_db']}.{r['target_schema']}.{r['target_table']}",
                    "Target Col":   r["target_column"],
                    "Match %":      f"{r['match_pct']:.1%}",
                    "Orphans":      r["orphans"],
                }
                for r in rels
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No matching columns found across profiled tables.")
    else:
        st.info("Click **Detect Relationships** to run analysis.")


# ── TAB 4: CORRELATION ────────────────────────────────────────────────────────

with tab_corr:
    st.subheader("Correlation Matrix")

    corr_data = persistence.load_result(session, "CORRELATION_RESULTS", db, schema, table)

    col1, _ = st.columns([1, 4])
    with col1:
        run_corr = st.button("Run Correlation", type="primary", key="run_corr")

    if run_corr:
        numeric_cols = [c["name"] for c in columns_data if c.get("mean") is not None]
        if len(numeric_cols) < 2:
            st.warning("Need at least 2 numeric columns for correlation.")
        else:
            with st.spinner("Computing correlations ..."):
                tbl_ref  = platform.table_ref(db, schema, table)
                corr_sql = platform.corr_sql(tbl_ref, numeric_cols[:25])
                row      = platform.fetch_one(corr_sql)
                n        = len(numeric_cols[:25])
                matrix   = []
                if row:
                    flat = list(row)
                    for i in range(n):
                        matrix.append([
                            float(flat[i * n + j]) if flat[i * n + j] is not None else None
                            for j in range(n)
                        ])
                corr_data = {"columns": numeric_cols[:25], "matrix": matrix}
                persistence.save_result(session, "CORRELATION_RESULTS", db, schema, table, corr_data)
            st.success("Correlation matrix computed.")

    if corr_data and corr_data.get("matrix"):
        cols   = corr_data["columns"]
        matrix = corr_data["matrix"]
        fig = go.Figure(go.Heatmap(
            z=matrix, x=cols, y=cols,
            colorscale="RdBu", zmid=0, zmin=-1, zmax=1,
            text=[[f"{v:.2f}" if v is not None else "" for v in row] for row in matrix],
            texttemplate="%{text}",
            hovertemplate="%{y} -- %{x}: %{z:.3f}<extra></extra>",
        ))
        fig.update_layout(
            height=max(400, len(cols) * 30),
            margin=dict(t=20, b=0),
            xaxis=dict(tickangle=-45),
        )
        st.plotly_chart(fig, use_container_width=True)

        with st.expander("Generate Correlation Insights (Cortex AI)"):
            _corr_ins_key = f"r_ins_{db}__{schema}__{table}"
            if _corr_ins_key not in st.session_state:
                st.session_state[_corr_ins_key] = (corr_data or {}).get("ai_insights")
            if st.button("Explain Correlations", key="explain_corr"):
                with st.spinner("Generating insights ..."):
                    explanation = cortex.explain_correlations(session, corr_data)
                st.session_state[_corr_ins_key] = explanation
                try:
                    persistence.merge_result(
                        session, "CORRELATION_RESULTS", db, schema, table,
                        {"ai_insights": explanation},
                    )
                except Exception:
                    pass
            if st.session_state.get(_corr_ins_key):
                st.markdown(st.session_state[_corr_ins_key])
    else:
        st.info("Click **Run Correlation** to compute the correlation matrix.")

    st.divider()
    st.caption("Ready to segment your data? Use the **Clustering** page for full ML clustering with preprocessing, optimal-k analysis, and sample data inspection.")


