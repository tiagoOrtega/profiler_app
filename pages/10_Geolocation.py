"""DataLens — Geolocation Insights page.

Queries GOLD_FACT_ORDERS + DIM_LOCATION (both in the user-selected schema)
and renders:
  • World choropleth — revenue or order count by country
  • Scatter-geo bubble map — city-level with bubble size = revenue
  • Continental breakdown bar chart
  • Top markets table
  • Monthly revenue by continent line chart
  • AI geolocation insights
"""

import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

from sis_session import get_session
try:
    session = get_session()
except RuntimeError as _e:
    st.error(str(_e))
    st.stop()

import sis_persistence as persistence
import sis_cortex as cortex

# ── Constants ─────────────────────────────────────────────────────────────────

_FACT_TABLE = "GOLD_FACT_ORDERS"
_DIM_TABLE  = "DIM_LOCATION"

_METRIC_OPTS = {
    "Net Revenue ($)":    "SUM(f.NET_AMOUNT)",
    "Gross Revenue ($)":  "SUM(f.GROSS_AMOUNT)",
    "Margin ($)":         "SUM(f.MARGIN_AMOUNT)",
    "Order Count":        "COUNT(*)",
    "Unique Customers":   "COUNT(DISTINCT f.CUSTOMER_KEY)",
    "Avg Order Value ($)":"AVG(f.NET_AMOUNT)",
}

_CONT_COLORS = {
    "North America": "#58a6ff",
    "South America": "#3fb950",
    "Asia":          "#d29922",
    "Europe":        "#bc8cff",
    "Oceania":       "#39d353",
    "Africa":        "#f85149",
}

# ── Data fetchers (all SQL, no raw rows) ──────────────────────────────────────

def _full_ref(db: str, sc: str, tbl: str) -> str:
    return f"{db}.{sc}.{tbl}"


@st.cache_data(ttl=600, show_spinner=False)
def _fetch_geo_data(db: str, sc: str, date_from: str, date_to: str) -> dict:
    fact = _full_ref(db, sc, _FACT_TABLE)
    dim  = _full_ref(db, sc, _DIM_TABLE)
    where = f"f.ORDER_DATE BETWEEN '{date_from}' AND '{date_to}'"

    # City-level aggregation
    city_rows = session.sql(f"""
        SELECT
            l.LOCATION_KEY,
            l.CITY,
            l.STATE_PROVINCE,
            l.COUNTRY,
            l.COUNTRY_CODE,
            l.CONTINENT,
            l.REGION,
            l.LATITUDE,
            l.LONGITUDE,
            l.IS_MAJOR_CITY,
            COUNT(*)                        AS order_count,
            SUM(f.NET_AMOUNT)               AS total_revenue,
            SUM(f.GROSS_AMOUNT)             AS total_gross,
            SUM(f.MARGIN_AMOUNT)            AS total_margin,
            AVG(f.NET_AMOUNT)               AS avg_order_value,
            COUNT(DISTINCT f.CUSTOMER_KEY)  AS unique_customers
        FROM {fact} f
        JOIN {dim}  l ON l.LOCATION_KEY = f.LOCATION_KEY
        WHERE {where}
        GROUP BY 1,2,3,4,5,6,7,8,9,10
        ORDER BY total_revenue DESC
    """).collect()

    # Country aggregation
    country_rows = session.sql(f"""
        SELECT
            l.COUNTRY,
            l.COUNTRY_CODE,
            l.CONTINENT,
            COUNT(*)                        AS order_count,
            SUM(f.NET_AMOUNT)               AS total_revenue,
            SUM(f.MARGIN_AMOUNT)            AS total_margin,
            AVG(f.NET_AMOUNT)               AS avg_order_value,
            COUNT(DISTINCT f.CUSTOMER_KEY)  AS unique_customers
        FROM {fact} f
        JOIN {dim}  l ON l.LOCATION_KEY = f.LOCATION_KEY
        WHERE {where}
        GROUP BY 1,2,3
        ORDER BY total_revenue DESC
    """).collect()

    total_rev = sum(r[4] or 0 for r in country_rows)

    # Continent aggregation
    continent_rows = session.sql(f"""
        SELECT
            l.CONTINENT,
            COUNT(*)                        AS order_count,
            SUM(f.NET_AMOUNT)               AS total_revenue,
            SUM(f.MARGIN_AMOUNT)            AS total_margin,
            COUNT(DISTINCT l.COUNTRY_CODE)  AS n_countries,
            COUNT(DISTINCT l.LOCATION_KEY)  AS n_cities
        FROM {fact} f
        JOIN {dim}  l ON l.LOCATION_KEY = f.LOCATION_KEY
        WHERE {where}
        GROUP BY 1
        ORDER BY total_revenue DESC
    """).collect()

    # Monthly × continent
    monthly_rows = session.sql(f"""
        SELECT
            l.CONTINENT,
            DATE_TRUNC('month', f.ORDER_DATE)::DATE AS month,
            COUNT(*)            AS order_count,
            SUM(f.NET_AMOUNT)   AS total_revenue
        FROM {fact} f
        JOIN {dim}  l ON l.LOCATION_KEY = f.LOCATION_KEY
        WHERE {where}
        GROUP BY 1,2
        ORDER BY 2,1
    """).collect()

    # Summary scalars
    summary_row = session.sql(f"""
        SELECT
            MIN(f.ORDER_DATE)       AS min_date,
            MAX(f.ORDER_DATE)       AS max_date,
            COUNT(*)                AS total_orders,
            SUM(f.NET_AMOUNT)       AS total_revenue
        FROM {fact} f
        WHERE {where}
    """).first()

    by_city = [
        {
            "location_key":    r[0],
            "city":            r[1],
            "state_province":  r[2],
            "country":         r[3],
            "country_code":    r[4],
            "continent":       r[5],
            "region":          r[6],
            "lat":             float(r[7] or 0),
            "lon":             float(r[8] or 0),
            "is_major_city":   bool(r[9]),
            "order_count":     int(r[10] or 0),
            "total_revenue":   float(r[11] or 0),
            "total_gross":     float(r[12] or 0),
            "total_margin":    float(r[13] or 0),
            "avg_order_value": float(r[14] or 0),
            "unique_customers":int(r[15] or 0),
        }
        for r in city_rows
    ]
    by_country = [
        {
            "country":         r[0],
            "country_code":    r[1],
            "continent":       r[2],
            "order_count":     int(r[3] or 0),
            "total_revenue":   float(r[4] or 0),
            "total_margin":    float(r[5] or 0),
            "avg_order_value": float(r[6] or 0),
            "unique_customers":int(r[7] or 0),
            "pct_revenue":     float(r[4] or 0) / total_rev * 100 if total_rev else 0,
        }
        for r in country_rows
    ]
    by_continent = [
        {
            "continent":    r[0],
            "order_count":  int(r[1] or 0),
            "total_revenue":float(r[2] or 0),
            "total_margin": float(r[3] or 0),
            "n_countries":  int(r[4] or 0),
            "n_cities":     int(r[5] or 0),
            "pct_revenue":  float(r[2] or 0) / total_rev * 100 if total_rev else 0,
        }
        for r in continent_rows
    ]
    by_month = [
        {
            "continent":    r[0],
            "month":        str(r[1]),
            "order_count":  int(r[2] or 0),
            "total_revenue":float(r[3] or 0),
        }
        for r in monthly_rows
    ]

    return {
        "by_city":      by_city,
        "by_country":   by_country,
        "by_continent": by_continent,
        "by_month":     by_month,
        "n_countries":  len(by_country),
        "n_cities":     len(by_city),
        "total_orders": int(summary_row[2] or 0) if summary_row else 0,
        "total_revenue":float(summary_row[3] or 0) if summary_row else 0,
        "min_date":     str(summary_row[0]) if summary_row else "",
        "max_date":     str(summary_row[1]) if summary_row else "",
        "table":        _FACT_TABLE,
    }


@st.cache_data(ttl=600, show_spinner=False)
def _fetch_date_range(db: str, sc: str) -> tuple[str, str]:
    fact = _full_ref(db, sc, _FACT_TABLE)
    row  = session.sql(
        f"SELECT MIN(ORDER_DATE)::VARCHAR, MAX(ORDER_DATE)::VARCHAR FROM {fact}"
    ).first()
    return (str(row[0]), str(row[1])) if row and row[0] else ("2023-01-01", "2024-12-31")


def _check_tables(db: str, sc: str) -> bool:
    for tbl in (_FACT_TABLE, _DIM_TABLE):
        try:
            session.sql(f"SELECT 1 FROM {_full_ref(db, sc, tbl)} LIMIT 1").collect()
        except Exception:
            return False
    return True


# ── Chart builders ────────────────────────────────────────────────────────────

def _choropleth(by_country: list, metric_col: str, metric_label: str) -> go.Figure:
    df = pd.DataFrame(by_country)
    if df.empty:
        return go.Figure()
    col = {
        "Net Revenue ($)":    "total_revenue",
        "Gross Revenue ($)":  "total_gross" if "total_gross" in df.columns else "total_revenue",
        "Margin ($)":         "total_margin",
        "Order Count":        "order_count",
        "Unique Customers":   "unique_customers",
        "Avg Order Value ($)":"avg_order_value",
    }.get(metric_label, "total_revenue")
    if col not in df.columns:
        col = "total_revenue"

    fig = go.Figure(go.Choropleth(
        locations         = df["country_code"],
        z                 = df[col],
        locationmode      = "ISO-3",
        colorscale        = "Blues",
        colorbar_title    = metric_label,
        text              = df["country"],
        hovertemplate     = "<b>%{text}</b><br>" + metric_label + ": %{z:,.0f}<extra></extra>",
        marker_line_color = "#1a1a2e",
        marker_line_width = 0.5,
    ))
    fig.update_layout(
        geo=dict(
            showframe       = False,
            showcoastlines  = True,
            coastlinecolor  = "#444",
            showland        = True,
            landcolor       = "#1e2a3a",
            showocean       = True,
            oceancolor      = "#0d1117",
            showlakes       = False,
            projection_type = "natural earth",
        ),
        height        = 420,
        margin        = dict(t=10, b=0, l=0, r=0),
        paper_bgcolor = "#0d1117",
        font_color    = "#c9d1d9",
    )
    return fig


def _scatter_geo(by_city: list, metric_label: str) -> go.Figure:
    df = pd.DataFrame(by_city)
    if df.empty:
        return go.Figure()
    col = {
        "Net Revenue ($)":    "total_revenue",
        "Gross Revenue ($)":  "total_revenue",
        "Margin ($)":         "total_margin",
        "Order Count":        "order_count",
        "Unique Customers":   "unique_customers",
        "Avg Order Value ($)":"avg_order_value",
    }.get(metric_label, "total_revenue")
    if col not in df.columns:
        col = "total_revenue"

    cont_vals = df["continent"].unique()
    color_map = {c: _CONT_COLORS.get(c, "#888") for c in cont_vals}

    fig = go.Figure()
    for cont in cont_vals:
        sub = df[df["continent"] == cont]
        sizes = sub[col]
        max_v = sizes.max() or 1
        fig.add_trace(go.Scattergeo(
            lat         = sub["lat"],
            lon         = sub["lon"],
            mode        = "markers",
            name        = cont,
            marker      = dict(
                size        = (sizes / max_v * 35 + 5).clip(5, 40),
                color       = color_map[cont],
                opacity     = 0.80,
                line        = dict(color="#0d1117", width=0.5),
            ),
            text        = sub["city"] + ", " + sub["country"],
            customdata  = sub[[col, "order_count"]].values,
            hovertemplate = (
                "<b>%{text}</b><br>"
                + metric_label + ": %{customdata[0]:,.0f}<br>"
                "Orders: %{customdata[1]:,}<extra></extra>"
            ),
        ))

    fig.update_layout(
        geo=dict(
            showframe       = False,
            showcoastlines  = True,
            coastlinecolor  = "#444",
            showland        = True,
            landcolor       = "#1e2a3a",
            showocean       = True,
            oceancolor      = "#0d1117",
            projection_type = "natural earth",
        ),
        legend=dict(
            orientation="h", y=-0.05, x=0.5, xanchor="center",
            bgcolor="rgba(0,0,0,0)", font_color="#c9d1d9",
        ),
        height        = 420,
        margin        = dict(t=10, b=0, l=0, r=0),
        paper_bgcolor = "#0d1117",
        font_color    = "#c9d1d9",
    )
    return fig


def _continent_bar(by_continent: list, metric_label: str) -> go.Figure:
    col = {
        "Net Revenue ($)":    "total_revenue",
        "Gross Revenue ($)":  "total_revenue",
        "Margin ($)":         "total_margin",
        "Order Count":        "order_count",
        "Unique Customers":   "n_cities",
        "Avg Order Value ($)":"total_revenue",
    }.get(metric_label, "total_revenue")

    rows = sorted(by_continent, key=lambda r: r.get(col, 0), reverse=True)
    labels = [r["continent"] for r in rows]
    vals   = [r.get(col, 0) for r in rows]
    colors = [_CONT_COLORS.get(r["continent"], "#888") for r in rows]
    pcts   = [r.get("pct_revenue", 0) for r in rows]

    fig = go.Figure(go.Bar(
        y         = labels[::-1],
        x         = vals[::-1],
        orientation= "h",
        marker_color= colors[::-1],
        text      = [f"{p:.1f}%" for p in pcts[::-1]],
        textposition="outside",
        hovertemplate = "<b>%{y}</b><br>" + metric_label + ": %{x:,.0f}<extra></extra>",
    ))
    fig.update_layout(
        title  = f"{metric_label} by Continent",
        height = 260,
        margin = dict(t=36, b=8, l=0, r=60),
        xaxis_title = metric_label,
        paper_bgcolor = "rgba(0,0,0,0)",
        plot_bgcolor  = "rgba(0,0,0,0)",
        font_color    = "#c9d1d9",
    )
    return fig


def _monthly_lines(by_month: list) -> go.Figure:
    if not by_month:
        return go.Figure()
    df = pd.DataFrame(by_month)
    df["month"] = pd.to_datetime(df["month"])
    fig = go.Figure()
    for cont in sorted(df["continent"].unique()):
        sub = df[df["continent"] == cont].sort_values("month")
        fig.add_trace(go.Scatter(
            x    = sub["month"],
            y    = sub["total_revenue"],
            name = cont,
            mode = "lines",
            line = dict(color=_CONT_COLORS.get(cont, "#888"), width=2),
            hovertemplate = "<b>" + cont + "</b><br>%{x|%b %Y}: $%{y:,.0f}<extra></extra>",
        ))
    fig.update_layout(
        title  = "Monthly Revenue by Continent",
        height = 280,
        margin = dict(t=36, b=8, l=0, r=8),
        xaxis_title = "Month",
        yaxis_title = "Net Revenue ($)",
        legend=dict(
            orientation="h", y=-0.25, x=0.5, xanchor="center",
            bgcolor="rgba(0,0,0,0)",
        ),
        paper_bgcolor = "rgba(0,0,0,0)",
        plot_bgcolor  = "rgba(0,0,0,0)",
        font_color    = "#c9d1d9",
    )
    return fig


# ══════════════════════════════ PAGE ═════════════════════════════════════════

st.title("Geolocation Insights")
st.caption(
    "Worldwide sales analysis from `GOLD_FACT_ORDERS` enriched with `DIM_LOCATION`.  "
    "Run `setup_geolocation.sql` to populate these tables before using this page."
)

# ── Source selector ───────────────────────────────────────────────────────────

with st.sidebar:
    st.subheader("Data Source")
    _db_val = st.text_input(
        "Database",
        value=st.session_state.get("geo_db", "SAMPLE_DW"),
        key="geo_db",
    )
    _sc_val = st.text_input(
        "Schema",
        value=st.session_state.get("geo_schema", "ORGANIC"),
        key="geo_schema",
    )

db = _db_val.strip().upper()
sc = _sc_val.strip().upper()

if not _check_tables(db, sc):
    st.error(
        f"Tables `{db}.{sc}.{_FACT_TABLE}` or `{db}.{sc}.{_DIM_TABLE}` not found.  \n"
        "Run **`setup_geolocation.sql`** first, then verify the database and schema above."
    )
    st.stop()

# ── Date range ────────────────────────────────────────────────────────────────

with st.spinner("Loading date range …"):
    _min_dt_str, _max_dt_str = _fetch_date_range(db, sc)

import datetime
_min_dt = datetime.date.fromisoformat(_min_dt_str)
_max_dt = datetime.date.fromisoformat(_max_dt_str)

with st.sidebar:
    st.subheader("Filters")
    date_from = st.date_input("From", value=_min_dt, min_value=_min_dt, max_value=_max_dt, key="geo_df")
    date_to   = st.date_input("To",   value=_max_dt, min_value=_min_dt, max_value=_max_dt, key="geo_dt")

    metric_label = st.selectbox("Metric", list(_METRIC_OPTS.keys()), key="geo_metric")
    map_type     = st.radio("Map Type", ["Choropleth (Country)", "Scatter (City)"], key="geo_maptype")

if date_from > date_to:
    st.warning("'From' date must be before 'To' date.")
    st.stop()

# ── Load data ─────────────────────────────────────────────────────────────────

with st.spinner("Fetching geolocation data …"):
    geo = _fetch_geo_data(db, sc, str(date_from), str(date_to))

by_city      = geo["by_city"]
by_country   = geo["by_country"]
by_continent = geo["by_continent"]
by_month     = geo["by_month"]

# ── Top-level metrics ─────────────────────────────────────────────────────────

mc1, mc2, mc3, mc4, mc5 = st.columns(5)
mc1.metric("Total Orders",    f"{geo['total_orders']:,}")
mc2.metric("Net Revenue",     f"${geo['total_revenue']:,.0f}")
mc3.metric("Countries",       geo["n_countries"])
mc4.metric("Cities",          geo["n_cities"])
_top_cont = max(by_continent, key=lambda r: r["total_revenue"], default={})
mc5.metric("Top Continent",   _top_cont.get("continent", "—"))

# ── Map ───────────────────────────────────────────────────────────────────────

st.divider()
if map_type == "Choropleth (Country)":
    st.plotly_chart(_choropleth(by_country, _METRIC_OPTS[metric_label], metric_label),
                    use_container_width=True)
else:
    st.plotly_chart(_scatter_geo(by_city, metric_label),
                    use_container_width=True)

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_cont, tab_mkt, tab_trend, tab_ai = st.tabs([
    "Continental Breakdown", "Top Markets", "Time Analysis", "AI Insights",
])

# Tab 1 — Continental breakdown ───────────────────────────────────────────────
with tab_cont:
    cb1, cb2 = st.columns([1, 1])
    with cb1:
        st.plotly_chart(_continent_bar(by_continent, metric_label), use_container_width=True)
    with cb2:
        df_cont = pd.DataFrame(by_continent)[
            ["continent", "order_count", "total_revenue", "total_margin",
             "n_countries", "n_cities", "pct_revenue"]
        ].rename(columns={
            "continent":    "Continent",
            "order_count":  "Orders",
            "total_revenue":"Revenue ($)",
            "total_margin": "Margin ($)",
            "n_countries":  "Countries",
            "n_cities":     "Cities",
            "pct_revenue":  "Rev %",
        })
        df_cont["Revenue ($)"] = df_cont["Revenue ($)"].map("${:,.0f}".format)
        df_cont["Margin ($)"]  = df_cont["Margin ($)"].map("${:,.0f}".format)
        df_cont["Rev %"]       = df_cont["Rev %"].map("{:.1f}%".format)
        df_cont["Orders"]      = df_cont["Orders"].map("{:,}".format)
        st.dataframe(df_cont, hide_index=True, use_container_width=True)

# Tab 2 — Top markets ─────────────────────────────────────────────────────────
with tab_mkt:
    tm1, tm2 = st.columns(2)

    with tm1:
        st.subheader("Top 15 Countries")
        df_ctry = pd.DataFrame(by_country[:15])[
            ["country", "continent", "order_count", "total_revenue",
             "total_margin", "avg_order_value", "unique_customers", "pct_revenue"]
        ].rename(columns={
            "country":         "Country",
            "continent":       "Continent",
            "order_count":     "Orders",
            "total_revenue":   "Revenue ($)",
            "total_margin":    "Margin ($)",
            "avg_order_value": "Avg Order ($)",
            "unique_customers":"Customers",
            "pct_revenue":     "Rev %",
        })
        df_ctry["Revenue ($)"]   = df_ctry["Revenue ($)"].map("${:,.0f}".format)
        df_ctry["Margin ($)"]    = df_ctry["Margin ($)"].map("${:,.0f}".format)
        df_ctry["Avg Order ($)"] = df_ctry["Avg Order ($)"].map("${:,.0f}".format)
        df_ctry["Orders"]        = df_ctry["Orders"].map("{:,}".format)
        df_ctry["Customers"]     = df_ctry["Customers"].map("{:,}".format)
        df_ctry["Rev %"]         = df_ctry["Rev %"].map("{:.1f}%".format)
        st.dataframe(df_ctry, hide_index=True, use_container_width=True)

    with tm2:
        st.subheader("Top 15 Cities")
        df_city = pd.DataFrame(by_city[:15])[
            ["city", "country", "continent", "order_count",
             "total_revenue", "total_margin", "avg_order_value", "unique_customers"]
        ].rename(columns={
            "city":            "City",
            "country":         "Country",
            "continent":       "Continent",
            "order_count":     "Orders",
            "total_revenue":   "Revenue ($)",
            "total_margin":    "Margin ($)",
            "avg_order_value": "Avg Order ($)",
            "unique_customers":"Customers",
        })
        df_city["Revenue ($)"]   = df_city["Revenue ($)"].map("${:,.0f}".format)
        df_city["Margin ($)"]    = df_city["Margin ($)"].map("${:,.0f}".format)
        df_city["Avg Order ($)"] = df_city["Avg Order ($)"].map("${:,.0f}".format)
        df_city["Orders"]        = df_city["Orders"].map("{:,}".format)
        df_city["Customers"]     = df_city["Customers"].map("{:,}".format)
        st.dataframe(df_city, hide_index=True, use_container_width=True)

    # Country revenue bar (top 20)
    st.divider()
    _top20 = by_country[:20]
    _fig_ctry = go.Figure(go.Bar(
        x    = [r["total_revenue"] for r in _top20][::-1],
        y    = [r["country"] for r in _top20][::-1],
        orientation  = "h",
        marker_color = [_CONT_COLORS.get(r["continent"], "#888") for r in _top20][::-1],
        text         = [f"${r['total_revenue']:,.0f}" for r in _top20][::-1],
        textposition = "outside",
        hovertemplate= "<b>%{y}</b><br>Revenue: %{x:,.0f}<extra></extra>",
    ))
    _fig_ctry.update_layout(
        title  = "Net Revenue — Top 20 Countries",
        height = max(300, 26 * len(_top20)),
        margin = dict(t=36, b=8, l=0, r=80),
        paper_bgcolor = "rgba(0,0,0,0)",
        plot_bgcolor  = "rgba(0,0,0,0)",
        font_color    = "#c9d1d9",
    )
    st.plotly_chart(_fig_ctry, use_container_width=True)

# Tab 3 — Time analysis ───────────────────────────────────────────────────────
with tab_trend:
    st.plotly_chart(_monthly_lines(by_month), use_container_width=True)

    # Heatmap: continent × month
    if by_month:
        df_m  = pd.DataFrame(by_month)
        df_m["month"] = pd.to_datetime(df_m["month"]).dt.strftime("%Y-%m")
        pivot = df_m.pivot_table(
            index="continent", columns="month",
            values="total_revenue", aggfunc="sum", fill_value=0,
        )
        fig_heat = go.Figure(go.Heatmap(
            z     = pivot.values.tolist(),
            x     = list(pivot.columns),
            y     = list(pivot.index),
            colorscale = "Blues",
            hovertemplate = "<b>%{y}</b> — %{x}<br>Revenue: $%{z:,.0f}<extra></extra>",
        ))
        fig_heat.update_layout(
            title  = "Revenue Heatmap — Continent × Month",
            height = 300,
            margin = dict(t=36, b=40, l=0, r=0),
            paper_bgcolor = "rgba(0,0,0,0)",
            plot_bgcolor  = "rgba(0,0,0,0)",
            font_color    = "#c9d1d9",
        )
        st.plotly_chart(fig_heat, use_container_width=True)

# Tab 4 — AI insights ─────────────────────────────────────────────────────────
with tab_ai:
    _geo_key = f"geo_ai_{db}__{sc}__{date_from}__{date_to}"

    if st.button("Generate AI Insights", key="geo_ai_btn", type="primary"):
        with st.spinner("Analyzing geolocation data with AI …"):
            _insight = cortex.explain_geolocation(session, geo)
            st.session_state[_geo_key] = _insight
            # Persist to Snowflake
            try:
                persistence.merge_result(
                    session, "GEO_RESULTS", db, sc, _FACT_TABLE,
                    {"ai_insights": _insight, "generated_at": str(date_to)},
                )
            except Exception:
                pass

    _cached = st.session_state.get(_geo_key)
    if not _cached:
        try:
            _saved = persistence.load_result(session, "GEO_RESULTS", db, sc, _FACT_TABLE)
            _cached = (_saved or {}).get("ai_insights")
        except Exception:
            pass

    if _cached:
        st.info(_cached)
    else:
        st.caption(
            "Click **Generate AI Insights** to get an AI interpretation of the "
            "geographic distribution, top markets, and expansion opportunities."
        )

    # Context expander for the data that was sent to the AI
    with st.expander("Data context sent to AI"):
        st.json({
            "n_countries":  geo["n_countries"],
            "n_cities":     geo["n_cities"],
            "total_orders": geo["total_orders"],
            "total_revenue":round(geo["total_revenue"], 2),
            "date_range":   f"{geo['min_date']} → {geo['max_date']}",
            "by_continent": [
                {k: v for k, v in r.items()} for r in geo["by_continent"]
            ],
            "top_10_countries": by_country[:10],
        })
