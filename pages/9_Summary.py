"""DataLens -- Business Summary Report page.

Combines profile, correlations, clustering, and trend data into a single
executive report.  Exports as:
  • HTML  — full interactive Plotly charts, print-to-PDF ready
  • PDF   — text + tables via reportlab (no charts)
  • Confluence — REST API (POST /wiki/rest/api/content), storage format
"""

import sys
import json
from io import BytesIO
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

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

# ── Chart builders ────────────────────────────────────────────────────────────

def _quality_chart(profile: dict) -> go.Figure:
    cols     = profile.get("columns", [])
    flagged  = sorted(
        [c for c in cols if c.get("null_rate", 0) > 0],
        key=lambda c: c.get("null_rate", 0), reverse=True,
    )[:10]
    if not flagged:
        return None
    names  = [c["name"] for c in flagged]
    values = [round(c.get("null_rate", 0) * 100, 1) for c in flagged]
    colors = ["#f85149" if v > 20 else "#d29922" if v > 5 else "#3fb950" for v in values]
    fig = go.Figure(go.Bar(
        x=values[::-1], y=names[::-1], orientation="h",
        marker_color=colors[::-1],
        text=[f"{v:.1f}%" for v in values[::-1]], textposition="outside",
    ))
    fig.update_layout(
        title="Null Rate by Column  (top 10)",
        xaxis_title="Null %", height=max(180, 28 * len(names)),
        margin=dict(t=36, b=8, l=0, r=60), showlegend=False,
    )
    return fig


def _dtype_chart(profile: dict) -> go.Figure:
    from collections import Counter
    cols   = profile.get("columns", [])
    counts = Counter(
        "Numeric" if c.get("mean") is not None
        else "Date/Time" if any(dt in c.get("data_type", "").lower()
                                for dt in ("date", "time", "timestamp"))
        else "Text"
        for c in cols
    )
    fig = go.Figure(go.Pie(
        labels=list(counts.keys()),
        values=list(counts.values()),
        hole=0.45,
        marker_colors=["#58a6ff", "#3fb950", "#d29922"],
    ))
    fig.update_layout(
        title="Column Types",
        height=220,
        margin=dict(t=36, b=8, l=0, r=0),
        showlegend=True,
        legend=dict(orientation="h", y=-0.1),
    )
    return fig


def _cluster_chart(clust_data: dict) -> go.Figure:
    stats = clust_data.get("cluster_stats", [])
    if not stats:
        return None
    colors_map = {ds["cluster"]: ds.get("color", "#58a6ff")
                  for ds in clust_data.get("scatter", {}).get("datasets", [])}
    labels = [f"Cluster {cs['cluster']}" for cs in stats]
    sizes  = [cs["size"] for cs in stats]
    pcts   = [cs["pct"] for cs in stats]
    clrs   = [colors_map.get(cs["cluster"], "#58a6ff") for cs in stats]
    fig = go.Figure(go.Bar(
        y=labels[::-1], x=sizes[::-1], orientation="h",
        marker_color=clrs[::-1],
        text=[f"{p:.1f}%" for p in pcts[::-1]], textposition="inside",
    ))
    fig.update_layout(
        title=f"Cluster Sizes  ({clust_data.get('model_label','?')})",
        height=max(160, 36 * len(stats)),
        margin=dict(t=36, b=8, l=0, r=8), showlegend=False,
        xaxis_title="Rows",
    )
    return fig


def _corr_chart(corr_data: dict) -> go.Figure:
    cols   = corr_data.get("columns", [])
    matrix = corr_data.get("matrix", [])
    pairs  = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            if i < len(matrix) and j < len(matrix[i]) and matrix[i][j] is not None:
                pairs.append((f"{cols[i]} / {cols[j]}", abs(matrix[i][j])))
    if not pairs:
        return None
    pairs.sort(key=lambda p: p[1], reverse=True)
    pairs = pairs[:8]
    labels = [p[0] for p in pairs]
    vals   = [round(p[1], 3) for p in pairs]
    colors = ["#f85149" if v > 0.85 else "#d29922" if v > 0.6 else "#58a6ff" for v in vals]
    fig = go.Figure(go.Bar(
        x=vals[::-1], y=labels[::-1], orientation="h",
        marker_color=colors[::-1],
        text=[f"{v:.3f}" for v in vals[::-1]], textposition="outside",
    ))
    fig.update_layout(
        title="Top Correlations  (|r|)",
        height=max(160, 28 * len(pairs)),
        margin=dict(t=36, b=8, l=0, r=60), showlegend=False,
        xaxis=dict(range=[0, 1.1]),
    )
    return fig


def _trend_chart(trend_data: dict) -> go.Figure:
    metrics = trend_data.get("metrics", {})
    rows    = [(col, m["trend"]["slope_pct"], m["trend"]["direction"])
               for col, m in metrics.items()
               if isinstance(m, dict) and "trend" in m]
    if not rows:
        return None
    rows.sort(key=lambda r: r[1])
    labels  = [r[0] for r in rows]
    vals    = [r[1] for r in rows]
    clrs    = ["#f85149" if v < 0 else "#3fb950" for v in vals]
    fig = go.Figure(go.Bar(
        x=vals, y=labels, orientation="h",
        marker_color=clrs,
        text=[f"{v:+.1f}%" for v in vals], textposition="outside",
    ))
    fig.update_layout(
        title=f"Trend Slope %/{trend_data.get('period','month')}",
        height=max(160, 28 * len(rows)),
        margin=dict(t=36, b=8, l=0, r=60), showlegend=False,
        xaxis_title="% change per period",
    )
    return fig


# ── Export builders ───────────────────────────────────────────────────────────

_HTML_CSS = """
<style>
  @media print { .no-print { display:none; } }
  body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         color:#1a1a2e; max-width:1100px; margin:0 auto; padding:2rem; }
  .rpt-header { background:linear-gradient(135deg,#1a1a2e,#16213e);
                color:#fff; padding:1.8rem 2rem; border-radius:8px; margin-bottom:1.5rem; }
  .rpt-header h1 { margin:0 0 0.3rem; font-size:1.8rem; }
  .rpt-header p  { margin:0; opacity:0.75; font-size:0.95rem; }
  .metric-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:1rem; margin:1.5rem 0; }
  .metric-card { background:#f4f6fa; border-left:4px solid #58a6ff;
                 padding:1rem 1.2rem; border-radius:4px; }
  .metric-val  { font-size:1.9rem; font-weight:700; color:#1a1a2e; }
  .metric-lbl  { font-size:0.75rem; color:#888; text-transform:uppercase; letter-spacing:.05em; }
  h2 { color:#1a1a2e; border-bottom:2px solid #58a6ff; padding-bottom:0.4rem;
       margin-top:2rem; }
  table { border-collapse:collapse; width:100%; margin:0.8rem 0; font-size:0.9rem; }
  th { background:#1a1a2e; color:#fff; padding:7px 12px; text-align:left; }
  td { border:1px solid #dee2e6; padding:6px 12px; }
  tr:nth-child(even) td { background:#f8f9fa; }
  .insight-box { background:#e8f4fd; border-left:4px solid #58a6ff;
                 padding:1rem 1.5rem; margin:1rem 0; border-radius:0 6px 6px 0;
                 white-space:pre-wrap; }
  .rec-box { background:#f0fff4; border-left:4px solid #3fb950;
             padding:1rem 1.5rem; margin:1rem 0; border-radius:0 6px 6px 0;
             white-space:pre-wrap; }
  .alert-row td { background:#fff3cd !important; }
  .footer { color:#aaa; font-size:0.8rem; text-align:center;
            margin-top:3rem; padding-top:1rem; border-top:1px solid #dee2e6; }
  @media print {
    .rpt-header { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
    h2 { page-break-before:auto; }
    table { page-break-inside:avoid; }
  }
</style>
"""


def _html_metric_cards(metrics: list[tuple]) -> str:
    cards = "".join(
        f'<div class="metric-card"><div class="metric-val">{v}</div>'
        f'<div class="metric-lbl">{l}</div></div>'
        for l, v in metrics
    )
    return f'<div class="metric-grid">{cards}</div>'


def _build_html(
    profile: dict,
    clust_data: dict | None,
    trend_data: dict | None,
    corr_data:  dict | None,
    ai_summary: dict,
    charts: dict,          # {name: html_fragment}
) -> str:
    from datetime import date as _date

    db, sc, tbl = profile.get("database",""), profile.get("schema",""), profile.get("table","")
    rows   = profile.get("row_count", 0)
    n_col  = profile.get("column_count", 0)
    dated  = (profile.get("profiled_at") or "")[:10]
    cols   = profile.get("columns", [])
    null_r = sum(c.get("null_rate", 0) for c in cols) / max(len(cols), 1)
    flags  = [c for c in cols if c.get("null_rate", 0) > 0.2 or c.get("error")]

    metric_cards = [
        ("Total Rows",    f"{rows:,}"),
        ("Columns",       str(n_col)),
        ("Avg Null Rate", f"{null_r:.1%}"),
        ("Quality Issues",str(len(flags))),
    ]
    if clust_data:
        metric_cards[3] = ("Segments",    str(clust_data.get("n_clusters", "?")))

    # Column stats table
    col_rows = "".join(
        f'<tr{"class=\"alert-row\"" if c.get("null_rate",0)>0.2 else ""}>'
        f"<td>{c['name']}</td><td>{c.get('data_type','')}</td>"
        f"<td>{c.get('null_rate',0):.1%}</td>"
        f"<td>{c.get('distinct_count','—'):,}" if isinstance(c.get('distinct_count'), int)
        else f"<td>—"
        f"</td><td>{c.get('mean','—')}</td></tr>"
        for c in cols[:30]
    )

    clust_section = ""
    if clust_data:
        m   = clust_data.get("metrics", {})
        sil = m.get("silhouette_score", 0)
        db_s = m.get("davies_bouldin_score", 0)
        clust_section = (
            "<h2>Cluster Analysis</h2>"
            f"<p>Model: <b>{clust_data.get('model_label','?')}</b>  &nbsp;|&nbsp; "
            f"Segments: <b>{clust_data.get('n_clusters','?')}</b>  &nbsp;|&nbsp; "
            f"Silhouette: <b>{sil:.3f}</b>  &nbsp;|&nbsp; "
            f"Davies-Bouldin: <b>{db_s:.3f}</b></p>"
            + charts.get("clusters", "")
        )

    trend_section = ""
    if trend_data and trend_data.get("metrics"):
        mets = trend_data["metrics"]
        trend_rows = "".join(
            f"<tr><td>{col}</td>"
            f"<td>{'▲' if m['trend']['direction']=='up' else '▼' if m['trend']['direction']=='down' else '—'} "
            f"{m['trend']['direction'].capitalize()}</td>"
            f"<td>{m['trend']['slope_pct']:+.2f}%</td>"
            f"<td>{m.get('mom_change_pct','—'):+.1f}%" if isinstance(m.get('mom_change_pct'), (int,float)) else "<td>—"
            f"</td><td>{len(m.get('spikes',[]))}</td></tr>"
            for col, m in mets.items() if isinstance(m, dict) and "trend" in m
        )
        trend_section = (
            "<h2>Time-Series Trends</h2>"
            f"<p>Date column: <b>{trend_data.get('date_col','')}</b>  &nbsp;|&nbsp; "
            f"Period: <b>{trend_data.get('period','')}</b>  &nbsp;|&nbsp; "
            f"Periods analysed: <b>{trend_data.get('n_periods','?')}</b></p>"
            + charts.get("trends", "")
            + "<table><tr><th>Metric</th><th>Direction</th><th>Slope %</th>"
              "<th>Last MoM %</th><th>Spikes</th></tr>"
            + trend_rows + "</table>"
        )

    corr_section = ""
    if corr_data and corr_data.get("columns"):
        corr_section = (
            "<h2>Correlations</h2>"
            "<p>Pairs with |r| ≥ 0.7 may indicate redundant features or strong causal links.</p>"
            + charts.get("correlations", "")
        )

    exec_html = (ai_summary.get("executive_summary") or "").replace("\n", "<br>")
    recs_html = (ai_summary.get("recommendations") or "").replace("\n", "<br>")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Business Report — {tbl}</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
{_HTML_CSS}
</head>
<body>
<div class="rpt-header">
  <h1>Business Report &mdash; {tbl}</h1>
  <p>{db}.{sc} &nbsp;|&nbsp; {rows:,} rows &nbsp;|&nbsp;
     {n_col} columns &nbsp;|&nbsp; Profiled {dated} &nbsp;|&nbsp;
     Generated {_date.today().isoformat()}</p>
</div>

{_html_metric_cards(metric_cards)}

<h2>Executive Summary</h2>
<div class="insight-box">{exec_html}</div>

<h2>Data Quality</h2>
<div style="display:grid;grid-template-columns:2fr 1fr;gap:1rem">
  <div>{charts.get("quality","")}</div>
  <div>{charts.get("dtypes","")}</div>
</div>
<table>
<tr><th>Column</th><th>Type</th><th>Null Rate</th><th>Distinct</th><th>Mean</th></tr>
{col_rows}
</table>

{clust_section}
{trend_section}
{corr_section}

<h2>Recommendations</h2>
<div class="rec-box">{recs_html}</div>

<div class="footer">Generated by DataLens &nbsp;|&nbsp; {_date.today().isoformat()}</div>
</body>
</html>"""


def _build_pdf(
    profile: dict,
    clust_data: dict | None,
    trend_data: dict | None,
    corr_data:  dict | None,
    ai_summary: dict,
) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2.2*cm, rightMargin=2.2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title=f"Business Report — {profile.get('table','')}",
    )

    styles  = getSampleStyleSheet()
    NAVY    = colors.HexColor("#1a1a2e")
    BLUE    = colors.HexColor("#58a6ff")
    RED     = colors.HexColor("#f85149")
    YELLOW  = colors.HexColor("#d29922")
    GREEN   = colors.HexColor("#3fb950")
    LGRAY   = colors.HexColor("#f4f6fa")

    h1_style = ParagraphStyle("h1", parent=styles["Heading1"],
                              textColor=NAVY, fontSize=18, spaceAfter=4)
    h2_style = ParagraphStyle("h2", parent=styles["Heading2"],
                              textColor=NAVY, fontSize=13, spaceBefore=14, spaceAfter=4)
    body_s   = ParagraphStyle("body", parent=styles["Normal"],
                               fontSize=9.5, leading=14, spaceAfter=6)
    caption  = ParagraphStyle("cap", parent=styles["Normal"],
                               fontSize=8.5, textColor=colors.gray, spaceAfter=4)

    def _tbl_style(header_color=NAVY):
        return TableStyle([
            ("BACKGROUND", (0,0), (-1,0), header_color),
            ("TEXTCOLOR",  (0,0), (-1,0), colors.white),
            ("FONTSIZE",   (0,0), (-1,0), 9),
            ("FONTNAME",   (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",   (0,1), (-1,-1), 8.5),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, LGRAY]),
            ("GRID",       (0,0), (-1,-1), 0.3, colors.HexColor("#dee2e6")),
            ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ])

    db, sc, tbl = (profile.get("database",""), profile.get("schema",""),
                   profile.get("table",""))
    rows_c = profile.get("row_count", 0)
    n_col  = profile.get("column_count", 0)
    dated  = (profile.get("profiled_at") or "")[:10]
    cols_p = profile.get("columns", [])
    flags  = [c for c in cols_p if c.get("null_rate", 0) > 0.2 or c.get("error")]

    story = []

    # ── Title block ───────────────────────────────────────────────────────────
    story.append(Paragraph(f"Business Report — {tbl}", h1_style))
    story.append(Paragraph(
        f"<font color='#{BLUE.hexval()[2:]}'>Location:</font> {db}.{sc} &nbsp;|&nbsp; "
        f"<font color='#{BLUE.hexval()[2:]}'>Rows:</font> {rows_c:,} &nbsp;|&nbsp; "
        f"<font color='#{BLUE.hexval()[2:]}'>Columns:</font> {n_col} &nbsp;|&nbsp; "
        f"<font color='#{BLUE.hexval()[2:]}'>Profiled:</font> {dated}",
        caption,
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=BLUE, spaceAfter=10))

    # ── Key metrics ───────────────────────────────────────────────────────────
    null_r = sum(c.get("null_rate", 0) for c in cols_p) / max(len(cols_p), 1)
    n_seg  = clust_data.get("n_clusters", "—") if clust_data else "—"
    metric_data = [
        ["Metric", "Value"],
        ["Total Rows",     f"{rows_c:,}"],
        ["Total Columns",  str(n_col)],
        ["Avg Null Rate",  f"{null_r:.1%}"],
        ["Quality Issues", str(len(flags))],
        ["Segments",       str(n_seg)],
    ]
    story.append(Paragraph("Key Metrics", h2_style))
    t = Table(metric_data, colWidths=[8*cm, 8*cm])
    t.setStyle(_tbl_style())
    story.append(t)
    story.append(Spacer(1, 10))

    # ── Executive summary ─────────────────────────────────────────────────────
    exec_s = ai_summary.get("executive_summary", "")
    if exec_s:
        story.append(Paragraph("Executive Summary", h2_style))
        story.append(Paragraph(exec_s, body_s))

    # ── Data quality ──────────────────────────────────────────────────────────
    story.append(Paragraph("Data Quality", h2_style))
    qual_data = [["Column", "Type", "Null Rate", "Distinct", "Mean"]]
    for c in cols_p[:25]:
        null_v = c.get("null_rate", 0)
        flag   = "⚠ " if null_v > 0.2 else ""
        qual_data.append([
            flag + c["name"],
            c.get("data_type", ""),
            f"{null_v:.1%}",
            f"{c.get('distinct_count', '—'):,}" if isinstance(c.get("distinct_count"), int) else "—",
            f"{c.get('mean', '—'):.3f}" if isinstance(c.get("mean"), (int, float)) else "—",
        ])
    t = Table(qual_data, colWidths=[4.5*cm, 2.5*cm, 2*cm, 2.5*cm, 2.5*cm])
    ts = _tbl_style()
    for i, row in enumerate(qual_data[1:], 1):
        if row[0].startswith("⚠"):
            ts.add("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fff3cd"))
    t.setStyle(ts)
    story.append(t)
    story.append(Spacer(1, 8))

    # ── Clustering ────────────────────────────────────────────────────────────
    if clust_data:
        story.append(Paragraph("Cluster Analysis", h2_style))
        m   = clust_data.get("metrics", {})
        sil = m.get("silhouette_score", 0)
        dbs = m.get("davies_bouldin_score", 0)
        story.append(Paragraph(
            f"Model: <b>{clust_data.get('model_label','?')}</b>  |  "
            f"Segments: <b>{clust_data.get('n_clusters','?')}</b>  |  "
            f"Silhouette: <b>{sil:.3f}</b>  |  Davies-Bouldin: <b>{dbs:.3f}</b>",
            body_s,
        ))
        cs_data = [["Cluster", "Rows", "% Total"]] + [
            [f"Cluster {cs['cluster']}", f"{cs['size']:,}", f"{cs.get('pct',0):.1f}%"]
            for cs in clust_data.get("cluster_stats", [])
        ]
        if len(cs_data) > 1:
            t = Table(cs_data, colWidths=[5*cm, 4*cm, 4*cm])
            t.setStyle(_tbl_style())
            story.append(t)
        story.append(Spacer(1, 8))

    # ── Trends ────────────────────────────────────────────────────────────────
    if trend_data and trend_data.get("metrics"):
        story.append(Paragraph("Time-Series Trends", h2_style))
        story.append(Paragraph(
            f"Date column: <b>{trend_data.get('date_col','')}</b>  |  "
            f"Period: <b>{trend_data.get('period','')}</b>  |  "
            f"Periods: <b>{trend_data.get('n_periods','?')}</b>",
            body_s,
        ))
        tr_data = [["Metric", "Direction", "Slope %/period", "Last MoM %", "Spikes"]]
        for col, mm in trend_data["metrics"].items():
            if not isinstance(mm, dict) or "trend" not in mm:
                continue
            t2  = mm["trend"]
            mom = mm.get("mom_change_pct")
            tr_data.append([
                col,
                t2["direction"].capitalize(),
                f"{t2['slope_pct']:+.2f}%",
                f"{mom:+.1f}%" if mom is not None else "—",
                str(len(mm.get("spikes", []))),
            ])
        t = Table(tr_data, colWidths=[4.5*cm, 2.5*cm, 3*cm, 2.5*cm, 1.5*cm])
        ts2 = _tbl_style()
        for i, row in enumerate(tr_data[1:], 1):
            if "Down" in row[1]:
                ts2.add("TEXTCOLOR", (1, i), (1, i), RED)
            elif "Up" in row[1]:
                ts2.add("TEXTCOLOR", (1, i), (1, i), GREEN)
        t.setStyle(ts2)
        story.append(t)
        story.append(Spacer(1, 8))

    # ── Correlations ──────────────────────────────────────────────────────────
    if corr_data and corr_data.get("columns"):
        story.append(Paragraph("Top Correlations", h2_style))
        cc  = corr_data["columns"]
        mat = corr_data.get("matrix", [])
        pairs = []
        for i in range(len(cc)):
            for j in range(i + 1, len(cc)):
                if i < len(mat) and j < len(mat[i]) and mat[i][j] is not None:
                    pairs.append((cc[i], cc[j], abs(mat[i][j])))
        pairs.sort(key=lambda p: p[2], reverse=True)
        corr_tbl = [["Column A", "Column B", "|r|"]] + [
            [a, b, f"{v:.3f}"] for a, b, v in pairs[:10]
        ]
        t = Table(corr_tbl, colWidths=[5.5*cm, 5.5*cm, 3*cm])
        t.setStyle(_tbl_style())
        story.append(t)
        story.append(Spacer(1, 8))

    # ── Recommendations ───────────────────────────────────────────────────────
    recs = ai_summary.get("recommendations", "")
    if recs:
        story.append(Paragraph("Recommendations", h2_style))
        story.append(Paragraph(recs.replace("\n", "<br/>"), body_s))

    story.append(Spacer(1, 20))
    from datetime import date as _date
    story.append(Paragraph(
        f"Generated by DataLens  |  {_date.today().isoformat()}", caption
    ))

    doc.build(story)
    return buf.getvalue()


def _build_confluence(
    profile: dict,
    clust_data: dict | None,
    trend_data: dict | None,
    corr_data:  dict | None,
    ai_summary: dict,
) -> str:
    from datetime import date as _date

    db, sc, tbl = (profile.get("database",""), profile.get("schema",""),
                   profile.get("table",""))
    rows_c = profile.get("row_count", 0)
    n_col  = profile.get("column_count", 0)
    dated  = (profile.get("profiled_at") or "")[:10]
    cols_p = profile.get("columns", [])
    null_r = sum(c.get("null_rate", 0) for c in cols_p) / max(len(cols_p), 1)

    def _row(*cells):
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"

    def _hrow(*cells):
        return "<tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>"

    exec_s = (ai_summary.get("executive_summary") or "").replace("\n", "<br/>")
    recs_s = (ai_summary.get("recommendations") or "").replace("\n", "<br/>")

    clust_html = ""
    if clust_data:
        m   = clust_data.get("metrics", {})
        sil = m.get("silhouette_score", 0)
        cs_rows = "".join(
            _row(f"Cluster {cs['cluster']}", f"{cs['size']:,}", f"{cs.get('pct',0):.1f}%")
            for cs in clust_data.get("cluster_stats", [])
        )
        clust_html = (
            f"<h2>Cluster Analysis</h2>"
            f"<p>Model: <strong>{clust_data.get('model_label','?')}</strong> | "
            f"Segments: <strong>{clust_data.get('n_clusters','?')}</strong> | "
            f"Silhouette: <strong>{sil:.3f}</strong></p>"
            f"<table><tbody>{_hrow('Cluster','Rows','% Total')}{cs_rows}</tbody></table>"
        )

    trend_html = ""
    if trend_data and trend_data.get("metrics"):
        tr_rows = ""
        for col, mm in trend_data["metrics"].items():
            if not isinstance(mm, dict) or "trend" not in mm:
                continue
            t2  = mm["trend"]
            mom = mm.get("mom_change_pct")
            icon = "▲" if t2["direction"] == "up" else "▼" if t2["direction"] == "down" else "—"
            tr_rows += _row(
                col, f"{icon} {t2['direction'].capitalize()}",
                f"{t2['slope_pct']:+.2f}%",
                f"{mom:+.1f}%" if mom is not None else "—",
                str(len(mm.get("spikes", []))),
            )
        trend_html = (
            f"<h2>Time-Series Trends</h2>"
            f"<p>Date column: <strong>{trend_data.get('date_col','')}</strong> | "
            f"Period: <strong>{trend_data.get('period','')}</strong></p>"
            f"<table><tbody>"
            f"{_hrow('Metric','Direction','Slope %','Last MoM %','Spikes')}"
            f"{tr_rows}</tbody></table>"
        )

    corr_html = ""
    if corr_data and corr_data.get("columns"):
        cc   = corr_data["columns"]
        mat  = corr_data.get("matrix", [])
        pairs = []
        for i in range(len(cc)):
            for j in range(i + 1, len(cc)):
                if i < len(mat) and j < len(mat[i]) and mat[i][j] is not None:
                    pairs.append((cc[i], cc[j], abs(mat[i][j])))
        pairs.sort(key=lambda p: p[2], reverse=True)
        corr_rows = "".join(_row(a, b, f"{v:.3f}") for a, b, v in pairs[:10])
        corr_html = (
            "<h2>Top Correlations</h2>"
            f"<table><tbody>{_hrow('Column A','Column B','|r|')}{corr_rows}</tbody></table>"
        )

    col_rows = "".join(
        _row(
            c["name"],
            c.get("data_type", ""),
            f"{c.get('null_rate',0):.1%}",
            f"{c.get('distinct_count','—'):,}" if isinstance(c.get("distinct_count"), int) else "—",
            f"{c.get('mean','—'):.3f}" if isinstance(c.get("mean"), (int,float)) else "—",
        )
        for c in cols_p[:25]
    )

    return (
        f"<h1>Business Report — {tbl}</h1>"
        f"<p><em>{db}.{sc} | {rows_c:,} rows | {n_col} columns | "
        f"Profiled {dated} | Generated {_date.today().isoformat()}</em></p>"
        "<h2>Key Metrics</h2>"
        f"<table><tbody>"
        f"{_hrow('Metric','Value')}"
        f"{_row('Total Rows', f'{rows_c:,}')}"
        f"{_row('Total Columns', str(n_col))}"
        f"{_row('Avg Null Rate', f'{null_r:.1%}')}"
        f"{_row('Segments', str(clust_data.get('n_clusters','—') if clust_data else '—'))}"
        "</tbody></table>"
        "<h2>Executive Summary</h2>"
        "<ac:structured-macro ac:name=\"info\"><ac:rich-text-body>"
        f"<p>{exec_s}</p>"
        "</ac:rich-text-body></ac:structured-macro>"
        "<h2>Data Quality</h2>"
        f"<table><tbody>"
        f"{_hrow('Column','Type','Null Rate','Distinct','Mean')}"
        f"{col_rows}</tbody></table>"
        + clust_html
        + trend_html
        + corr_html
        + "<h2>Recommendations</h2>"
        f"<p>{recs_s}</p>"
    )


def _post_to_confluence(
    base_url: str, username: str, token: str,
    space_key: str, title: str, content: str,
    parent_id: str = "",
) -> dict:
    import requests
    import base64
    auth    = base64.b64encode(f"{username}:{token}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    payload: dict = {
        "type":  "page",
        "title": title,
        "space": {"key": space_key},
        "body":  {"storage": {"value": content, "representation": "storage"}},
    }
    if parent_id.strip():
        payload["ancestors"] = [{"id": parent_id.strip()}]
    resp = requests.post(
        f"{base_url.rstrip('/')}/wiki/rest/api/content",
        json=payload, headers=headers, timeout=30,
    )
    return {"status": resp.status_code, "body": resp.text[:500]}


# ══════════════════════════════ PAGE ═════════════════════════════════════════

st.title("Business Report")
st.caption(
    "Combines all available analysis into a single executive report. "
    "Export as HTML (print-to-PDF), PDF document, or publish to Confluence."
)

try:
    profiles = persistence.list_profiles(session)
except Exception as e:
    st.error(f"Could not load profiles: {e}")
    st.stop()

if not profiles:
    st.info("No profiles found. Go to **Profile** first.")
    st.stop()

# ── Table selector ────────────────────────────────────────────────────────────

options    = [f"{p.get('database')}.{p.get('schema')}.{p.get('table')}" for p in profiles]
sel_key_ss = (
    f"{st.session_state.get('sel_db')}.{st.session_state.get('sel_schema')}"
    f".{st.session_state.get('sel_table')}"
    if st.session_state.get("sel_db") else None
)
default_idx = options.index(sel_key_ss) if sel_key_ss and sel_key_ss in options else 0
chosen      = st.selectbox("Table", options, index=default_idx, key="rpt_tbl")
parts       = chosen.split(".", 2)
db, schema, table = parts
profile     = profiles[options.index(chosen)]

# Load all available analysis results
clust_data = persistence.load_result(session, "CLUSTERING_RESULTS", db, schema, table)
trend_data = persistence.load_result(session, "TREND_RESULTS",      db, schema, table)
corr_data  = persistence.load_result(session, "CORRELATION_RESULTS", db, schema, table)

# ── Options ───────────────────────────────────────────────────────────────────

st.divider()
opt_c1, opt_c2, opt_c3, opt_c4 = st.columns(4)
with opt_c1:
    inc_clust = st.checkbox("Clustering",    value=clust_data is not None, key="rpt_clust",
                            disabled=clust_data is None)
with opt_c2:
    inc_trend = st.checkbox("Trends",        value=trend_data is not None, key="rpt_trend",
                            disabled=trend_data is None)
with opt_c3:
    inc_corr  = st.checkbox("Correlations",  value=corr_data  is not None, key="rpt_corr",
                            disabled=corr_data  is None)
with opt_c4:
    inc_ai    = st.checkbox("AI Summary",    value=True,                    key="rpt_ai")

if not clust_data:
    st.caption("Clustering: no results — run Clustering page first to include.")
if not trend_data:
    st.caption("Trends: no results — run Trends page first to include.")
if not corr_data:
    st.caption("Correlations: no results — run Report page first to include.")

_eff_clust = clust_data if inc_clust else None
_eff_trend = trend_data if inc_trend else None
_eff_corr  = corr_data  if inc_corr  else None

# ── Generate ──────────────────────────────────────────────────────────────────

st.divider()
gen_col, _ = st.columns([1, 3])
with gen_col:
    gen_clicked = st.button("Generate Report", type="primary",
                            key="rpt_gen", use_container_width=True)

_rpt_key = f"rpt_{db}__{schema}__{table}"

if gen_clicked:
    with st.spinner("Building report …"):
        _ai = {}
        if inc_ai:
            with st.spinner("Generating AI summary …"):
                _ai = cortex.generate_business_summary(
                    session, profile, _eff_clust, _eff_trend, _eff_corr
                )
        st.session_state[_rpt_key] = {
            "profile":    profile,
            "clust_data": _eff_clust,
            "trend_data": _eff_trend,
            "corr_data":  _eff_corr,
            "ai_summary": _ai,
        }

rpt = st.session_state.get(_rpt_key)
if not rpt:
    st.info("Click **Generate Report** to build the report.")
    st.stop()

_prof  = rpt["profile"]
_clust = rpt["clust_data"]
_trend = rpt["trend_data"]
_corr  = rpt["corr_data"]
_ai    = rpt["ai_summary"]

# ══════════════════════════ IN-APP PREVIEW ═══════════════════════════════════

st.divider()

# ── Metric cards ──────────────────────────────────────────────────────────────

_cols_p  = _prof.get("columns", [])
_null_r  = sum(c.get("null_rate", 0) for c in _cols_p) / max(len(_cols_p), 1)
_flags   = [c for c in _cols_p if c.get("null_rate", 0) > 0.2 or c.get("error")]

mc1, mc2, mc3, mc4 = st.columns(4)
mc1.metric("Total Rows",     f"{_prof.get('row_count', 0):,}")
mc2.metric("Columns",        _prof.get("column_count", 0))
mc3.metric("Avg Null Rate",  f"{_null_r:.1%}")
if _clust:
    mc4.metric("Segments", _clust.get("n_clusters", "?"))
else:
    mc4.metric("Quality Issues", len(_flags))

# ── AI Executive Summary ──────────────────────────────────────────────────────

if _ai.get("executive_summary"):
    st.subheader("Executive Summary")
    st.info(_ai["executive_summary"])

# ── Quality section ───────────────────────────────────────────────────────────

st.subheader("Data Quality")
qc1, qc2 = st.columns([2, 1])
with qc1:
    _qfig = _quality_chart(_prof)
    if _qfig:
        st.plotly_chart(_qfig, use_container_width=True)
    else:
        st.success("No null values detected across all columns.")
with qc2:
    _dfig = _dtype_chart(_prof)
    if _dfig:
        st.plotly_chart(_dfig, use_container_width=True)

if _flags:
    with st.expander(f"{len(_flags)} quality issues"):
        st.dataframe(
            pd.DataFrame([{
                "Column":    c["name"],
                "Null Rate": f"{c.get('null_rate',0):.1%}",
                "Issue":     "High nulls" if c.get("null_rate",0) > 0.2 else "Error",
            } for c in _flags]),
            hide_index=True, use_container_width=True,
        )

# ── Analysis sections (conditional) ──────────────────────────────────────────

if _clust:
    st.subheader("Cluster Analysis")
    cl1, cl2 = st.columns([1, 2])
    with cl1:
        _cfig = _cluster_chart(_clust)
        if _cfig:
            st.plotly_chart(_cfig, use_container_width=True)
    with cl2:
        m   = _clust.get("metrics", {})
        cm1, cm2, cm3 = st.columns(3)
        cm1.metric("Silhouette",      f"{m.get('silhouette_score',0):.3f}")
        cm2.metric("Davies-Bouldin",  f"{m.get('davies_bouldin_score',0):.3f}")
        cm3.metric("Model",           _clust.get("model_label", "?"))

if _corr:
    st.subheader("Correlations")
    _cofig = _corr_chart(_corr)
    if _cofig:
        st.plotly_chart(_cofig, use_container_width=True)

if _trend:
    st.subheader("Time-Series Trends")
    tr1, tr2 = st.columns([1, 1])
    with tr1:
        _tfig = _trend_chart(_trend)
        if _tfig:
            st.plotly_chart(_tfig, use_container_width=True)
    with tr2:
        _mets = _trend.get("metrics", {})
        _trows = [
            {
                "Metric":     col,
                "Direction":  ("▲ Up" if m["trend"]["direction"]=="up"
                               else "▼ Down" if m["trend"]["direction"]=="down"
                               else "— Flat"),
                "Slope %":    f"{m['trend']['slope_pct']:+.2f}%",
                "Last MoM":   (f"{m['mom_change_pct']:+.1f}%"
                               if m.get("mom_change_pct") is not None else "—"),
                "Spikes":     len(m.get("spikes", [])),
            }
            for col, m in _mets.items()
            if isinstance(m, dict) and "trend" in m
        ]
        if _trows:
            st.dataframe(pd.DataFrame(_trows), hide_index=True, use_container_width=True)

if _ai.get("recommendations"):
    st.subheader("Recommendations")
    st.success(_ai["recommendations"])

# ══════════════════════════ EXPORT ═══════════════════════════════════════════

st.divider()
st.subheader("Export")

exp_c1, exp_c2, exp_c3 = st.columns(3)

# ── HTML (print-to-PDF) ────────────────────────────────────────────────────────

with exp_c1:
    with st.spinner("Preparing HTML …") if False else st.container():
        _qfig2 = _quality_chart(_prof)
        _dfig2 = _dtype_chart(_prof)
        _cfig2 = _cluster_chart(_clust) if _clust else None
        _cofig2 = _corr_chart(_corr)   if _corr  else None
        _tfig2  = _trend_chart(_trend)  if _trend else None

        def _fig_html(fig):
            return fig.to_html(include_plotlyjs=False, full_html=False) if fig else ""

        _charts_html = {
            "quality":      _fig_html(_qfig2),
            "dtypes":       _fig_html(_dfig2),
            "clusters":     _fig_html(_cfig2),
            "correlations": _fig_html(_cofig2),
            "trends":       _fig_html(_tfig2),
        }
        _html_str = _build_html(_prof, _clust, _trend, _corr, _ai, _charts_html)

    st.download_button(
        "Download HTML",
        data=_html_str,
        file_name=f"report_{table}.html",
        mime="text/html",
        key="rpt_dl_html",
        use_container_width=True,
        help="Open in a browser and use File → Print → Save as PDF",
    )
    st.caption("Interactive charts included. Print as PDF from your browser.")

# ── PDF ────────────────────────────────────────────────────────────────────────

with exp_c2:
    if st.button("Generate PDF", key="rpt_gen_pdf", use_container_width=True):
        with st.spinner("Building PDF …"):
            try:
                _pdf_bytes = _build_pdf(_prof, _clust, _trend, _corr, _ai)
                st.session_state["rpt_pdf_bytes"] = _pdf_bytes
                st.success("PDF ready.")
            except Exception as _pe:
                st.error(f"PDF generation failed: {_pe}")

    if st.session_state.get("rpt_pdf_bytes"):
        st.download_button(
            "Download PDF",
            data=st.session_state["rpt_pdf_bytes"],
            file_name=f"report_{table}.pdf",
            mime="application/pdf",
            key="rpt_dl_pdf",
            use_container_width=True,
        )
    st.caption("Tables + text. Charts not included in PDF.")

# ── Confluence ─────────────────────────────────────────────────────────────────

with exp_c3:
    if st.button("Publish to Confluence", key="rpt_conf_open", use_container_width=True):
        st.session_state["rpt_conf_panel"] = not st.session_state.get("rpt_conf_panel", False)

if st.session_state.get("rpt_conf_panel"):
    with st.container():
        st.markdown("**Confluence Settings**")
        cf1, cf2 = st.columns(2)
        with cf1:
            _conf_url = st.text_input(
                "Confluence URL",
                value=st.session_state.get("confluence_url", "https://company.atlassian.net"),
                key="conf_url",
                placeholder="https://yourcompany.atlassian.net",
            )
            _conf_space = st.text_input(
                "Space Key",
                value=st.session_state.get("confluence_space", ""),
                key="conf_space",
            )
            _conf_parent = st.text_input(
                "Parent Page ID (optional)",
                value="", key="conf_parent",
            )
        with cf2:
            _conf_user = st.text_input(
                "Username / Email",
                value=st.session_state.get("confluence_username", ""),
                key="conf_user",
            )
            _conf_token = st.text_input(
                "API Token",
                type="password", key="conf_token",
            )
            _conf_title = st.text_input(
                "Page Title",
                value=f"DataLens Report — {table}",
                key="conf_title",
            )

        if st.button("Publish Now", key="rpt_conf_publish", type="primary"):
            _missing = [f for f, v in [
                ("URL", _conf_url), ("Space Key", _conf_space),
                ("Username", _conf_user), ("API Token", _conf_token),
                ("Page Title", _conf_title),
            ] if not v]
            if _missing:
                st.error(f"Fill in: {', '.join(_missing)}")
            else:
                try:
                    persistence.save_app_setting(session, "confluence_url",      _conf_url)
                    persistence.save_app_setting(session, "confluence_space",    _conf_space)
                    persistence.save_app_setting(session, "confluence_username", _conf_user)
                    st.session_state["confluence_url"]      = _conf_url
                    st.session_state["confluence_space"]    = _conf_space
                    st.session_state["confluence_username"] = _conf_user
                except Exception:
                    pass

                with st.spinner("Publishing to Confluence …"):
                    _conf_content = _build_confluence(
                        _prof, _clust, _trend, _corr, _ai
                    )
                    _resp = _post_to_confluence(
                        _conf_url, _conf_user, _conf_token,
                        _conf_space, _conf_title, _conf_content,
                        _conf_parent,
                    )

                if _resp["status"] in (200, 201):
                    st.success("Page published successfully.")
                else:
                    st.error(
                        f"Confluence returned HTTP {_resp['status']}. "
                        f"Check URL, space key, and API token.\n\n"
                        f"Response: {_resp['body']}"
                    )
