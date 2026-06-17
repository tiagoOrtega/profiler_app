"""DataLens -- Data Catalog & Export page.

Generates a shareable Markdown catalog from all profiled tables:
column stats, relationships, quality flags, and optional AI descriptions.
Download as Markdown, export as JSON, or save directly to Snowflake.
"""

import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd

from sis_session import get_session
try:
    session = get_session()
except RuntimeError as _e:
    st.error(str(_e))
    st.stop()

import sis_persistence as persistence
import sis_cortex as cortex

# ── Catalog builder ────────────────────────────────────────────────────────────

def _build_catalog(
    selected_profiles: list,
    rel_map:           dict,
    ai_descriptions:   dict,
    opts:              dict,
) -> str:
    from datetime import date
    lines = [
        "# DataLens Data Catalog",
        "",
        f"*Generated {date.today().isoformat()}  ·  {len(selected_profiles)} table(s)*",
        "",
    ]

    for p in selected_profiles:
        db    = p.get("database", "")
        sc    = p.get("schema", "")
        tbl   = p.get("table", "")
        rows  = p.get("row_count", 0)
        n_col = p.get("column_count", 0)
        dated = (p.get("profiled_at") or "")[:10]
        key   = persistence.profile_key(db, sc, tbl)

        lines += [
            "---",
            "",
            f"## {tbl}",
            f"**Location:** `{db}.{sc}`  "
            f"| **Rows:** {rows:,}  "
            f"| **Columns:** {n_col}  "
            f"| **Profiled:** {dated}",
            "",
        ]

        # AI / manual table description
        desc = ai_descriptions.get(key) or p.get("table_comment")
        if opts["ai"] and desc:
            lines += [f"> {desc}", ""]

        # Column stats table
        if opts["stats"] and p.get("columns"):
            lines += ["### Columns", ""]
            lines += ["| Column | Type | Nulls | Distinct | Min | Max | Mean |"]
            lines += ["|--------|------|------:|--------:|----:|----:|-----:|"]
            for c in p["columns"]:
                null_r = f"{c.get('null_rate', 0):.1%}" if c.get("null_rate") is not None else "—"
                dist   = f"{c.get('distinct_count', 0):,}" if c.get("distinct_count") else "—"
                mn     = str(c["min"])  if c.get("min")  is not None else "—"
                mx     = str(c["max"])  if c.get("max")  is not None else "—"
                mean   = f"{c['mean']:.3f}" if c.get("mean") is not None else "—"
                col_ai = ""
                if opts["col_desc"]:
                    col_ai = ai_descriptions.get(f"{key}::{c['name']}", "")
                name_cell = f"**{c['name']}**" + (f"<br><sub>{col_ai}</sub>" if col_ai else "")
                lines.append(
                    f"| {name_cell} | `{c.get('data_type', '')}` | {null_r} | {dist} | {mn} | {mx} | {mean} |"
                )
            lines.append("")

        # Relationships
        rels = rel_map.get(key, {}).get("relationships", [])
        if opts["rels"] and rels:
            lines += ["### Relationships", ""]
            for r in rels[:15]:
                conf = r.get("confidence", 0)
                lines.append(
                    f"- `{r.get('from_col')}` → "
                    f"`{r.get('to_table')}.{r.get('to_col')}`  "
                    f"*(confidence: {conf:.0%})*"
                )
            lines.append("")

        # Quality flags
        if opts["quality"]:
            flags = []
            for c in p.get("columns", []):
                null_r = c.get("null_rate", 0)
                if null_r and null_r > 0.2:
                    flags.append(f"- ⚠ `{c['name']}`: {null_r:.1%} null rate")
                if c.get("error"):
                    flags.append(f"- ✗ `{c['name']}`: profiling error — {c['error']}")
            if flags:
                lines += ["### Quality Flags", ""]
                lines += flags
                lines.append("")

    return "\n".join(lines)


# ══════════════════════════════ PAGE ═════════════════════════════════════════

st.title("Data Catalog & Export")
st.caption(
    "Generate a portable data catalog from your profiled tables — "
    "download as Markdown, export as JSON, or save directly to Snowflake."
)

try:
    profiles = persistence.list_profiles(session)
except Exception as e:
    st.error(f"Could not load profiles: {e}")
    st.stop()

if not profiles:
    st.info("No profiles found. Go to **Profile** first to profile a table.")
    st.stop()

# ── Table selector ────────────────────────────────────────────────────────────

st.subheader("Tables")
all_keys = [
    f"{p.get('database')}.{p.get('schema')}.{p.get('table')}"
    for p in profiles
]
sel_keys = st.multiselect(
    "Include in catalog",
    all_keys,
    default=all_keys,
    key="cat_tables",
)
selected_profiles = [p for p in profiles if
    f"{p.get('database')}.{p.get('schema')}.{p.get('table')}" in sel_keys]

if not selected_profiles:
    st.warning("Select at least one table.")
    st.stop()

# ── Options ───────────────────────────────────────────────────────────────────

st.subheader("Include")
opt_c1, opt_c2, opt_c3, opt_c4, opt_c5 = st.columns(5)
with opt_c1:
    opt_stats    = st.checkbox("Column Stats",     value=True,  key="cat_stats")
with opt_c2:
    opt_rels     = st.checkbox("Relationships",    value=True,  key="cat_rels")
with opt_c3:
    opt_quality  = st.checkbox("Quality Flags",   value=True,  key="cat_qual")
with opt_c4:
    opt_ai       = st.checkbox("Table Descriptions", value=True, key="cat_ai")
with opt_c5:
    opt_col_desc = st.checkbox("Column Descriptions", value=False, key="cat_col_desc")

opts = {
    "stats":    opt_stats,
    "rels":     opt_rels,
    "quality":  opt_quality,
    "ai":       opt_ai,
    "col_desc": opt_col_desc,
}

# ── AI description generation ─────────────────────────────────────────────────

_ai_cache_key = "cat_ai_descriptions"
if _ai_cache_key not in st.session_state:
    st.session_state[_ai_cache_key] = {}

ai_descriptions: dict = st.session_state[_ai_cache_key]

if opt_ai or opt_col_desc:
    st.divider()
    ai_c1, ai_c2, ai_c3 = st.columns([2, 2, 3])
    with ai_c1:
        if st.button("Generate Table Descriptions", key="cat_gen_tbl"):
            with st.spinner(f"Generating descriptions for {len(selected_profiles)} table(s) …"):
                for _p in selected_profiles:
                    _key = persistence.profile_key(
                        _p.get("database", ""), _p.get("schema", ""), _p.get("table", "")
                    )
                    if _key not in ai_descriptions:
                        try:
                            ai_descriptions[_key] = cortex.generate_table_comment(session, _p)
                        except Exception:
                            pass
            st.session_state[_ai_cache_key] = ai_descriptions
            st.success(f"Descriptions generated for {len(selected_profiles)} table(s).")

    if opt_col_desc:
        with ai_c2:
            if st.button("Generate Column Descriptions", key="cat_gen_col"):
                with st.spinner("Generating column descriptions (this may take a moment) …"):
                    for _p in selected_profiles:
                        _pk = persistence.profile_key(
                            _p.get("database", ""), _p.get("schema", ""), _p.get("table", "")
                        )
                        try:
                            col_descs = cortex.generate_column_comments(session, _p)
                            for _cn, _cd in col_descs.items():
                                ai_descriptions[f"{_pk}::{_cn}"] = _cd
                        except Exception:
                            pass
                st.session_state[_ai_cache_key] = ai_descriptions
                st.success("Column descriptions generated.")

    n_desc = sum(1 for k in ai_descriptions if "::" not in k)
    n_col  = sum(1 for k in ai_descriptions if "::" in k)
    with ai_c3:
        if ai_descriptions:
            st.caption(f"Cached: {n_desc} table description(s), {n_col} column description(s).")
            if st.button("Clear AI Cache", key="cat_clear_ai"):
                st.session_state[_ai_cache_key] = {}
                st.rerun()

# ── Load relationships ────────────────────────────────────────────────────────

rel_map: dict = {}
if opt_rels:
    for _p in selected_profiles:
        _pk  = persistence.profile_key(
            _p.get("database", ""), _p.get("schema", ""), _p.get("table", "")
        )
        _rel = persistence.load_result(
            session, "RELATIONSHIP_RESULTS",
            _p.get("database", ""), _p.get("schema", ""), _p.get("table", "")
        )
        if _rel:
            rel_map[_pk] = _rel

# ── Generate & preview ────────────────────────────────────────────────────────

st.divider()
gen_col, _ = st.columns([1, 3])
with gen_col:
    gen_clicked = st.button(
        "Generate Catalog", type="primary",
        key="cat_generate", use_container_width=True,
    )

if gen_clicked or st.session_state.get("cat_content"):
    if gen_clicked:
        catalog_md = _build_catalog(selected_profiles, rel_map, ai_descriptions, opts)
        st.session_state["cat_content"] = catalog_md

    catalog_md = st.session_state.get("cat_content", "")

    if catalog_md:
        n_lines = catalog_md.count("\n")
        st.caption(
            f"Catalog generated: {len(selected_profiles)} table(s)  ·  "
            f"~{n_lines} lines  ·  {len(catalog_md):,} characters"
        )

        # ── Download / Save ───────────────────────────────────────────────────

        dl_c1, dl_c2, dl_c3 = st.columns([1, 1, 1])
        with dl_c1:
            st.download_button(
                "Download Markdown",
                data=catalog_md,
                file_name="datalens_catalog.md",
                mime="text/markdown",
                key="cat_dl_md",
                use_container_width=True,
            )
        with dl_c2:
            json_payload = json.dumps(
                [p for p in profiles
                 if f"{p.get('database')}.{p.get('schema')}.{p.get('table')}" in sel_keys],
                indent=2,
            )
            st.download_button(
                "Download JSON",
                data=json_payload,
                file_name="datalens_catalog.json",
                mime="application/json",
                key="cat_dl_json",
                use_container_width=True,
            )
        with dl_c3:
            if st.button("Save to Snowflake", key="cat_save_sf", use_container_width=True):
                with st.spinner("Saving to Snowflake …"):
                    try:
                        persistence.save_catalog(session, catalog_md)
                        tbl_loc = (
                            f"{st.session_state.get('meta_db', persistence.DEFAULT_DB)}"
                            f".{st.session_state.get('meta_schema', persistence.DEFAULT_SCHEMA)}"
                            f".CATALOG_EXPORTS"
                        )
                        st.success(
                            f"Saved to **{tbl_loc}** (key: LATEST).  "
                            "Query with: `SELECT CONTENT_MD FROM {tbl_loc} WHERE EXPORT_KEY = 'LATEST'`"
                        )
                    except Exception as _e:
                        st.error(f"Save failed: {_e}")

        # ── Preview ───────────────────────────────────────────────────────────

        with st.expander("Preview", expanded=True):
            st.markdown(catalog_md)
