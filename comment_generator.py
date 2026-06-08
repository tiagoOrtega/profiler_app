"""
Auto-generate table and column COMMENT metadata using LLM or rule-based fallback.

Both table-level and column-level comments are generated in one LLM round-trip.
The rule-based fallback uses column-name patterns and statistical properties
so useful comments are always produced even without an LLM.

Output format
-------------
{
  "table_comment":    "...",
  "column_comments":  {"COL": "...", ...},
  "rule_based":       bool,
  "provider":         str,
  "model":            str,
  "generated_at":     str,
}
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_providers import BaseLLMProvider


# ── Rule-based column comment ─────────────────────────────────────────────────

_ENDS = {
    "_key":    "Surrogate key — FK to {tbl} dimension.",
    "_sk":     "Surrogate key — FK to {tbl} dimension.",
    "_id":     "Business/natural key. Stable identifier for cross-system joins.",
    "_nk":     "Natural key. Stable identifier for cross-system joins.",
    "_date":   "Date column. Range: {min_d} to {max_d}.",
    "_dt":     "Date column. Range: {min_d} to {max_d}.",
    "_at":     "Timestamp column.",
    "_amount": "Monetary amount (USD). Range: {min_v} to {max_v}.",
    "_amt":    "Monetary amount (USD). Range: {min_v} to {max_v}.",
    "_total":  "Aggregate total. Range: {min_v} to {max_v}.",
    "_price":  "Unit price (USD). Range: {min_v} to {max_v}.",
    "_cost":   "Unit cost (USD). Range: {min_v} to {max_v}.",
    "_qty":    "Quantity. Range: {min_v} to {max_v}.",
    "_count":  "Count metric. Range: {min_v} to {max_v}.",
    "_cnt":    "Count metric.",
    "_num":    "Numeric measure.",
    "_pct":    "Percentage (0–100).",
    "_rate":   "Rate or ratio expressed as a decimal (0.0–1.0).",
    "_ratio":  "Ratio. Range: {min_v} to {max_v}.",
    "_score":  "Score metric. Range: {min_v} to {max_v}.",
    "_flag":   "Boolean indicator. TRUE/FALSE.",
    "_ind":    "Boolean indicator. TRUE/FALSE.",
    "_name":   "Descriptive name. {dist} distinct values.",
    "_label":  "Display label. {dist} distinct values.",
    "_desc":   "Free-text description.",
    "_code":   "Standardised code. {dist} distinct values.",
    "_type":   "Type/category code. {dist} distinct values.",
    "_status": "Status code. {dist} distinct values.",
    "_segment":"Segment or tier. {dist} distinct values.",
    "_method": "Method or approach indicator. {dist} distinct values.",
    "_channel":"Channel classification. {dist} distinct values.",
}

_STARTS = {
    "is_":     "Boolean flag. TRUE/FALSE.",
    "has_":    "Boolean flag. TRUE/FALSE.",
    "can_":    "Permission flag. TRUE/FALSE.",
    "num_":    "Numeric count metric.",
    "total_":  "Aggregate total. Range: {min_v} to {max_v}.",
    "avg_":    "Average value. Range: {min_v} to {max_v}.",
    "max_":    "Maximum value.",
    "min_":    "Minimum value.",
}


def _rule_column(col: dict) -> str:
    name  = col["name"].lower()
    dtype = (col.get("data_type") or "").upper()
    dist  = col.get("distinct_count", 0)
    null_rate = col.get("null_rate", 0.0)
    min_v = col.get("min_val", "?")
    max_v = col.get("max_val", "?")
    min_d = col.get("min_date", "?")
    max_d = col.get("max_date", "?")
    mean  = col.get("mean")

    ctx = dict(tbl=name.replace("_key","").replace("_sk",""),
               dist=dist, min_v=min_v, max_v=max_v, min_d=min_d, max_d=max_d)

    for suffix, tmpl in _ENDS.items():
        if name.endswith(suffix):
            comment = tmpl.format(**ctx)
            if null_rate > 0.01:
                comment += f" ~{null_rate*100:.0f}% null."
            return comment

    for prefix, tmpl in _STARTS.items():
        if name.startswith(prefix):
            return tmpl.format(**ctx)

    # Type-based fallback
    if any(t in dtype for t in ("INT","BIGINT","SMALLINT","TINYINT","BYTEINT","NUMBER","DECIMAL","FLOAT","REAL","DOUBLE")):
        if mean is not None:
            return f"Numeric column. Range {min_v}–{max_v}, mean {mean:.4g}."
        return f"Numeric column. {dist} distinct values."

    if any(t in dtype for t in ("DATE","TIMESTAMP","TIME","DATETIME")):
        return f"Temporal column. Range: {min_d} to {max_d}." if min_d != "?" else "Temporal column."

    if any(t in dtype for t in ("VARCHAR","CHAR","STRING","TEXT","NVARCHAR")):
        avg_len = col.get("avg_length")
        base = f"Text column. {dist} distinct values."
        if avg_len:
            base += f" Average length {avg_len:.0f} chars."
        return base

    if "BOOL" in dtype:
        return "Boolean flag. TRUE/FALSE."

    return f"{dist} distinct values." + (f" ~{null_rate*100:.0f}% null." if null_rate > 0.01 else "")


def _rule_table(profile: dict) -> str:
    name  = profile.get("table", "TABLE")
    rows  = profile.get("row_count", 0)
    ncols = profile.get("column_count", 0)
    plat  = profile.get("platform", "")
    src   = profile.get("source_name", "")

    base = f"{name} — {rows:,} rows, {ncols} columns."
    if src:
        base += f" Source: {src}."
    if plat:
        base += f" Platform: {plat}."
    return base


# ── LLM prompt builder ────────────────────────────────────────────────────────

def _llm_prompt(profile: dict) -> str:
    db    = profile.get("database", "")
    schema = profile.get("schema", "")
    table  = profile.get("table",  "")
    rows   = profile.get("row_count", 0)
    cols   = profile.get("columns", [])[:25]   # cap at 25 to avoid token overflow

    lines = []
    for c in cols:
        stats = f"type={c.get('data_type','?')}, nulls={c.get('null_rate',0)*100:.1f}%, distinct={c.get('distinct_count',0)}"
        if c.get("mean") is not None:
            stats += f", range=[{c.get('min_val')},{c.get('max_val')}]"
        elif c.get("min_date"):
            stats += f", range=[{c.get('min_date')},{c.get('max_date')}]"
        elif c.get("avg_length"):
            stats += f", avg_len={c.get('avg_length',0):.0f}"
        lines.append(f"  {c['name']}: {stats}")

    return f"""\
You are a senior data engineer writing documentation for a data warehouse table.

Table: {db}.{schema}.{table}  ({rows:,} rows)
Columns:
{chr(10).join(lines)}

Generate concise, business-friendly COMMENT metadata.
Return ONLY valid JSON — no extra text, no markdown fences:
{{
  "table_comment": "<2-sentence table description mentioning purpose, row count and key FKs>",
  "column_comments": {{
    "COLUMN_NAME": "<under 150 chars: business meaning, value range or distinct count, any data-quality note>",
    ...include ALL columns listed above...
  }}
}}

Rules:
- Columns ending in _KEY or _SK: state which dimension they reference.
- Monetary columns: mention currency (USD) and typical range.
- Boolean columns: state TRUE/FALSE meaning.
- Null column (null_rate > 0): mention approximate null rate.
- Be concise — max 150 characters per column comment.
"""


def _parse_llm(text: str) -> dict | None:
    for attempt in [
        text,
        re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text),
        re.search(r"\{[\s\S]*\}", text),
    ]:
        blob = attempt.group(1) if hasattr(attempt, "group") else (attempt or "")
        if blob:
            try:
                d = json.loads(blob)
                if isinstance(d, dict) and ("table_comment" in d or "column_comments" in d):
                    return d
            except Exception:
                continue
    return None


# ── Main engine ───────────────────────────────────────────────────────────────

class CommentGenerator:

    def __init__(self, provider: "BaseLLMProvider"):
        self.provider = provider

    def generate(self, profile: dict) -> dict:
        from datetime import datetime

        use_llm = (self.provider.is_available()
                   and self.provider.provider_id != "disabled")

        table_comment   = ""
        column_comments: dict[str, str] = {}
        errors: list[str] = []

        if use_llm:
            try:
                raw = self.provider.generate(
                    _llm_prompt(profile), temperature=0.2, max_tokens=1500,
                )
                parsed = _parse_llm(raw)
                if parsed:
                    table_comment   = parsed.get("table_comment", "")
                    column_comments = parsed.get("column_comments", {})
                else:
                    errors.append("LLM returned non-JSON response — using rule-based fallback.")
                    use_llm = False
            except Exception as exc:
                errors.append(f"LLM error: {exc}")
                use_llm = False

        # Fill in any missing columns (or all, if LLM failed)
        for col in profile.get("columns", []):
            cname = col["name"]
            if cname not in column_comments or not column_comments[cname]:
                column_comments[cname] = _rule_column(col)

        if not table_comment:
            table_comment = _rule_table(profile)

        return {
            "table_comment":    table_comment,
            "column_comments":  column_comments,
            "rule_based":       not use_llm,
            "provider":         self.provider.provider_id,
            "model":            self.provider.model_name,
            "errors":           errors,
            "generated_at":     datetime.now().isoformat(),
        }
