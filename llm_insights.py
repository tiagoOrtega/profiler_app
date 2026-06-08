"""
Cluster insights engine — generates AI summaries and actionable insights
from clustering results using the configured LLM provider.

When no LLM is available (or the provider is 'disabled'), deterministic
rule-based insights are produced directly from the cluster statistics.

Output format
-------------
{
  "provider":     "ollama",
  "model":        "llama3.2",
  "cluster_insights": {
    "0": {
      "summary": "...",
      "characteristics": ["...", "..."],
      "label": "Premium Buyers"
    },
    ...
  },
  "global_insights": "Bullet-point narrative...",
  "generated_at": "2024-...",
  "rule_based": false
}
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_providers import BaseLLMProvider


# ── Prompt builders ────────────────────────────────────────────────────────────

def _cluster_prompt(stat: dict, columns: list[str], table_info: dict, total_rows: int) -> str:
    cid  = stat["cluster"]
    size = stat["size"]
    pct  = stat["pct"]
    cent = stat.get("centroid", {})

    # Top 10 columns by largest absolute value (most discriminating)
    top = sorted(cent.items(), key=lambda kv: abs(kv[1]) if kv[1] else 0, reverse=True)[:10]
    stats_block = "\n".join(f"  {k}: {v:.4g}" for k, v in top) or "  (no statistics available)"

    return f"""\
You are a data analyst reviewing one cluster from a machine learning clustering analysis.

Table: {table_info.get('database','?')}.{table_info.get('schema','?')}.{table_info.get('table','?')}
Total rows in sample: {total_rows:,}

Cluster {cid} — {size:,} rows ({pct:.1f}% of data)
Mean feature values:
{stats_block}

Write a concise analysis. Return ONLY valid JSON with these keys:
{{
  "summary": "<one sentence describing what makes this cluster distinctive>",
  "characteristics": ["<trait 1>", "<trait 2>", "<trait 3>"],
  "label": "<3-5 word cluster name>"
}}"""


def _global_prompt(result: dict) -> str:
    n      = result.get("n_clusters", 0)
    model  = result.get("model_label") or result.get("model", "")
    stats  = result.get("cluster_stats", [])
    cols   = result.get("columns_used", [])
    m      = result.get("metrics", {})

    lines = "\n".join(
        f"  Cluster {s['cluster']}: {s['size']:,} rows ({s['pct']:.1f}%)"
        for s in stats if s["cluster"] >= 0
    )
    sil = m.get("silhouette_score")
    quality_note = f"Silhouette score {sil:.3f} — " + (
        "good cluster separation" if sil and sil > 0.5 else
        "moderate separation" if sil and sil > 0.25 else
        "weak separation — consider adjusting k or features"
    ) if sil else "Quality metrics unavailable"

    return f"""\
You are a senior data analyst. A {model} clustering on {len(cols)} numeric features \
produced {n} groups from this dataset.

Cluster distribution:
{lines}

Cluster quality: {quality_note}
Features used: {', '.join(cols[:12])}{'…' if len(cols) > 12 else ''}

Provide 4 concise, actionable business insights about what this clustering reveals.
Format as a plain bulleted list (start each bullet with •).
Focus on patterns, anomalies, segment differences, and potential business value."""


# ── JSON parser (LLM output is noisy) ─────────────────────────────────────────

def _parse_cluster_json(text: str) -> dict | None:
    for attempt in [
        text,
        re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL),
        re.search(r"\{[\s\S]*\}", text),
    ]:
        blob = attempt.group(1) if hasattr(attempt, "group") else (attempt or "")
        if blob:
            try:
                d = json.loads(blob)
                if isinstance(d, dict):
                    return d
            except Exception:
                continue
    return None


def _extract_bullets(text: str) -> list[str]:
    """Extract bullet points or numbered items from raw text."""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"^[•\-*\d]", line):
            line = re.sub(r"^[•\-*\d.]+\s*", "", line)
        if len(line) > 15:
            lines.append(line)
    return lines[:5]


# ── Rule-based fallback ────────────────────────────────────────────────────────

def _rule_based_cluster(stat: dict, columns: list[str], all_stats: list[dict]) -> dict:
    """Generate a cluster summary without an LLM using statistical reasoning."""
    cid  = stat["cluster"]
    size = stat["size"]
    pct  = stat["pct"]
    cent = stat.get("centroid", {})

    # Compare each column to the overall mean (approximate from all clusters)
    overall: dict[str, float] = {}
    for other in all_stats:
        for col, val in (other.get("centroid") or {}).items():
            overall.setdefault(col, []).append(val)
    overall = {col: sum(vs)/len(vs) for col, vs in overall.items() if vs}

    high, low = [], []
    for col, val in cent.items():
        ref = overall.get(col, val)
        if ref and abs(ref) > 1e-9:
            ratio = (val - ref) / abs(ref)
            if ratio > 0.30:
                high.append(f"High {col} ({val:.3g})")
            elif ratio < -0.30:
                low.append(f"Low {col} ({val:.3g})")

    traits = (high[:2] + low[:2])[:3] or [f"{cid} distinct pattern"]

    size_desc = "large" if pct > 35 else "small" if pct < 15 else "medium-sized"
    summary = (
        f"Cluster {cid} is a {size_desc} group ({pct:.1f}% of data) "
        f"characterised by {', '.join(t.lower() for t in traits[:2]) or 'distinctive feature values'}."
    )
    label = " / ".join(t.split()[0:2] for t in traits[:2]) or f"Cluster {cid}"

    return {"summary": summary, "characteristics": traits, "label": label}


def _rule_based_global(result: dict) -> str:
    n     = result.get("n_clusters", 0)
    stats = result.get("cluster_stats", [])
    m     = result.get("metrics", {})
    sil   = m.get("silhouette_score")

    sizes = [(s["cluster"], s["pct"]) for s in stats if s["cluster"] >= 0]
    dominant = max(sizes, key=lambda x: x[1]) if sizes else None

    bullets = []
    bullets.append(
        f"• The data separates into {n} distinct groups with "
        + (f"silhouette score {sil:.3f}." if sil else "measurable separation.")
    )
    if dominant:
        bullets.append(
            f"• Cluster {dominant[0]} is the dominant segment ({dominant[1]:.1f}% of rows)."
        )
    if sil and sil < 0.25:
        bullets.append(
            "• Weak cluster separation detected — consider reducing k, removing correlated "
            "features, or trying DBSCAN for non-spherical clusters."
        )
    elif sil and sil > 0.5:
        bullets.append("• Strong cluster separation suggests the features well-discriminate the groups.")
    bullets.append(
        "• Review per-cluster centroids to understand which features drive each segment."
    )
    return "\n".join(bullets)


# ── Main engine ────────────────────────────────────────────────────────────────

class InsightsEngine:

    def __init__(self, provider: "BaseLLMProvider"):
        self.provider = provider

    def generate(self, cluster_result: dict) -> dict:
        """
        Generate per-cluster and global insights.
        Returns a dict suitable for merging into the clustering JSON.
        """
        from datetime import datetime

        use_llm   = self.provider.is_available() and self.provider.provider_id != "disabled"
        stats     = cluster_result.get("cluster_stats", [])
        columns   = cluster_result.get("columns_used", [])
        total     = cluster_result.get("sample_size", 0)
        table_info = {
            "database": cluster_result.get("database", ""),
            "schema":   cluster_result.get("schema",   ""),
            "table":    cluster_result.get("table",    ""),
        }

        cluster_insights: dict[str, dict] = {}
        global_insights  = ""
        errors: list[str] = []

        for s in stats:
            cid = str(s["cluster"])
            if s["cluster"] == -1:
                cluster_insights[cid] = {
                    "summary": "Noise points — rows that do not belong to any cluster (DBSCAN outliers).",
                    "characteristics": ["Low density region", "Potential outliers", "Does not fit cluster model"],
                    "label": "Noise / Outliers",
                }
                continue

            if use_llm:
                try:
                    raw    = self.provider.generate(
                        _cluster_prompt(s, columns, table_info, total),
                        temperature=0.3, max_tokens=400,
                    )
                    parsed = _parse_cluster_json(raw)
                    if parsed and parsed.get("summary"):
                        cluster_insights[cid] = {
                            "summary":         parsed.get("summary", ""),
                            "characteristics": parsed.get("characteristics", []),
                            "label":           parsed.get("label", f"Cluster {s['cluster']}"),
                        }
                        continue
                    # LLM returned non-JSON — use raw text as summary
                    bullets = _extract_bullets(raw)
                    cluster_insights[cid] = {
                        "summary":         raw[:300] if raw else "",
                        "characteristics": bullets[:3],
                        "label":           f"Cluster {s['cluster']}",
                    }
                    continue
                except Exception as exc:
                    errors.append(f"Cluster {cid}: {exc}")

            # Rule-based fallback
            cluster_insights[cid] = _rule_based_cluster(s, columns, stats)

        # Global insights
        if use_llm:
            try:
                raw = self.provider.generate(
                    _global_prompt(cluster_result),
                    temperature=0.3, max_tokens=600,
                )
                global_insights = raw.strip()
            except Exception as exc:
                errors.append(f"Global insights: {exc}")

        if not global_insights:
            global_insights = _rule_based_global(cluster_result)

        return {
            "provider":         self.provider.provider_id,
            "model":            self.provider.model_name,
            "cluster_insights": cluster_insights,
            "global_insights":  global_insights,
            "rule_based":       not use_llm,
            "errors":           errors,
            "generated_at":     datetime.now().isoformat(),
        }
