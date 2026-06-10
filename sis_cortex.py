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
