"""
Auto-detect FK-like column relationships by testing value containment
across all previously-profiled tables.

Algorithm
---------
For every column C in the source table:

1. Scan all saved profile JSONs for other tables that also have a column
   named C (case-insensitive).

2. For each matching (target_table, target_column) pair, execute a single
   round-trip containment query using EXCEPT set-operation:

       orphans = DISTINCT(source.C) EXCEPT DISTINCT(target.C)

3. Derive:
       matched   = src_distinct − orphans
       match_pct = matched / src_distinct

4. Classify:
       PASS — 0 orphans   (perfect FK-like containment)
       WARN — match_pct ≥ 0.90  (mostly matched; minor data quality issue)
       FAIL — match_pct < 0.90  (not a FK relationship, or data quality failure)

Platform quoting
----------------
When conn is a BasePlatform the detector uses conn.table_ref() and
conn.quote_col() for correct identifier quoting per dialect.  For legacy or
mock connections (no table_ref attribute) it falls back to ANSI double-quotes.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

@dataclass
class RelationshipResult:
    source_column: str
    target_db: str
    target_schema: str
    target_table: str
    target_column: str
    src_distinct: int     # distinct non-null values in source column
    tgt_distinct: int     # distinct non-null values in target column
    matched: int          # source values that exist in target
    orphans: int          # source values NOT found in target
    match_pct: float      # matched / src_distinct  (0.0–1.0)
    status: str           # PASS | WARN | FAIL


class RelationshipDetector:

    def __init__(self, conn, reports_dir: Path):
        self.conn = conn
        self.reports_dir = reports_dir

    # ── helpers ───────────────────────────────────────────────────────────────

    def _load_profiles(self) -> list:
        profiles = []
        for fp in self.reports_dir.glob("*.json"):
            try:
                with open(fp) as f:
                    profiles.append(json.load(f))
            except Exception:
                pass
        return profiles

    def _candidates(self, col_name: str, exclude_key: str, profiles: list) -> list:
        """Return columns from other tables that share the same name."""
        out = []
        for p in profiles:
            tbl_key = f"{p['database']}__{p['schema']}__{p['table']}".upper()
            if tbl_key == exclude_key:
                continue
            for c in p["columns"]:
                if c["name"].upper() == col_name.upper():
                    out.append({
                        "database": p["database"],
                        "schema":   p["schema"],
                        "table":    p["table"],
                        "column":   c["name"],
                    })
        return out

    def _test(
        self,
        src_db, src_schema, src_table, src_col,
        tgt_db, tgt_schema, tgt_table, tgt_col,
    ) -> RelationshipResult:
        # Use platform methods when available, else fall back to ANSI double-quotes
        if hasattr(self.conn, "table_ref"):
            src_ref = self.conn.table_ref(src_db, src_schema, src_table)
            tgt_ref = self.conn.table_ref(tgt_db, tgt_schema, tgt_table)
        else:
            src_ref = f'"{src_db}"."{src_schema}"."{src_table}"'
            tgt_ref = f'"{tgt_db}"."{tgt_schema}"."{tgt_table}"'

        qc = getattr(self.conn, "quote_col", lambda n: f'"{n}"')
        qs = qc(src_col)
        qt = qc(tgt_col)

        # Single round-trip: src_distinct, tgt_distinct, orphan_count
        row = self.conn.fetch_one(f"""
            SELECT
                (SELECT COUNT(DISTINCT {qs}) FROM {src_ref} WHERE {qs} IS NOT NULL),
                (SELECT COUNT(DISTINCT {qt}) FROM {tgt_ref} WHERE {qt} IS NOT NULL),
                (SELECT COUNT(*) FROM (
                    SELECT DISTINCT {qs} AS v FROM {src_ref} WHERE {qs} IS NOT NULL
                    EXCEPT
                    SELECT DISTINCT {qt} AS v FROM {tgt_ref} WHERE {qt} IS NOT NULL
                ))
        """)

        src_d   = int(row[0]) if row and row[0] is not None else 0
        tgt_d   = int(row[1]) if row and row[1] is not None else 0
        orphans = int(row[2]) if row and row[2] is not None else src_d
        matched  = max(0, src_d - orphans)
        match_pct = matched / src_d if src_d > 0 else 0.0

        if orphans == 0:
            status = "PASS"
        elif match_pct >= 0.90:
            status = "WARN"
        else:
            status = "FAIL"

        return RelationshipResult(
            source_column=src_col,
            target_db=tgt_db, target_schema=tgt_schema,
            target_table=tgt_table, target_column=tgt_col,
            src_distinct=src_d, tgt_distinct=tgt_d,
            matched=matched, orphans=orphans,
            match_pct=match_pct, status=status,
        )

    # ── public API ────────────────────────────────────────────────────────────

    def detect(self, database: str, schema: str, table: str) -> List[RelationshipResult]:
        """
        Test all columns in database.schema.table against same-named columns
        in every other previously-profiled table.
        Returns one RelationshipResult per (source_column, target_table) pair tested.
        """
        profiles = self._load_profiles()
        source_key = f"{database}__{schema}__{table}".upper()

        src_profile = next(
            (p for p in profiles
             if f"{p['database']}__{p['schema']}__{p['table']}".upper() == source_key),
            None,
        )
        if not src_profile:
            return []

        results: List[RelationshipResult] = []
        for col in src_profile["columns"]:
            if col["distinct_count"] == 0:
                continue
            for cand in self._candidates(col["name"], source_key, profiles):
                results.append(self._test(
                    database, schema, table, col["name"],
                    cand["database"], cand["schema"], cand["table"], cand["column"],
                ))
        return results
