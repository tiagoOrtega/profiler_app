"""DataLens -- Time-Series Trend Analysis page.

Auto-detects date columns, runs period aggregates in Snowflake,
and visualises trend direction, spikes, and period-over-period change.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import numpy as np
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
from trends import TrendAnalyzer, detect_date_columns, detect_metric_columns, PERIODS

platform = SnowparkPlatform(session)

# ── Constants ─────────────────────────────────────────────────────────────────

PERIOD_LABELS = {
    "day":     "Daily",
    "week":    "Weekly",
    "month":   "Monthly",
    "quarter": "Quarterly",
    "year":    "Yearly",
}

TREND_ICON = {
    "up":   "▲",
    "down": "▼",
    "flat": "—",
}
TREND_COLOR = {
    "up":   "#3fb950",
    "down": "#f85149",
    "flat": "#8b949e",
}


# ── Chart helpers ─────────────────────────────────────────────────────────────

def _trend_line_chart(col: str, m: dict, period: str) -> go.Figure:
    periods = m.get("periods", [])
    values  = m.get("values", [])
    spikes  = set(m.get("spikes", []))
    direction = m.get("trend", {}).get("direction", "flat")

    fig = go.Figure()

    # Main line
    fig.add_trace(go.Scatter(
        x=periods, y=values,
        mode="lines+markers",
        name=col,
        line=dict(color="#58a6ff", width=2),
        marker=dict(size=5),
    ))

    # Spike markers
    if spikes:
        spike_x = [periods[i] for i in spikes if i < len(periods)]
        spike_y = [values[i]  for i in spikes if i < len(values)]
        fig.add_trace(go.Scatter(
            x=spike_x, y=spike_y,
            mode="markers",
            name="Spike",
            marker=dict(color="#d29922", size=10, symbol="diamond"),
            showlegend=bool(spike_x),
        ))

    # Trend line (linear regression)
    if len(values) >= 2:
        x_idx = np.arange(len(values), dtype=np.float64)
        y_arr = np.array(values, dtype=np.float64)
        valid = ~np.isnan(y_arr)
        if valid.sum() >= 2:
            coeffs = np.polyfit(x_idx[valid], y_arr[valid], 1)
            trend_y = np.polyval(coeffs, x_idx).tolist()
            fig.add_trace(go.Scatter(
                x=periods, y=trend_y,
                mode="lines",
                name="Trend",
                line=dict(
                    color=TREND_COLOR[direction],
                    width=1.5,
                    dash="dash",
                ),
            ))

    slope_pct = m.get("trend", {}).get("slope_pct", 0)
    mom       = m.get("mom_change_pct")
    subtitle  = (
        f"{TREND_ICON[direction]} {slope_pct:+.1f}%/{period}"
        + (f"  |  last {period}-over-{period}: {mom:+.1f}%" if mom is not None else "")
    )
    fig.update_layout(
        title=f"<b>{col}</b><br><sup>{subtitle}</sup>",
        height=280,
        margin=dict(t=56, b=8, l=0, r=0),
        legend=dict(orientation="h", y=-0.12),
        xaxis_title=period.capitalize(),
    )
    return fig


# ══════════════════════════════ PAGE ═════════════════════════════════════════

st.title("Time-Series Trends")
st.caption(
    "Aggregate numeric metrics over time, detect trend direction, "
    "and flag anomalous spikes — all computed in Snowflake."
)

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
chosen      = st.selectbox("Table", options, index=default_idx, key="tr_tbl")
parts       = chosen.split(".", 2)
db, schema, table = parts
profile     = profiles[options.index(chosen)]

date_cols   = detect_date_columns(profile)
metric_cols = detect_metric_columns(profile)

if not date_cols:
    st.warning(
        "No date or timestamp columns detected in this table's profile. "
        "Profile the table and ensure it has a DATE or TIMESTAMP column."
    )
    st.stop()

trend_data = persistence.load_result(session, "TREND_RESULTS", db, schema, table)

# ── Config row ────────────────────────────────────────────────────────────────

st.divider()
cfg_c1, cfg_c2, cfg_c3 = st.columns([2, 2, 3], gap="medium")

with cfg_c1:
    saved_date = (trend_data or {}).get("date_col", date_cols[0])
    date_idx   = date_cols.index(saved_date) if saved_date in date_cols else 0
    date_col   = st.selectbox("Date Column", date_cols, index=date_idx, key="tr_date")

with cfg_c2:
    saved_period = (trend_data or {}).get("period", "month")
    period_keys  = list(PERIOD_LABELS.keys())
    period_idx   = period_keys.index(saved_period) if saved_period in period_keys else 2
    period       = st.selectbox(
        "Period",
        period_keys,
        format_func=lambda k: PERIOD_LABELS[k],
        index=period_idx,
        key="tr_period",
    )

with cfg_c3:
    saved_metrics = (trend_data or {}).get("metrics", {})
    saved_sel     = list(saved_metrics.keys()) if saved_metrics else metric_cols[:6]
    sel_metrics   = st.multiselect(
        "Metric Columns",
        metric_cols,
        default=[m for m in saved_sel if m in metric_cols] or metric_cols[:6],
        key="tr_metrics",
    )

# ── Run button ────────────────────────────────────────────────────────────────

st.divider()
run_col, status_col = st.columns([1, 3])
with run_col:
    run_clicked = st.button(
        "Analyze Trends", type="primary",
        disabled=not sel_metrics,
        key="tr_run", use_container_width=True,
    )

if run_clicked:
    if not sel_metrics:
        st.warning("Select at least one metric column.")
    else:
        with st.spinner(f"Running {PERIOD_LABELS[period].lower()} aggregation in Snowflake …"):
            try:
                analyzer = TrendAnalyzer(platform, profile)
                result   = analyzer.run(
                    db=db, schema=schema, table=table,
                    date_col=date_col,
                    metric_cols=sel_metrics,
                    period=period,
                )
                persistence.save_result(session, "TREND_RESULTS", db, schema, table, result)
                trend_data = result
                n_ok = sum(1 for m in result["metrics"].values()
                           if isinstance(m, dict) and "trend" in m)
                with status_col:
                    st.success(
                        f"{n_ok} metric(s) analysed  |  "
                        f"{result.get('n_periods', '?')} {PERIOD_LABELS[period].lower()} periods  |  "
                        f"Date: {date_col}"
                    )
                st.rerun()
            except Exception as exc:
                st.error(f"Trend analysis failed: {exc}")

# ══════════════════════════ RESULTS ══════════════════════════════════════════

if not trend_data or not trend_data.get("metrics"):
    st.info("Configure the date column and metrics above, then click **Analyze Trends**.")
    st.stop()

metrics      = trend_data.get("metrics", {})
result_period = trend_data.get("period", period)
result_date   = trend_data.get("date_col", date_col)

# ── Summary table ─────────────────────────────────────────────────────────────

st.subheader("Trend Summary")
summary_rows = []
for col, m in metrics.items():
    if not isinstance(m, dict):
        continue
    if "error" in m:
        summary_rows.append({
            "Metric": col,
            "Direction": "—",
            "Trend %/period": "—",
            "R²": "—",
            "Last MoM %": "—",
            "Spikes": "—",
            "Note": m["error"],
        })
        continue
    t   = m.get("trend", {})
    mom = m.get("mom_change_pct")
    summary_rows.append({
        "Metric": col,
        "Direction": f"{TREND_ICON.get(t.get('direction','flat'))} {t.get('direction','').capitalize()}",
        "Trend %/period": f"{t.get('slope_pct', 0):+.2f}%",
        "R²": f"{t.get('r2', 0):.3f}",
        "Last MoM %": f"{mom:+.1f}%" if mom is not None else "—",
        "Spikes": len(m.get("spikes", [])),
        "Note": "",
    })

st.dataframe(
    pd.DataFrame(summary_rows),
    use_container_width=True,
    hide_index=True,
)

# ── Line charts (2-per-row) ───────────────────────────────────────────────────

st.subheader("Trend Lines")
ok_metrics = [(col, m) for col, m in metrics.items()
              if isinstance(m, dict) and "trend" in m]

for i in range(0, len(ok_metrics), 2):
    chunk = ok_metrics[i: i + 2]
    cols  = st.columns(len(chunk))
    for j, (col, m) in enumerate(chunk):
        with cols[j]:
            st.plotly_chart(
                _trend_line_chart(col, m, result_period),
                use_container_width=True,
            )

# ── Spike detail ──────────────────────────────────────────────────────────────

spike_cols = [(col, m) for col, m in ok_metrics if m.get("spikes")]
if spike_cols:
    with st.expander(f"Spike Detail  ({len(spike_cols)} column(s) with spikes)"):
        for col, m in spike_cols:
            periods_list = m.get("periods", [])
            values_list  = m.get("values", [])
            spikes       = m.get("spikes", [])
            rows = [
                {
                    "Period": periods_list[i],
                    "Value":  values_list[i],
                    "Mean":   round(m.get("mean", 0), 4),
                    "Deviation": round(
                        abs(values_list[i] - m.get("mean", 0)) /
                        max(abs(m.get("max", 1) - m.get("min", 0)), 1e-9), 3
                    ),
                }
                for i in spikes if i < len(periods_list)
            ]
            st.markdown(f"**{col}**")
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ── AI Insights ───────────────────────────────────────────────────────────────

st.divider()
st.subheader("AI Insights")
_ins_key = f"tr_ins_{db}__{schema}__{table}"
if _ins_key not in st.session_state:
    st.session_state[_ins_key] = trend_data.get("ai_insights")

if st.button("Explain Trends", key="tr_ai_btn"):
    with st.spinner("Generating …"):
        try:
            _text = cortex.explain_trends(session, result_date, result_period, metrics)
            st.session_state[_ins_key] = _text
            try:
                persistence.merge_result(
                    session, "TREND_RESULTS", db, schema, table,
                    {"ai_insights": _text},
                )
            except Exception:
                pass
        except Exception as _exc:
            st.error(f"AI explanation failed: {_exc}")

if st.session_state.get(_ins_key):
    st.markdown(st.session_state[_ins_key])
