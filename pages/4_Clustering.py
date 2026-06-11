"""DataLens -- Clustering page.

Full-featured ML clustering: model selection, preprocessing, optimal-k analysis,
feature importance, full-dataset mode, and tabbed results (scatter / optimal-k / sample data).
Consolidates the quick-run panel from the Report page with all advanced controls.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import tempfile
import numpy as np
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


from sis_session import get_session
try:
    session = get_session()
except RuntimeError as _e:
    st.error(str(_e))
    st.stop()

import sis_persistence as persistence
import sis_cortex as cortex
from platforms.snowpark_platform import SnowparkPlatform
from clustering import ClusteringEngine, MODELS as CLUSTERING_MODELS, _PALETTE, _NOISE_COLOR

platform = SnowparkPlatform(session)

# ── Constants ─────────────────────────────────────────────────────────────────

SCALERS = {
    "standard": "Standard (z-score)",
    "minmax":   "Min-Max [0-1]",
    "robust":   "Robust (IQR)",
    "none":     "None (raw)",
}
SCALER_DESC = {
    "standard": "Mean=0, std=1. Best for normally distributed features.",
    "minmax":   "Scales to [0, 1]. Sensitive to outliers.",
    "robust":   "Median/IQR — resistant to outliers.",
    "none":     "Only imputes missing values. Use when features share the same scale.",
}
K_BASED_MODELS = {"kmeans", "bisecting_kmeans"}


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _apply_scaler(scaler_type: str, X_imp: np.ndarray) -> np.ndarray:
    if scaler_type == "minmax":
        from sklearn.preprocessing import MinMaxScaler
        return MinMaxScaler().fit_transform(X_imp)
    if scaler_type == "robust":
        from sklearn.preprocessing import RobustScaler
        return RobustScaler().fit_transform(X_imp)
    if scaler_type == "none":
        return X_imp.copy()
    from sklearn.preprocessing import StandardScaler
    return StandardScaler().fit_transform(X_imp)


def _detect_elbow(ks: list, inertias: list) -> int:
    if len(ks) < 3:
        return ks[0]
    mn, mx = min(inertias), max(inertias)
    if mx == mn:
        return ks[0]
    norm  = [(x - mn) / (mx - mn) for x in inertias]
    p1    = np.array([ks[0],  norm[0]])
    p2    = np.array([ks[-1], norm[-1]])
    d_hat = (p2 - p1) / (np.linalg.norm(p2 - p1) + 1e-12)
    dists = [abs(np.cross(d_hat, np.array([ks[i], norm[i]]) - p1))
             for i in range(len(ks))]
    return ks[int(np.argmax(dists))]


def _run_k_suggestion(platform, profile, db, schema, table,
                      features, sample_size, scaler_type):
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.impute import SimpleImputer

    n = min(sample_size or 5_000, 5_000)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp  = Path(tmpdir)
        tkey = f"{db.upper()}__{schema.upper()}__{table.upper()}"
        (tmp / f"{tkey}.json").write_text(json.dumps(profile))
        eng   = ClusteringEngine(platform, tmp)
        X_raw = eng._fetch_sample(db, schema, table, features, n)

    if len(X_raw) < 10:
        raise ValueError(f"Only {len(X_raw)} rows — too few for k suggestion.")

    X_imp = SimpleImputer(strategy="mean").fit_transform(X_raw)
    X_sc  = _apply_scaler(scaler_type, X_imp)
    max_k = min(12, max(3, len(X_sc) // 10))

    ks, sil_scores, inertias = [], [], []
    for k in range(2, max_k + 1):
        km     = KMeans(n_clusters=k, n_init=3, max_iter=100, random_state=42)
        labels = km.fit_predict(X_sc)
        ks.append(k)
        inertias.append(float(km.inertia_))
        try:
            sil_scores.append(float(
                silhouette_score(X_sc, labels, sample_size=min(2_000, len(X_sc)))
            ))
        except Exception:
            sil_scores.append(0.0)

    best_k  = ks[int(np.argmax(sil_scores))]
    elbow_k = _detect_elbow(ks, inertias)
    return {"ks": ks, "sil": sil_scores, "inertia": inertias,
            "best_k": best_k, "elbow_k": elbow_k, "n_samples": len(X_sc)}


def _mk_set_k(k: int):
    def _cb():
        st.session_state["c_p_n_clusters"] = k
    return _cb


# ── Chart helpers ─────────────────────────────────────────────────────────────

def _k_chart(sug: dict) -> go.Figure:
    ks, sil, inertia = sug["ks"], sug["sil"], sug["inertia"]
    best_k, elbow_k  = sug["best_k"], sug["elbow_k"]
    mn, mx    = min(inertia), max(inertia)
    inertia_n = [(x - mn) / (mx - mn + 1e-12) for x in inertia]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.55, 0.45],
        subplot_titles=["Silhouette Score  (higher = better)",
                        "Inertia / WCSS  (look for the elbow)"],
        vertical_spacing=0.14,
    )
    fig.add_trace(go.Scatter(x=ks, y=sil, mode="lines+markers",
        line=dict(color="#58a6ff", width=2), marker=dict(size=7),
        name="Silhouette"), row=1, col=1)
    fig.add_trace(go.Scatter(x=[best_k], y=[sil[ks.index(best_k)]],
        mode="markers", marker=dict(color="#3fb950", size=14, symbol="star"),
        name=f"Best k={best_k}"), row=1, col=1)
    fig.add_trace(go.Scatter(x=ks, y=inertia_n, mode="lines+markers",
        line=dict(color="#d29922", width=2, dash="dash"), marker=dict(size=6),
        name="Inertia (norm.)"), row=2, col=1)
    if elbow_k != best_k:
        fig.add_trace(go.Scatter(
            x=[elbow_k], y=[inertia_n[ks.index(elbow_k)]],
            mode="markers", marker=dict(color="#ff7b72", size=11, symbol="diamond"),
            name=f"Elbow k={elbow_k}"), row=2, col=1)
    fig.update_layout(height=400, margin=dict(t=34, b=8, l=0, r=0),
                      legend=dict(orientation="h", y=-0.08, x=0))
    fig.update_xaxes(title_text="k (number of clusters)", row=2, col=1, dtick=1)
    fig.update_yaxes(title_text="Score",         row=1, col=1)
    fig.update_yaxes(title_text="Norm. inertia", row=2, col=1)
    return fig


def _scatter_chart(scatter_info: dict, height: int = 480) -> go.Figure:
    fig = go.Figure()
    for ds in scatter_info.get("datasets", []):
        pts = ds.get("points", [])
        if pts:
            fig.add_trace(go.Scatter(
                x=[p["x"] for p in pts], y=[p["y"] for p in pts],
                mode="markers", name=ds.get("label", ""),
                marker=dict(color=ds.get("color", "#58a6ff"), size=4, opacity=0.7),
            ))
    fig.update_layout(
        title="Cluster Scatter  (PCA 2-D projection)",
        xaxis_title=scatter_info.get("x_label", "PC1"),
        yaxis_title=scatter_info.get("y_label", "PC2"),
        height=height, margin=dict(t=44, b=0),
        legend=dict(orientation="h", y=-0.06),
    )
    return fig


def _size_chart(cluster_stats: list, scatter_data: list) -> go.Figure:
    color_map = {ds["cluster"]: ds.get("color", "#58a6ff") for ds in scatter_data}
    cs_s   = sorted(cluster_stats, key=lambda x: x.get("cluster", 0))
    labels = [f"Cluster {cs['cluster']}" for cs in cs_s]
    sizes  = [cs["size"] for cs in cs_s]
    pcts   = [cs["pct"]  for cs in cs_s]
    colors = [color_map.get(cs["cluster"], "#58a6ff") for cs in cs_s]
    fig = go.Figure(go.Bar(
        y=labels[::-1], x=sizes[::-1], orientation="h",
        text=[f"{p:.1f}%" for p in pcts[::-1]],
        textposition="inside", marker_color=colors[::-1],
    ))
    fig.update_layout(title="Cluster Sizes",
                      height=max(130, 38 * len(labels)),
                      margin=dict(t=36, b=8, l=0, r=8),
                      xaxis_title="Rows", showlegend=False)
    return fig


# ══════════════════════════════ PAGE ═════════════════════════════════════════

st.title("Clustering")
st.caption("Unsupervised ML clustering with preprocessing, optimal-k analysis, and tabbed results.")

try:
    profiles = persistence.list_profiles(session)
except Exception as e:
    st.error(f"Could not load profiles: {e}")
    st.stop()

if not profiles:
    st.info("No profiles found. Go to **Profile** first.")
    st.stop()

options    = [f"{p.get('database')}.{p.get('schema')}.{p.get('table')}" for p in profiles]
sel_key_ss = (
    f"{st.session_state.get('sel_db')}.{st.session_state.get('sel_schema')}"
    f".{st.session_state.get('sel_table')}"
    if st.session_state.get("sel_db") else None
)
default_idx = options.index(sel_key_ss) if sel_key_ss and sel_key_ss in options else 0
chosen      = st.selectbox("Table", options, index=default_idx, key="clust_tbl")
parts       = chosen.split(".", 2)
db, schema, table = parts
profile      = profiles[options.index(chosen)]
numeric_cols = [c["name"] for c in profile.get("columns", []) if c.get("mean") is not None]

if not numeric_cols:
    st.warning("This table has no numeric columns.")
    st.stop()

clust_data = persistence.load_result(session, "CLUSTERING_RESULTS", db, schema, table)

# ── Row 1: Model + Scaler + Data Volume ──────────────────────────────────────

st.divider()
col_model, col_scaler, col_volume = st.columns([2, 2, 2], gap="medium")

with col_model:
    st.markdown("**Model**")
    model_options = list(CLUSTERING_MODELS.keys())
    model_labels  = [CLUSTERING_MODELS[m]["label"] for m in model_options]
    saved_model   = (clust_data or {}).get("config", {}).get("model_name", "kmeans")
    model_idx     = model_options.index(saved_model) if saved_model in model_options else 0
    model_label   = st.selectbox("Model", model_labels, index=model_idx,
                                 key="c_model", label_visibility="collapsed")
    model_name    = model_options[model_labels.index(model_label)]
    st.caption(CLUSTERING_MODELS[model_name]["desc"])

with col_scaler:
    st.markdown("**Preprocessing**")
    saved_scaler = (clust_data or {}).get("config", {}).get("scaler_type", "standard")
    scaler_idx   = list(SCALERS.keys()).index(saved_scaler) if saved_scaler in SCALERS else 0
    scaler_key   = st.selectbox(
        "Normalization", list(SCALERS.keys()),
        format_func=lambda k: SCALERS[k],
        index=scaler_idx, key="c_scaler", label_visibility="collapsed",
    )
    st.caption(SCALER_DESC[scaler_key])

with col_volume:
    st.markdown("**Data Volume**")
    row_count   = profile.get("row_count")
    full_dataset = st.toggle(
        "Use full dataset", value=False, key="c_full_dataset",
        help="Fetch all rows without LIMIT. May be slow on large tables.",
    )
    if full_dataset:
        if row_count and row_count > 500_000:
            st.warning(f"{row_count:,} rows — this may be slow.")
        elif row_count:
            st.info(f"Full dataset: {row_count:,} rows.")
        sample_size = None
    else:
        sample_size = st.slider(
            "Sample size", 1_000, 500_000, 10_000, 1_000, key="c_sample",
            label_visibility="collapsed",
        )
        if row_count and sample_size >= row_count:
            st.info(f"Covers all {row_count:,} rows.")
        else:
            st.caption(f"Sample: {sample_size:,} rows")

# ── Row 2: Parameters (horizontal) ───────────────────────────────────────────

saved_params = (clust_data or {}).get("config", {}).get("params", {})
param_values: dict = {}
param_defs   = CLUSTERING_MODELS[model_name]["params"]

# For k-based models reserve the last column slot for suggest-k
n_param_cols = len(param_defs)
extra_col    = model_name in K_BASED_MODELS
total_cols   = n_param_cols + (1 if extra_col else 0)
param_cols   = st.columns(min(total_cols, 5), gap="medium")

for i, pdef in enumerate(param_defs):
    pid     = pdef["id"]
    default = saved_params.get(pid, pdef["default"])
    with param_cols[i % min(total_cols, 5)]:
        if pdef["type"] == "int":
            param_values[pid] = st.number_input(
                pdef["label"], min_value=pdef["min"], max_value=pdef["max"],
                value=int(default), help=pdef.get("hint"), key=f"c_p_{pid}")
        elif pdef["type"] == "float":
            param_values[pid] = st.number_input(
                pdef["label"], min_value=float(pdef["min"]), max_value=float(pdef["max"]),
                value=float(default), step=float(pdef.get("step", 0.1)),
                help=pdef.get("hint"), key=f"c_p_{pid}")
        elif pdef["type"] == "select":
            opts   = pdef["options"]
            sel_ix = opts.index(str(default)) if str(default) in opts else 0
            param_values[pid] = st.selectbox(
                pdef["label"], opts, index=sel_ix,
                help=pdef.get("hint"), key=f"c_p_{pid}")

# ── Suggest-k controls in the extra column ────────────────────────────────────

# sug_key must be computed after selected_features is known; we precompute it
# here using numeric_cols and then recompute below once we know the selection.

if model_name in K_BASED_MODELS:
    with param_cols[min(n_param_cols, 4)]:
        st.markdown("**Optimal k**")
        suggest_clicked = st.button(
            "Suggest k", key="c_suggest_k",
            help="Tries k=2..12 and picks the best silhouette score. See Optimal k tab for the chart.",
        )

# ── Row 3: Feature Selection ──────────────────────────────────────────────────

st.markdown("**Feature Selection**")
saved_features    = (clust_data or {}).get("config", {}).get("feature_ids") or numeric_cols[:10]
selected_features = st.multiselect(
    "Columns", numeric_cols,
    default=[f for f in saved_features if f in numeric_cols],
    key="c_features",
    label_visibility="collapsed",
)

# sug_key is keyed on table + selected features so stale suggestions auto-expire.
sug_key = (f"_k_sug_{db}__{schema}__{table}__"
           f"{'_'.join(sorted(selected_features))}")

# Restore k-suggestion from DB if not already in session state for this selection
if sug_key not in st.session_state and clust_data:
    stored_sug   = clust_data.get("k_suggestion")
    stored_feats = clust_data.get("k_suggestion_features", [])
    if stored_sug and sorted(stored_feats) == sorted(selected_features):
        st.session_state[sug_key] = stored_sug

# ── Process suggest-k click (after features are known) ───────────────────────

if model_name in K_BASED_MODELS and suggest_clicked:
    if len(selected_features) < 2:
        st.warning("Select at least 2 features before suggesting k.")
    else:
        with st.spinner("Analyzing optimal k (k=2..12) ..."):
            try:
                sug_result = _run_k_suggestion(
                    platform, profile, db, schema, table,
                    selected_features, sample_size, scaler_key,
                )
                st.session_state[sug_key] = sug_result
                try:
                    persistence.merge_result(
                        session, "CLUSTERING_RESULTS", db, schema, table,
                        {"k_suggestion": sug_result,
                         "k_suggestion_features": selected_features},
                    )
                except Exception:
                    pass
            except Exception as exc:
                st.error(f"k suggestion failed: {exc}")

# Show apply buttons whenever a suggestion for the current selection exists
sug = st.session_state.get(sug_key)
if model_name in K_BASED_MODELS and sug:
    best_k, elbow_k = sug["best_k"], sug["elbow_k"]
    info_col, btn1_col, btn2_col = st.columns([3, 1, 1], gap="small")
    with info_col:
        st.caption(
            f"Suggestion: **k={best_k}** (silhouette)"
            + (f" / **k={elbow_k}** (elbow)" if elbow_k != best_k else "")
            + f" — {sug['n_samples']:,} rows analysed. See **Optimal k** tab for the chart."
        )
    with btn1_col:
        st.button(f"Apply k={best_k}", on_click=_mk_set_k(best_k),
                  key="c_apply_k_sil", use_container_width=True)
    if elbow_k != best_k:
        with btn2_col:
            st.button(f"Apply k={elbow_k}", on_click=_mk_set_k(elbow_k),
                      key="c_apply_k_elb", use_container_width=True)

# ── Feature Engineering Studio ────────────────────────────────────────────────

_ENG_TYPE_LABELS = {
    "original":     "Original",
    "log1p":        "Log Transform",
    "quartile_bin": "Quartile Bin",
    "outlier_flag": "Outlier Flag",
    "ratio":        "Ratio",
    "freq_enc":     "Freq. Encoding",
    "ordinal_enc":  "Ordinal Encoding",
    "binary_enc":   "Binary (0/1)",
}
# Keys scoped to table + feature selection
_eng_ctr_key = f"_eng_ctr_{db}__{schema}__{table}"
_eng_sug_key = f"_eng_sugs_{db}__{schema}__{table}__{'_'.join(sorted(selected_features))}"
_eng_ids_key = f"_eng_ids_{db}__{schema}__{table}__{'_'.join(sorted(selected_features))}"
_eng_ai_key  = f"_eng_ai_{db}__{schema}__{table}__{'_'.join(sorted(selected_features))}"

# Categorical columns available for encoding (string, 2-20 distinct values, <50% null)
_profile_col_dict = {c["name"]: c for c in profile.get("columns", [])}
_cat_cols_avail = [
    c["name"] for c in profile.get("columns", [])
    if c.get("min_length") is not None
    and 2 <= c.get("distinct_count", 0) <= 20
    and c.get("null_rate", 0) < 0.5
]

with st.expander("Feature Engineering Studio", expanded=False):
    st.caption(
        "Add derived features — log transforms, quartile bins, outlier flags, ratios — "
        "computed from your selected columns before the model runs."
    )
    _eng_c1, _eng_c2, _eng_c3 = st.columns([1, 1, 2])
    with _eng_c1:
        _analyze_clicked = st.button(
            "Analyze Features", key="c_eng_analyze",
            disabled=len(selected_features) < 1,
            help="Fetch a sample and compute available transforms for the selected columns.",
        )
    with _eng_c2:
        _ai_eng_clicked = st.button(
            "Ask AI", key="c_eng_ai_btn",
            disabled=_eng_sug_key not in st.session_state,
            help="Use AI to recommend the most impactful transforms for clustering.",
        )
    with _eng_c3:
        if st.session_state.get(_eng_ids_key):
            _orig_set = set(numeric_cols + _cat_cols_avail)
            _n_orig   = sum(1 for i in st.session_state[_eng_ids_key] if i in _orig_set)
            _n_drv    = len(st.session_state[_eng_ids_key]) - _n_orig
            st.caption(
                f"Active: **{len(st.session_state[_eng_ids_key])} features** "
                f"({_n_orig} original + {_n_drv} derived)"
            )

    # Analyze button handler
    if _analyze_clicked:
        with st.spinner("Fetching sample and computing transforms …"):
            try:
                with tempfile.TemporaryDirectory() as _tmpdir:
                    _tmp = Path(_tmpdir)
                    _tk  = f"{db.upper()}__{schema.upper()}__{table.upper()}"
                    (_tmp / f"{_tk}.json").write_text(json.dumps(profile))
                    _eng_engine = ClusteringEngine(platform, _tmp)
                    _sugs = _eng_engine.suggest_features(
                        db, schema, table, selected_features,
                        min(sample_size or 5_000, 5_000),
                    )
                st.session_state[_eng_sug_key] = _sugs
                # Default: auto-selected numeric + freq/binary for categorical columns
                _def_num = [s["id"] for s in _sugs if s.get("selected", False)]
                _def_cat = []
                for _cc in _cat_cols_avail:
                    _dc = _profile_col_dict[_cc].get("distinct_count", 0)
                    _def_cat.append(f"{_cc}_freq")
                    if _dc == 2:
                        _def_cat.append(f"{_cc}_01")
                st.session_state[_eng_ids_key] = _def_num + _def_cat
                # Increment counter so data_editor resets to new defaults
                st.session_state[_eng_ctr_key] = st.session_state.get(_eng_ctr_key, 0) + 1
            except Exception as _exc:
                st.error(f"Analysis failed: {_exc}")

    # AI recommendation handler
    if _ai_eng_clicked and _eng_sug_key in st.session_state:
        with st.spinner("Generating AI recommendations …"):
            _ai_rec = cortex.suggest_feature_engineering(
                session, profile, st.session_state[_eng_sug_key]
            )
            st.session_state[_eng_ai_key] = _ai_rec

    # Show suggestions UI when analysis has been run
    if _eng_sug_key in st.session_state:
        _sugs = st.session_state[_eng_sug_key]

        if st.session_state.get(_eng_ai_key):
            st.info(st.session_state[_eng_ai_key])

        _current_ids = set(st.session_state.get(
            _eng_ids_key,
            [s["id"] for s in _sugs if s.get("selected", False)],
        ))

        # Build unified feature table: numeric transforms + categorical encodings
        _table_rows = []
        for _s in _sugs:
            _table_rows.append({
                "Include":     _s["id"] in _current_ids,
                "Feature":     _s["id"],
                "Type":        _ENG_TYPE_LABELS.get(_s["type"], _s["type"]),
                "Source":      ", ".join(_s.get("source_cols", [])),
                "Description": _s["description"],
            })
        for _cc in _cat_cols_avail:
            _cdef = _profile_col_dict.get(_cc, {})
            _dc   = _cdef.get("distinct_count", 0)
            _fid_freq = f"{_cc}_freq"
            _table_rows.append({
                "Include":     _fid_freq in _current_ids,
                "Feature":     _fid_freq,
                "Type":        "Freq. Encoding",
                "Source":      _cc,
                "Description": f"Value frequency in {_cc}  ({_dc} distinct values)",
            })
            _fid_ord = f"{_cc}_ord"
            _table_rows.append({
                "Include":     _fid_ord in _current_ids,
                "Feature":     _fid_ord,
                "Type":        "Ordinal Encoding",
                "Source":      _cc,
                "Description": f"Sorted-rank encoding of {_cc}  (0 to {_dc - 1})",
            })
            if _dc == 2:
                _fid_01 = f"{_cc}_01"
                _table_rows.append({
                    "Include":     _fid_01 in _current_ids,
                    "Feature":     _fid_01,
                    "Type":        "Binary (0/1)",
                    "Source":      _cc,
                    "Description": f"Binary indicator for {_cc}",
                })

        _de_ctr    = st.session_state.get(_eng_ctr_key, 0)
        _edited_df = st.data_editor(
            pd.DataFrame(_table_rows),
            use_container_width=True,
            hide_index=True,
            disabled=["Feature", "Type", "Source", "Description"],
            column_config={
                "Include":     st.column_config.CheckboxColumn("Include",
                                   help="Push this feature to the clustering model"),
                "Feature":     st.column_config.TextColumn("Feature",     width="medium"),
                "Type":        st.column_config.TextColumn("Type",        width="small"),
                "Source":      st.column_config.TextColumn("Source",      width="small"),
                "Description": st.column_config.TextColumn("Description", width="large"),
            },
            key=f"c_eng_de_{_de_ctr}",
        )
        _new_ids = _edited_df[_edited_df["Include"]]["Feature"].tolist()
        st.session_state[_eng_ids_key] = _new_ids

        if not _new_ids:
            st.warning("No features selected — clustering will use all original columns.")
    else:
        st.info(
            "Click **Analyze Features** to see available transforms for the selected columns."
        )


# ── Run button ────────────────────────────────────────────────────────────────

st.divider()
run_col, status_col = st.columns([1, 3])
with run_col:
    run_clicked = st.button(
        "Run Clustering", type="primary",
        disabled=len(selected_features) < 2,
        key="c_run", use_container_width=True,
    )
if run_clicked:
    with st.spinner("Running clustering ..."):
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp  = Path(tmpdir)
                tkey = f"{db.upper()}__{schema.upper()}__{table.upper()}"
                (tmp / f"{tkey}.json").write_text(json.dumps(profile))
                engine = ClusteringEngine(platform, tmp)
                # Split engineered IDs into numeric and categorical
                _all_eng_ids = st.session_state.get(_eng_ids_key) or []
                _cat_sfxs    = ("_freq", "_ord", "_01")
                _run_num_ids = [i for i in _all_eng_ids if not i.endswith(_cat_sfxs)]
                _run_cat_ids = [i for i in _all_eng_ids if i.endswith(_cat_sfxs)]
                if not _run_num_ids:
                    _run_num_ids = selected_features
                result = engine.run(
                    db=db, schema=schema, table=table,
                    model_name=model_name, params=param_values,
                    columns=selected_features,
                    feature_ids=_run_num_ids,
                    cat_feature_ids=_run_cat_ids or None,
                    sample_size=sample_size, scaler_type=scaler_key,
                )
            result["config"] = {
                "model_name":   model_name, "params": param_values,
                "feature_ids":  selected_features, "sample_size": sample_size,
                "scaler_type":  scaler_key,
                "eng_num_ids":  _run_num_ids,
                "eng_cat_ids":  _run_cat_ids,
            }
            # Carry existing k-suggestion into the saved result so it survives the write
            if sug := st.session_state.get(sug_key):
                result["k_suggestion"]          = sug
                result["k_suggestion_features"] = selected_features
            # Carry existing AI insights if present for this table
            _ins_key = f"c_ins_{db}__{schema}__{table}"
            if _ins := st.session_state.get(_ins_key):
                result["ai_insights"] = _ins
            _viz_key = f"c_viz_{db}__{schema}__{table}"
            if _viz := st.session_state.get(_viz_key):
                result["viz_insights"] = _viz
            persistence.save_result(session, "CLUSTERING_RESULTS", db, schema, table, result)
            clust_data = result
            volume_label = "full dataset" if full_dataset else f"{result.get('sample_size',0):,} rows"
            with status_col:
                st.success(
                    f"{result.get('n_clusters','?')} clusters  |  "
                    f"{volume_label}  |  {SCALERS[scaler_key]}"
                )
            st.rerun()
        except Exception as exc:
            st.error(f"Clustering failed: {exc}")

# ══════════════════════════ RESULTS TABS ═════════════════════════════════════

st.divider()
tab_results, tab_ksug, tab_data, tab_viz, tab_anomaly, tab_dimred = st.tabs(
    ["Results", "Optimal k", "Sample Data", "Visual Insights",
     "Anomaly Detection", "Dim. Reduction"]
)

# ── Results tab ───────────────────────────────────────────────────────────────

with tab_results:
    if not clust_data or not clust_data.get("scatter"):
        st.info("Configure the model above and click **Run Clustering** to see results here.")
    else:
        scatter_info  = clust_data["scatter"]
        scatter_data  = scatter_info.get("datasets", [])
        cluster_stats = clust_data.get("cluster_stats", [])
        metrics       = clust_data.get("metrics", {})
        feat_names    = clust_data.get("columns_used", selected_features)
        used_scaler   = clust_data.get("scaler_type", "standard")

        # Metrics row
        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("Clusters", clust_data.get("n_clusters", "?"))
        mc2.metric("Silhouette", f"{metrics.get('silhouette_score', 0):.3f}",
                   help="Range -1 to 1. Closer to 1 = well-separated clusters.")
        mc3.metric("Davies-Bouldin", f"{metrics.get('davies_bouldin_score', 0):.3f}",
                   help="Lower is better.")
        mc4.metric("Scaler", SCALERS.get(used_scaler, used_scaler))

        # Full-width scatter
        st.plotly_chart(_scatter_chart(scatter_info, height=500),
                        use_container_width=True)

        # Cluster size bars + centroid table
        if cluster_stats:
            left, right = st.columns([1, 2], gap="medium")
            with left:
                st.plotly_chart(_size_chart(cluster_stats, scatter_data),
                                use_container_width=True)
            with right:
                st.subheader("Centroids  (pre-scale averages)")
                rows = []
                for cs in cluster_stats:
                    centroid = cs.get("centroid", {})
                    row = {
                        "Cluster": f"Cluster {cs.get('cluster','?')}",
                        "Size":    cs.get("size", 0),
                        "% Total": f"{cs.get('pct',0):.1f}%",
                    }
                    for fn in feat_names[:8]:
                        if fn in centroid:
                            row[fn[:12]] = round(centroid[fn], 3)
                    rows.append(row)
                st.dataframe(pd.DataFrame(rows),
                             use_container_width=True, hide_index=True)

        # AI Insights
        st.divider()
        st.subheader("AI Insights")
        _ins_key = f"c_ins_{db}__{schema}__{table}"
        # Restore from DB on first visit for this table
        if _ins_key not in st.session_state:
            st.session_state[_ins_key] = (clust_data or {}).get("ai_insights")
        if st.button("Generate Cluster Insights", key="c_insights"):
            with st.spinner("Generating ..."):
                insights = cortex.explain_clusters(session, cluster_stats, feat_names)
            st.session_state[_ins_key] = insights
            try:
                persistence.merge_result(
                    session, "CLUSTERING_RESULTS", db, schema, table,
                    {"ai_insights": insights},
                )
            except Exception:
                pass
        if st.session_state.get(_ins_key):
            st.markdown(st.session_state[_ins_key])

# ── Optimal k tab ─────────────────────────────────────────────────────────────

with tab_ksug:
    if model_name not in K_BASED_MODELS:
        st.info("Optimal-k analysis applies only to K-Means and Bisecting K-Means.")
    else:
        sug = st.session_state.get(sug_key)
        if not sug:
            st.info(
                "Click **Suggest k** in the parameters row above to run the analysis. "
                "DataLens will try k=2 through k=12 and score each with the silhouette coefficient."
            )
        else:
            best_k, elbow_k = sug["best_k"], sug["elbow_k"]
            h1, h2 = st.columns(2)
            h1.metric("Recommended k  (silhouette)", best_k)
            h2.metric("Elbow k  (inertia)", elbow_k)
            st.caption(
                f"Analysed {sug['n_samples']:,} sample rows — "
                f"{SCALERS[scaler_key]} scaling.  "
                "Use the **Apply** buttons above the feature selector to set k."
            )
            st.plotly_chart(_k_chart(sug), use_container_width=True)

            with st.expander("How to read this chart"):
                st.markdown("""
**Silhouette score** (top) — measures how similar each point is to its own cluster vs its
nearest neighbour. Range -1 to 1; pick the k where the score peaks.

**Inertia / WCSS** (bottom) — sum of squared distances from each point to its centroid.
Always decreases as k grows; the "elbow" is where improvement flattens sharply.

**When they disagree** — prefer the silhouette pick. Use the elbow as a secondary check
if you want a more compact partition with fewer, broader clusters.
""")

# ── Sample Data tab ───────────────────────────────────────────────────────────

with tab_data:
    if not clust_data:
        st.info("Run clustering first to inspect the data fed to the model.")
    else:
        data_sample = clust_data.get("data_sample", [])
        feat_names  = clust_data.get("columns_used", [])
        used_scaler = clust_data.get("scaler_type", "standard")

        if not data_sample:
            st.warning(
                "No sample captured. Re-run clustering to include the data snapshot "
                "(requires the latest engine version)."
            )
        else:
            n_rows = len(data_sample)
            st.caption(
                f"**{n_rows} rows** (stratified sample, up to 500)  —  "
                f"values are **imputed but pre-scale** (before {SCALERS[used_scaler].lower()}).  "
                "The model trained on the scaled version of these values."
            )
            df = pd.DataFrame(data_sample)
            df = df[["cluster"] + [c for c in df.columns if c != "cluster"]]

            all_clusters = sorted(df["cluster"].unique().tolist())
            sel_clusters = st.multiselect(
                "Filter by cluster", options=all_clusters, default=all_clusters,
                key="dt_filter", format_func=lambda c: f"Cluster {c}",
            )
            filtered = df[df["cluster"].isin(sel_clusters)] if sel_clusters else df
            st.dataframe(filtered.sort_values("cluster").reset_index(drop=True),
                         use_container_width=True, hide_index=True, height=440)
            st.caption(
                f"Showing {len(filtered):,} / {n_rows} rows  |  "
                f"{len(feat_names)} features  |  "
                f"Scaler: {SCALERS.get(used_scaler, used_scaler)}"
            )

# ── Visual Insights tab ───────────────────────────────────────────────────────

with tab_viz:
    if not clust_data or not clust_data.get("data_sample"):
        st.info("Run clustering first to generate visual insights across features.")
    else:
        _vi_sample      = clust_data.get("data_sample", [])
        _vi_feat_names  = clust_data.get("columns_used", selected_features)
        _vi_scatter     = clust_data.get("scatter", {}).get("datasets", [])
        _vi_color_map   = {ds["cluster"]: ds.get("color", "#58a6ff") for ds in _vi_scatter}
        _vi_clust_stats = clust_data.get("cluster_stats", [])

        if not _vi_sample:
            st.warning(
                "No sample data captured. Re-run clustering to include the data snapshot."
            )
        else:
            _vi_df        = pd.DataFrame(_vi_sample)
            _vi_clusters  = sorted(_vi_df["cluster"].unique().tolist())
            _vi_feat_list = [f for f in _vi_feat_names if f in _vi_df.columns]

            # ── Feature distribution box plots (2 per row) ────────────────────

            st.subheader("Feature Distributions by Cluster")
            st.caption(
                "Box plots show median, IQR, and outliers per cluster. "
                "Greater separation between boxes = stronger cluster differentiation."
            )

            for _i in range(0, len(_vi_feat_list), 2):
                _chunk    = _vi_feat_list[_i:_i + 2]
                _pcols    = st.columns(len(_chunk))
                for _j, _feat in enumerate(_chunk):
                    _fig = go.Figure()
                    for _cl in _vi_clusters:
                        _vals = _vi_df[_vi_df["cluster"] == _cl][_feat].dropna().tolist()
                        _fig.add_trace(go.Box(
                            y=_vals, name=f"Cluster {_cl}",
                            marker_color=_vi_color_map.get(int(_cl), "#58a6ff"),
                            boxmean=True, boxpoints="outliers",
                        ))
                    _fig.update_layout(
                        title=_feat, height=300,
                        margin=dict(t=36, b=8, l=0, r=0),
                        showlegend=(_i == 0 and _j == 0),
                        legend=dict(orientation="h", y=-0.12),
                    )
                    with _pcols[_j]:
                        st.plotly_chart(_fig, use_container_width=True)

            # ── Feature divergence ranking ────────────────────────────────────

            st.subheader("Feature Divergence Ranking")
            st.caption(
                "Inter-cluster mean spread divided by overall standard deviation. "
                "Higher = this feature separates clusters more strongly."
            )

            _divergence = []
            for _feat in _vi_feat_list:
                _std = _vi_df[_feat].std()
                if not _std or pd.isna(_std):
                    continue
                _cmeans = _vi_df.groupby("cluster")[_feat].mean()
                _spread = float(_cmeans.max() - _cmeans.min())
                _divergence.append({
                    "Feature":    _feat,
                    "Divergence": round(_spread / float(_std), 3),
                })

            if _divergence:
                _div_df = (
                    pd.DataFrame(_divergence)
                    .sort_values("Divergence", ascending=True)
                )
                _fig_div = go.Figure(go.Bar(
                    x=_div_df["Divergence"], y=_div_df["Feature"],
                    orientation="h", marker_color="#58a6ff",
                    text=_div_df["Divergence"].apply(lambda v: f"{v:.3f}"),
                    textposition="outside",
                ))
                _fig_div.update_layout(
                    title="Feature Divergence Score",
                    height=max(200, 32 * len(_div_df)),
                    margin=dict(t=36, b=8, l=0, r=80),
                    xaxis_title="Divergence score",
                )
                st.plotly_chart(_fig_div, use_container_width=True)

            # ── AI explanation ────────────────────────────────────────────────

            st.divider()
            st.subheader("AI Explanation")
            _viz_ins_key = f"c_viz_{db}__{schema}__{table}"
            if _viz_ins_key not in st.session_state:
                st.session_state[_viz_ins_key] = (clust_data or {}).get("viz_insights")
            if st.button("Explain Visual Patterns", key="c_viz_insights"):
                with st.spinner("Generating explanation ..."):
                    try:
                        _viz_text = cortex.explain_visual_insights(
                            session,
                            _vi_clust_stats,
                            _vi_feat_list,
                            _divergence,
                        )
                        st.session_state[_viz_ins_key] = _viz_text
                        try:
                            persistence.merge_result(
                                session, "CLUSTERING_RESULTS", db, schema, table,
                                {"viz_insights": _viz_text},
                            )
                        except Exception:
                            pass
                    except Exception as _exc:
                        st.error(f"AI explanation failed: {_exc}")
            if st.session_state.get(_viz_ins_key):
                st.markdown(st.session_state[_viz_ins_key])

# ── Anomaly Detection tab ─────────────────────────────────────────────────────

with tab_anomaly:
    st.caption(
        "Detects statistically unusual rows using Isolation Forest or Local Outlier Factor. "
        "Anomalies are highlighted on a PCA 2-D scatter."
    )

    _an_method_key = f"an_method_{db}__{schema}__{table}"
    _an_data_key   = f"an_data_{db}__{schema}__{table}"

    _an_c1, _an_c2, _an_c3, _an_c4 = st.columns([2, 2, 2, 1], gap="medium")
    with _an_c1:
        _an_method = st.selectbox(
            "Method",
            ["isolation_forest", "lof"],
            format_func=lambda m: "Isolation Forest" if m == "isolation_forest" else "Local Outlier Factor",
            key="an_method",
        )
    with _an_c2:
        _an_contamination = st.slider(
            "Expected anomaly rate", 0.01, 0.30, 0.05, 0.01, key="an_contam",
            help="Fraction of rows expected to be anomalous.",
        )
    with _an_c3:
        _an_features = st.multiselect(
            "Columns", numeric_cols,
            default=numeric_cols[:min(10, len(numeric_cols))],
            key="an_features",
            label_visibility="visible",
        )
    with _an_c4:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        _an_run = st.button("Run", type="primary", key="an_run",
                            disabled=len(_an_features) < 1,
                            use_container_width=True)

    if _an_run and _an_features:
        with st.spinner("Detecting anomalies …"):
            try:
                with tempfile.TemporaryDirectory() as _tmpdir:
                    _tmp = Path(_tmpdir)
                    _tk  = f"{db.upper()}__{schema.upper()}__{table.upper()}"
                    (_tmp / f"{_tk}.json").write_text(json.dumps(profile))
                    _an_engine = ClusteringEngine(platform, _tmp)
                    _an_result = _an_engine.run_anomaly_detection(
                        db, schema, table,
                        columns=_an_features,
                        method=_an_method,
                        contamination=_an_contamination,
                        sample_size=sample_size or 10_000,
                        scaler_type=scaler_key,
                    )
                st.session_state[_an_data_key] = _an_result
                try:
                    persistence.merge_result(
                        session, "CLUSTERING_RESULTS", db, schema, table,
                        {"anomaly_result": _an_result},
                    )
                except Exception:
                    pass
            except Exception as _exc:
                st.error(f"Anomaly detection failed: {_exc}")

    # Restore from DB on first visit
    if _an_data_key not in st.session_state:
        st.session_state[_an_data_key] = (clust_data or {}).get("anomaly_result")

    _an_data = st.session_state.get(_an_data_key)
    if not _an_data:
        st.info("Configure and click **Run** to detect anomalies.")
    else:
        _an_sc = _an_data.get("scatter", {})
        _an_m1, _an_m2, _an_m3 = st.columns(3)
        _an_m1.metric("Rows analysed", f"{_an_data.get('sample_size', 0):,}")
        _an_m2.metric("Anomalies found", f"{_an_data.get('n_anomalies', 0):,}")
        _an_m3.metric("Anomaly rate", f"{_an_data.get('anomaly_pct', 0):.1f}%")

        # Scatter
        _an_fig = go.Figure()
        if _an_sc.get("normal"):
            _an_fig.add_trace(go.Scatter(
                x=[p["x"] for p in _an_sc["normal"]],
                y=[p["y"] for p in _an_sc["normal"]],
                mode="markers", name="Normal",
                marker=dict(color="#58a6ff", size=4, opacity=0.5),
            ))
        if _an_sc.get("anomalies"):
            _an_fig.add_trace(go.Scatter(
                x=[p["x"] for p in _an_sc["anomalies"]],
                y=[p["y"] for p in _an_sc["anomalies"]],
                mode="markers", name="Anomaly",
                marker=dict(color="#f85149", size=7, opacity=0.85,
                            symbol="x"),
            ))
        _an_fig.update_layout(
            title="Anomaly Detection  (PCA 2-D projection)",
            xaxis_title=_an_sc.get("x_label", "PC1"),
            yaxis_title=_an_sc.get("y_label", "PC2"),
            height=480, margin=dict(t=44, b=0),
            legend=dict(orientation="h", y=-0.06),
        )
        st.plotly_chart(_an_fig, use_container_width=True)

        # Top anomalies table
        _top = _an_data.get("top_anomalies", [])
        if _top:
            st.subheader("Top Anomalies")
            st.caption("Rows with the highest anomaly scores, with their original feature values.")
            _top_df = pd.DataFrame(_top)
            cols_order = ["anomaly_score"] + [c for c in _top_df.columns if c != "anomaly_score"]
            st.dataframe(_top_df[cols_order], use_container_width=True, hide_index=True, height=360)

# ── Dimensionality Reduction tab ──────────────────────────────────────────────

with tab_dimred:
    st.caption(
        "Project high-dimensional data into 2-D for visual exploration. "
        "If clustering has been run the scatter is coloured by cluster."
    )

    _dr_data_key = f"dr_data_{db}__{schema}__{table}"

    _dr_c1, _dr_c2, _dr_c3, _dr_c4 = st.columns([2, 2, 2, 1], gap="medium")
    with _dr_c1:
        _dr_method = st.selectbox(
            "Method",
            ["pca", "tsne", "umap"],
            format_func=lambda m: {"pca": "PCA", "tsne": "t-SNE", "umap": "UMAP"}[m],
            key="dr_method",
        )
        if _dr_method == "tsne":
            st.caption("Sample capped at 2 000 rows — t-SNE is slow on larger sets.")
        elif _dr_method == "umap":
            st.caption("Requires `pip install umap-learn`.")
    with _dr_c2:
        _dr_features = st.multiselect(
            "Columns", numeric_cols,
            default=numeric_cols[:min(10, len(numeric_cols))],
            key="dr_features",
            label_visibility="visible",
        )
    with _dr_c3:
        _dr_sample = st.slider(
            "Sample size", 500, 10_000, 3_000, 500, key="dr_sample",
            help="Number of rows to project.",
        )
    with _dr_c4:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        _dr_run = st.button("Run", type="primary", key="dr_run",
                            disabled=len(_dr_features) < 2,
                            use_container_width=True)

    if _dr_run and len(_dr_features) >= 2:
        with st.spinner(f"Running {_dr_method.upper()} projection …"):
            try:
                with tempfile.TemporaryDirectory() as _tmpdir:
                    _tmp = Path(_tmpdir)
                    _tk  = f"{db.upper()}__{schema.upper()}__{table.upper()}"
                    (_tmp / f"{_tk}.json").write_text(json.dumps(profile))
                    _dr_engine = ClusteringEngine(platform, _tmp)
                    _dr_result = _dr_engine.run_dim_reduction(
                        db, schema, table,
                        columns=_dr_features,
                        method=_dr_method,
                        sample_size=_dr_sample,
                        scaler_type=scaler_key,
                    )
                st.session_state[_dr_data_key] = _dr_result
                try:
                    persistence.merge_result(
                        session, "CLUSTERING_RESULTS", db, schema, table,
                        {"dimred_result": _dr_result},
                    )
                except Exception:
                    pass
            except Exception as _exc:
                st.error(f"Dimensionality reduction failed: {_exc}")

    # Restore from DB on first visit
    if _dr_data_key not in st.session_state:
        st.session_state[_dr_data_key] = (clust_data or {}).get("dimred_result")

    _dr_data = st.session_state.get(_dr_data_key)
    if not _dr_data:
        st.info("Configure and click **Run** to project the data.")
    else:
        _pts = _dr_data.get("points", [])
        _has_clusters = any("cluster" in p for p in _pts)

        _dr_fig = go.Figure()
        if _has_clusters:
            _dr_cluster_groups: dict = {}
            for _p in _pts:
                _cl = _p.get("cluster", -1)
                _dr_cluster_groups.setdefault(_cl, []).append(_p)
            for _li, (_cl, _cpts) in enumerate(sorted(_dr_cluster_groups.items())):
                _is_noise = _cl == -1
                _color    = _NOISE_COLOR if _is_noise else _PALETTE[_li % len(_PALETTE)]
                _name     = "Noise" if _is_noise else f"Cluster {_cl}"
                _dr_fig.add_trace(go.Scatter(
                    x=[p["x"] for p in _cpts], y=[p["y"] for p in _cpts],
                    mode="markers", name=_name,
                    marker=dict(color=_color, size=4, opacity=0.7),
                ))
        else:
            _dr_fig.add_trace(go.Scatter(
                x=[p["x"] for p in _pts], y=[p["y"] for p in _pts],
                mode="markers", name="Data",
                marker=dict(color="#58a6ff", size=4, opacity=0.6),
            ))

        _dr_method_label = {"pca": "PCA", "tsne": "t-SNE", "umap": "UMAP"}.get(
            _dr_data.get("method", "pca"), "Projection"
        )
        _dr_fig.update_layout(
            title=f"{_dr_method_label} 2-D Projection  ({_dr_data.get('sample_size', 0):,} rows)",
            xaxis_title=_dr_data.get("x_label", "Dim 1"),
            yaxis_title=_dr_data.get("y_label", "Dim 2"),
            height=520, margin=dict(t=44, b=0),
            legend=dict(orientation="h", y=-0.06),
        )
        st.plotly_chart(_dr_fig, use_container_width=True)

        if _dr_data.get("explained_variance"):
            _ev = _dr_data["explained_variance"]
            _ev_c1, _ev_c2 = st.columns(2)
            _ev_c1.metric("PC1 variance explained", f"{_ev[0]*100:.1f}%")
            if len(_ev) > 1:
                _ev_c2.metric("PC2 variance explained", f"{_ev[1]*100:.1f}%")

        if not _has_clusters and clust_data and clust_data.get("data_sample"):
            st.caption(
                "Run clustering first to colour this projection by cluster assignment."
            )
