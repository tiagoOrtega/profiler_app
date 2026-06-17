"""
DataLens — AI integration for Streamlit in Snowflake.

Supports two providers (switchable via session_state["ai_provider"]):
  cortex  — SNOWFLAKE.CORTEX.COMPLETE()  (default; works in SiS and remote connections)
  ollama  — Local Ollama REST API         (local development only; http://localhost:11434)

All public functions degrade gracefully: on provider failure the rule-based fallback runs.
"""

from __future__ import annotations

import json
import re
from typing import Optional

# ── Provider constants ────────────────────────────────────────────────────────

PROVIDER_CORTEX = "cortex"
PROVIDER_OLLAMA = "ollama"

DEFAULT_MODEL        = "mistral-large"
OLLAMA_DEFAULT_URL   = "http://localhost:11434"
OLLAMA_DEFAULT_MODEL = "llama3.2"

AVAILABLE_MODELS = [
    "mistral-large",
    "mistral-large2",
    "mixtral-8x7b",
    "llama3.1-70b",
    "llama3.1-8b",
    "llama3-70b",
    "snowflake-arctic",
]


# ── Session-state helpers ─────────────────────────────────────────────────────

def _get_provider() -> str:
    try:
        import streamlit as st
        return st.session_state.get("ai_provider", PROVIDER_CORTEX)
    except Exception:
        return PROVIDER_CORTEX


def _model(session) -> str:
    try:
        import streamlit as st
        return st.session_state.get("cortex_model", DEFAULT_MODEL)
    except Exception:
        return DEFAULT_MODEL


def _ollama_url() -> str:
    try:
        import streamlit as st
        return st.session_state.get("ollama_url", OLLAMA_DEFAULT_URL)
    except Exception:
        return OLLAMA_DEFAULT_URL


def _ollama_model() -> str:
    try:
        import streamlit as st
        return st.session_state.get("ollama_model", OLLAMA_DEFAULT_MODEL)
    except Exception:
        return OLLAMA_DEFAULT_MODEL


# ── Ollama helpers ────────────────────────────────────────────────────────────

def ollama_list_models(base_url: str = "") -> list[str]:
    """Return model names pulled in Ollama. Empty list on any failure."""
    url = (base_url or _ollama_url()).rstrip("/")
    try:
        import requests
        r = requests.get(f"{url}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def ollama_is_available(base_url: str = "") -> bool:
    url = (base_url or _ollama_url()).rstrip("/")
    try:
        import requests
        r = requests.get(f"{url}/api/tags", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _ollama_complete(prompt: str, model: str, base_url: str) -> str:
    import requests
    payload = {
        "model":  model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 512},
    }
    r = requests.post(f"{base_url.rstrip('/')}/api/generate", json=payload, timeout=120)
    r.raise_for_status()
    return r.json().get("response", "").strip()


# ── Low-level completion ──────────────────────────────────────────────────────

def complete(session, prompt: str, model: Optional[str] = None) -> str:
    """Run an LLM completion via the active provider. Returns empty string on failure."""
    provider = _get_provider()

    if provider == PROVIDER_OLLAMA:
        base_url = _ollama_url()
        m        = model or _ollama_model()
        try:
            return _ollama_complete(prompt, m, base_url)
        except Exception:
            return ""

    # Cortex (default)
    m    = model or _model(session)
    safe = prompt.replace("$$", "$ $")
    try:
        row = session.sql(
            f"SELECT SNOWFLAKE.CORTEX.COMPLETE('{m}', $${safe}$$)"
        ).first()
        return str(row[0]).strip() if row and row[0] else ""
    except Exception:
        return ""


# ── Table / column comments ───────────────────────────────────────────────────

def generate_table_comment(session, profile: dict, model: Optional[str] = None) -> str:
    cols = ", ".join(
        f"{c['name']} ({c['data_type']})"
        for c in profile.get("columns", [])[:20]
    )
    prompt = (
        f"You are a data analyst. Write a concise 1-2 sentence description for this table.\n\n"
        f"Table: {profile.get('table', '')}\n"
        f"Database: {profile.get('database', '')}.{profile.get('schema', '')}\n"
        f"Row count: {profile.get('row_count', '?')}\n"
        f"Columns: {cols}\n\n"
        f"Return only the description, no quotes or markdown."
    )
    result = complete(session, prompt, model)
    if result:
        return result
    return (
        f"Table {profile.get('table', '')} in {profile.get('database', '')}"
        f".{profile.get('schema', '')} with {profile.get('row_count', 0):,} rows "
        f"and {profile.get('column_count', 0)} columns."
    )


def generate_column_comments(
    session, profile: dict, model: Optional[str] = None
) -> dict[str, str]:
    """Return {column_name: description} for all columns in profile."""
    cols_text = "\n".join(
        f"- {c['name']}: {c['data_type']}"
        + (f", {c.get('distinct_count', '?')} distinct values" if c.get("distinct_count") else "")
        + (f", null rate {c['null_rate']:.1%}" if c.get("null_rate", 0) > 0 else "")
        for c in profile.get("columns", [])
    )
    prompt = (
        f"You are a data analyst. For the table {profile.get('table', '')}, "
        f"write a short 1-sentence description for each column.\n\n"
        f"Columns:\n{cols_text}\n\n"
        f'Return a JSON object: {{"COLUMN_NAME": "description", ...}}\n'
        f"Descriptions should be specific and business-oriented."
    )
    result = complete(session, prompt, model)
    if result:
        try:
            match = re.search(r"\{[^{}]+\}", result, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception:
            pass

    return {c["name"]: _rule_column(c["name"], c["data_type"])
            for c in profile.get("columns", [])}


def _rule_column(name: str, data_type: str) -> str:
    nl = name.lower()
    if nl.endswith("_key"):   return f"Surrogate key for {name[:-4].replace('_', ' ')}"
    if nl.endswith("_id"):    return f"Identifier for {name[:-3].replace('_', ' ')}"
    if "date" in nl:          return f"Date of {name.replace('_', ' ').lower()}"
    if nl.endswith("_at"):    return f"Timestamp when {name[:-3].replace('_', ' ')} occurred"
    if "amount" in nl:        return f"Monetary amount for {name.replace('_', ' ').lower()}"
    if "price" in nl:         return f"Price value for {name.replace('_', ' ').lower()}"
    if "cost" in nl:          return f"Cost figure for {name.replace('_', ' ').lower()}"
    if "qty" in nl or "quantity" in nl: return f"Quantity of {name.replace('_', ' ').lower()}"
    if "count" in nl:         return f"Count of {name[:-6].replace('_', ' ')}"
    if nl.startswith("is_"):  return f"Boolean flag: {name[3:].replace('_', ' ')}"
    if "name" in nl:          return f"Name of {name.replace('_name', '').replace('_', ' ')}"
    return f"Value for {name.replace('_', ' ').lower()}"


# ── Apply comments to source table ────────────────────────────────────────────

def apply_column_comments(
    session, database: str, schema: str, table: str, comments: dict[str, str]
) -> list[str]:
    """ALTER TABLE ... ALTER COLUMN ... COMMENT. Returns list of error strings."""
    errors = []
    for col_name, comment in comments.items():
        if not comment:
            continue
        safe = comment.replace("'", "''")
        try:
            session.sql(
                f'ALTER TABLE "{database}"."{schema}"."{table}" '
                f'ALTER COLUMN "{col_name}" COMMENT \'{safe}\''
            ).collect()
        except Exception as e:
            errors.append(f"{col_name}: {e}")
    return errors


def apply_table_comment(
    session, database: str, schema: str, table: str, comment: str
) -> Optional[str]:
    safe = comment.replace("'", "''")
    try:
        session.sql(
            f'ALTER TABLE "{database}"."{schema}"."{table}" SET COMMENT = \'{safe}\''
        ).collect()
        return None
    except Exception as e:
        return str(e)


# ── Correlation explanation ───────────────────────────────────────────────────

def explain_correlations(
    session, corr_data: dict, model: Optional[str] = None
) -> str:
    cols   = corr_data.get("columns", [])
    matrix = corr_data.get("matrix", [])
    high, low = [], []
    for i, ci in enumerate(cols):
        for j, cj in enumerate(cols):
            if i >= j:
                continue
            v = matrix[i][j] if i < len(matrix) and j < len(matrix[i]) else None
            if v is None:
                continue
            if abs(v) >= 0.7:
                high.append(f"{ci} <-> {cj}: r={v:.3f}")
            elif abs(v) <= 0.1:
                low.append(f"{ci} <-> {cj}: r={v:.3f}")

    high_text = "\n".join(high[:10]) or "None found"
    low_text  = "\n".join(low[:5])   or "None found"

    prompt = (
        f"Analyze these column correlations and provide 3-5 business insights.\n\n"
        f"Strong correlations (|r| >= 0.7):\n{high_text}\n\n"
        f"Weak/no correlations (|r| <= 0.1):\n{low_text}\n\n"
        f"Focus on what these patterns mean for data quality and business decisions. "
        f"Be concise and specific."
    )
    result = complete(session, prompt, model)
    return result or "No AI insights available. Check AI provider configuration."


# ── Feature engineering recommendations ──────────────────────────────────────

def suggest_feature_engineering(
    session, profile: dict, suggestions: list, model: Optional[str] = None
) -> str:
    """Return AI-written rationale for which feature transforms to include in clustering."""
    orig_text = "\n".join(
        f"  {s['id']}: skewness={s.get('skewness', 0):.2f}"
        for s in suggestions if s["type"] == "original"
    )[:800]

    transforms = [s for s in suggestions if s["type"] != "original"]
    tx_text = "\n".join(
        f"  {s['id']}  ({s['type']}): {s['description']}"
        for s in transforms[:15]
    )

    prompt = (
        f"You are a data scientist preparing features for unsupervised clustering.\n\n"
        f"Table: {profile.get('table', '')} — {profile.get('row_count', '?'):,} rows\n"
        f"Original columns (with skewness):\n{orig_text}\n\n"
        f"Available transformations:\n{tx_text}\n\n"
        f"Recommend which transforms to include. For each recommended transform explain "
        f"the statistical reason in one sentence (e.g. 'log1p_X because skewness=3.2 "
        f"distorts Euclidean distance in K-Means'). Limit to the 3-5 most impactful. "
        f"Be concise and specific."
    )
    result = complete(session, prompt, model)
    return result or "No AI recommendation available. Check AI provider configuration."


# ── Business report summary ───────────────────────────────────────────────────

def generate_business_summary(
    session,
    profile:    dict,
    clust_data: "dict | None" = None,
    trend_data: "dict | None" = None,
    corr_data:  "dict | None" = None,
    model:      Optional[str] = None,
) -> dict:
    """Return {executive_summary, recommendations} for the business report."""
    rows     = profile.get("row_count", 0)
    cols_all = profile.get("columns", [])
    numeric  = [c for c in cols_all if c.get("mean") is not None]
    nullish  = [c for c in cols_all if c.get("null_rate", 0) > 0.2]

    ctx = [
        f"Table: {profile.get('database')}.{profile.get('schema')}.{profile.get('table')}",
        f"Size: {rows:,} rows, {profile.get('column_count', 0)} columns "
        f"({len(numeric)} numeric)",
        f"Columns with >20% nulls: {len(nullish)}",
    ]

    if clust_data and clust_data.get("n_clusters"):
        sil = clust_data.get("metrics", {}).get("silhouette_score", 0)
        ctx.append(
            f"Clustering: {clust_data['n_clusters']} segments, "
            f"model={clust_data.get('model_label', '')}, silhouette={sil:.3f}"
        )

    if trend_data and trend_data.get("metrics"):
        mets = trend_data["metrics"]
        up   = sum(1 for m in mets.values()
                   if isinstance(m, dict) and m.get("trend", {}).get("direction") == "up")
        dn   = sum(1 for m in mets.values()
                   if isinstance(m, dict) and m.get("trend", {}).get("direction") == "down")
        ctx.append(
            f"Trends ({trend_data.get('period','month')}ly): "
            f"{up} growing, {dn} declining out of {len(mets)} metrics"
        )

    if corr_data and corr_data.get("columns"):
        cols_c  = corr_data["columns"]
        matrix  = corr_data.get("matrix", [])
        high_cr = []
        for i in range(len(cols_c)):
            for j in range(i + 1, len(cols_c)):
                if i < len(matrix) and j < len(matrix[i]) and matrix[i][j] is not None:
                    if abs(matrix[i][j]) >= 0.7:
                        high_cr.append(f"{cols_c[i]}<->{cols_c[j]}")
        if high_cr:
            ctx.append(f"Strong correlations: {', '.join(high_cr[:4])}")

    context = "\n".join(ctx)
    prompt = (
        f"You are a senior data analyst writing an executive report for business stakeholders.\n\n"
        f"Data context:\n{context}\n\n"
        f"Write exactly two clearly labelled sections:\n\n"
        f"EXECUTIVE SUMMARY:\n"
        f"2-3 sentences covering what this dataset is, its quality status, and the most "
        f"important finding. Avoid technical jargon.\n\n"
        f"RECOMMENDATIONS:\n"
        f"3-5 specific, actionable bullet points (start each with a dash '-') for the data or "
        f"business team. Be concrete, not generic."
    )
    raw = complete(session, prompt, model)

    if not raw:
        return {
            "executive_summary": "AI insights unavailable. Check provider in Configuration.",
            "recommendations":   "",
        }

    exec_sum, recs = raw, ""
    for marker in ("RECOMMENDATIONS:", "RECOMMENDATIONS\n", "Recommendations:"):
        if marker in raw:
            idx      = raw.index(marker)
            exec_sum = raw[:idx].replace("EXECUTIVE SUMMARY:", "").replace(
                "EXECUTIVE SUMMARY\n", "").strip()
            recs     = raw[idx + len(marker):].strip()
            break

    return {"executive_summary": exec_sum, "recommendations": recs}


# ── Clustering insights ───────────────────────────────────────────────────────

def explain_clusters(
    session, cluster_stats: list, feature_names: list, model: Optional[str] = None
) -> str:
    segments = ""
    for cs in cluster_stats:
        label    = cs.get("label", cs.get("cluster", "?"))
        size     = cs.get("size", 0)
        centroid = cs.get("centroid", {})
        segments += f"\nCluster {label} ({size:,} points):\n"
        if isinstance(centroid, dict):
            for fn in feature_names[:8]:
                if fn in centroid:
                    segments += f"  {fn}: {centroid[fn]:.3f}\n"
        else:
            for fn, cv in zip(feature_names[:8], centroid):
                try:
                    segments += f"  {fn}: {float(cv):.3f}\n"
                except (TypeError, ValueError):
                    segments += f"  {fn}: {cv}\n"

    prompt = (
        f"Analyze these data segments and describe each cluster in business terms.\n"
        f"Features: {', '.join(feature_names)}\n"
        f"{segments}\n"
        f"For each cluster provide: a short descriptive name, key characteristics, "
        f"and a business interpretation. Be concise and specific."
    )
    result = complete(session, prompt, model)
    return result or "No AI insights available. Check AI provider configuration."


def explain_trends(
    session,
    date_col: str,
    period: str,
    metrics: dict,
    model: Optional[str] = None,
) -> str:
    """Return AI interpretation of time-series trend results."""
    lines = []
    for col, m in metrics.items():
        if not isinstance(m, dict) or "trend" not in m:
            continue
        t   = m["trend"]
        mom = m.get("mom_change_pct")
        spk = len(m.get("spikes", []))
        mom_str = f", last {period}-over-{period}: {mom:+.1f}%" if mom is not None else ""
        spk_str = f", {spk} spike(s)" if spk else ""
        lines.append(
            f"  {col}: {t['direction']} trend "
            f"({t['slope_pct']:+.1f}%/{period}, R²={t['r2']:.2f})"
            f"{mom_str}{spk_str}"
        )

    summary = "\n".join(lines[:10]) or "No trend data available."
    prompt = (
        f"You are a data analyst reviewing time-series trends in a business dataset.\n\n"
        f"Date column: {date_col}  |  Period: {period}\n"
        f"Metric trends:\n{summary}\n\n"
        f"Write 3-5 bullet points interpreting these trends in business terms:\n"
        f"• Which metrics are growing / declining and what might drive this?\n"
        f"• Are there any concerning patterns or spikes worth investigating?\n"
        f"• What actions or further analyses would you recommend?\n"
        f"Be specific and concise."
    )
    result = complete(session, prompt, model)
    return result or "No AI insights available. Check AI provider configuration."


def explain_visual_insights(
    session,
    cluster_stats: list,
    feature_names: list,
    divergence: list,
    model: Optional[str] = None,
) -> str:
    top_div  = sorted(divergence, key=lambda d: d["Divergence"], reverse=True)[:6]
    div_text = "\n".join(f"  {d['Feature']}: {d['Divergence']:.3f}" for d in top_div)

    segments = ""
    for cs in cluster_stats:
        label    = cs.get("label", cs.get("cluster", "?"))
        size     = cs.get("size", 0)
        centroid = cs.get("centroid", {})
        segments += f"\nCluster {label} ({size:,} points):\n"
        if isinstance(centroid, dict):
            for fn in feature_names[:6]:
                if fn in centroid:
                    segments += f"  {fn}: {centroid[fn]:.3f}\n"

    prompt = (
        f"You are a data analyst reviewing ML clustering results.\n\n"
        f"Most discriminative features (divergence = inter-cluster mean spread / overall std):\n"
        f"{div_text}\n"
        f"Cluster centroids:\n{segments}\n"
        f"Write a concise interpretation using bullet points:\n"
        f"• Which features drive the most separation between clusters?\n"
        f"• What does each cluster represent in business terms?\n"
        f"• What action or next step could be taken based on these patterns?\n"
        f"Use 3-5 bullet points. Be specific and business-oriented."
    )
    result = complete(session, prompt, model)
    return result or "No AI insights available. Check AI provider configuration."


def explain_geolocation(
    session,
    geo_data: dict,
    model: Optional[str] = None,
) -> str:
    """Generate AI insights from aggregated geolocation sales data."""
    by_country   = sorted(
        geo_data.get("by_country", []),
        key=lambda x: x.get("total_revenue", 0), reverse=True,
    )
    by_continent = geo_data.get("by_continent", [])

    ctx = [
        f"Table: {geo_data.get('table', 'GOLD_FACT_ORDERS')}",
        f"Date range: {geo_data.get('min_date', '?')} to {geo_data.get('max_date', '?')}",
        f"Total orders: {geo_data.get('total_orders', 0):,}",
        f"Countries active: {geo_data.get('n_countries', 0)}",
        f"Cities active: {geo_data.get('n_cities', 0)}",
        "",
        "Revenue by continent:",
    ]
    for row in sorted(by_continent, key=lambda r: r.get("total_revenue", 0), reverse=True):
        ctx.append(
            f"  {row['continent']}: ${row['total_revenue']:,.0f}  "
            f"({row.get('pct_revenue', 0):.1f}%)  {row.get('order_count', 0):,} orders"
        )

    ctx.append("\nTop 10 countries by revenue:")
    for row in by_country[:10]:
        ctx.append(
            f"  {row['country']} ({row['continent']}): "
            f"${row['total_revenue']:,.0f}  |  "
            f"{row.get('order_count', 0):,} orders  |  "
            f"avg ${row.get('avg_order_value', 0):,.0f}"
        )

    ctx.append("\nTop 5 cities by revenue:")
    for row in geo_data.get("by_city", [])[:5]:
        ctx.append(
            f"  {row.get('city')}, {row.get('country')}: "
            f"${row.get('total_revenue', 0):,.0f}  ({row.get('order_count', 0):,} orders)"
        )

    prompt = (
        "You are a senior business intelligence analyst with expertise in global market strategy. "
        "Analyze the following worldwide sales geolocation data and provide 4-6 specific, "
        "actionable insights covering: (1) top-performing markets and why, (2) underperforming "
        "regions with growth potential, (3) regional patterns or anomalies, (4) recommended "
        "geographic expansion or investment priorities.\n\n"
        + "\n".join(ctx)
        + "\n\nFormat as bullet points. Be specific, data-driven, and actionable."
    )

    result = complete(session, prompt, model)
    if result:
        return result

    top3 = by_country[:3]
    if not top3:
        return "No geolocation data available."
    leaders = ", ".join(
        f"{r['country']} (${r['total_revenue']:,.0f})" for r in top3
    )
    conts = {r["continent"] for r in by_country}
    return (
        f"Top markets: {leaders}. "
        f"Operations span {len(by_country)} countries across {len(conts)} continents. "
        f"Consider expanding presence in under-indexed regions to diversify revenue."
    )
