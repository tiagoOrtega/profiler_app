"""
Feature relevance ranking for clustering feature selection.

Given a target column, ranks every other feature — including engineered
features produced by FeatureEngineer (log1p transforms, ratio pairs) —
by how much information it shares with the target.

Scoring
-------
1. Mutual Information (sklearn)
   mutual_info_regression  → numeric target
   mutual_info_classif     → categorical / low-cardinality target
   Captures non-linear relationships (65% weight).

2. Pearson Correlation (numeric targets only, 35% weight)
   Fast linear-relationship signal.

Combined score = 0.65 × norm(MI) + 0.35 × |corr|

The top-k features are flagged "recommended".
If no feature_ids are supplied the selector analyses all suggestions
returned by FeatureEngineer (originals + log1p + ratios).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_categorical(data_type: str, values: np.ndarray, profile_col: dict) -> bool:
    dtype = data_type.upper()
    if any(t in dtype for t in ("CHAR", "TEXT", "VARCHAR", "STRING", "BOOL")):
        return True
    n_dist = profile_col.get("distinct_count", 0)
    if n_dist and n_dist <= 20:
        return True
    return False


def _encode_categorical(values: np.ndarray) -> np.ndarray:
    unique = {v: i for i, v in enumerate(sorted(set(str(v) for v in values if v is not None)))}
    return np.array([unique.get(str(v), -1) for v in values], dtype=np.int32)


def _feat_type(name: str) -> str:
    if name.startswith("log1p_"):  return "log1p"
    if name.startswith("ratio_"):  return "ratio"
    return "original"


# ── Engine ────────────────────────────────────────────────────────────────────

class FeatureSelector:

    TOP_K_RATIO  = 0.6
    MAX_FEATURES = 20
    SAMPLE_SIZE  = 8_000

    def __init__(self, conn, reports_dir: Path):
        self.conn = conn
        self.reports_dir = reports_dir

    # ── helpers ───────────────────────────────────────────────────────────────

    def _load_profile(self, db: str, schema: str, table: str) -> dict:
        key = f"{db.upper()}__{schema.upper()}__{table.upper()}"
        rp  = self.reports_dir / f"{key}.json"
        if not rp.exists():
            raise FileNotFoundError(
                f"No profile for {db}.{schema}.{table}. Profile the table first."
            )
        with open(rp) as f:
            return json.load(f)

    def _fetch_raw(
        self,
        db: str, schema: str, table: str,
        columns: list[str], limit: int,
    ) -> np.ndarray:
        tbl_ref  = self.conn.table_ref(db, schema, table)
        qcols    = ", ".join(self.conn.quote_col(c) for c in columns)
        sql      = f"SELECT {qcols} FROM {tbl_ref} LIMIT {limit}"
        rows     = self.conn.fetch_all(sql)
        if not rows:
            return np.zeros((0, len(columns)))
        return np.array(
            [[float(v) if v is not None else np.nan for v in row] for row in rows],
            dtype=object,
        )

    # ── main ─────────────────────────────────────────────────────────────────

    def analyze(
        self,
        db:          str,
        schema:      str,
        table:       str,
        target_col:  str,
        feature_ids: list[str] | None = None,
        sample_size: int = SAMPLE_SIZE,
    ) -> dict:
        """
        Rank all features by relevance to *target_col*.

        *feature_ids* — list of feature IDs from FeatureEngineer.suggest()
        (may include originals, log1p_*, ratio_* variants).
        When None, all suggestions are analysed (original + engineered).
        """
        from sklearn.feature_selection import (
            mutual_info_regression,
            mutual_info_classif,
        )
        from sklearn.impute import SimpleImputer
        from clustering import FeatureEngineer

        profile  = self._load_profile(db, schema, table)
        col_meta = {c["name"]: c for c in profile.get("columns", [])}

        # Base numeric columns (excluding target)
        base_cols = [
            c["name"] for c in profile["columns"]
            if c.get("mean") is not None and c["name"] != target_col
        ][:self.MAX_FEATURES]

        if not base_cols:
            raise ValueError("No numeric feature columns found (excluding target).")

        # Fetch raw base data + target together
        all_fetch = [target_col] + base_cols
        raw       = self._fetch_raw(db, schema, table, all_fetch, sample_size)
        if len(raw) < 20:
            raise ValueError(f"Too few rows ({len(raw)}) for feature importance analysis.")

        y_raw  = raw[:, 0]
        X_base = raw[:, 1:].astype(float)

        # Apply FeatureEngineer to build the full engineered feature matrix
        fe           = FeatureEngineer(base_cols, X_base)
        suggestions  = fe.suggest()
        all_ids      = [s["id"] for s in suggestions]

        if feature_ids:
            # Only analyse the IDs the user has selected (or all valid ones)
            valid = set(all_ids)
            use_ids = [fid for fid in feature_ids if fid in valid] or all_ids
        else:
            # Default: use all auto-selected suggestions
            use_ids = [s["id"] for s in suggestions if s["selected"]]

        X_eng, feature_names = fe.apply(use_ids)

        # Target encoding
        target_meta = col_meta.get(target_col, {})
        target_dtype = target_meta.get("data_type", "")
        is_cat = _is_categorical(target_dtype, y_raw, target_meta)

        if is_cat:
            y = _encode_categorical(y_raw)
        else:
            y = y_raw.astype(float)
            nan_mask = np.isnan(y)
            if nan_mask.any():
                y[nan_mask] = float(np.nanmean(y))

        # Impute missing feature values
        imputer = SimpleImputer(strategy="mean")
        X       = imputer.fit_transform(X_eng)

        # Keep only rows where target is known
        good = (y >= 0) if is_cat else ~np.isnan(raw[:, 0].astype(float))
        X, y = X[good], y[good]
        if len(X) < 10:
            raise ValueError("Too few complete rows after null filtering.")

        # ── Mutual Information ─────────────────────────────────────────────────
        rs = 42
        mi_raw = (mutual_info_classif(X, y, random_state=rs)
                  if is_cat else
                  mutual_info_regression(X, y, random_state=rs))
        mi_max  = mi_raw.max() if mi_raw.max() > 0 else 1.0
        mi_norm = mi_raw / mi_max

        # ── Pearson Correlation (numeric targets only) ──────────────────────────
        if not is_cat:
            corr = np.array([
                abs(float(np.corrcoef(X[:, j], y)[0, 1]))
                if np.std(X[:, j]) > 0 and np.std(y) > 0 else 0.0
                for j in range(X.shape[1])
            ])
            np.nan_to_num(corr, nan=0.0, copy=False)
        else:
            corr = np.zeros(len(feature_names))

        # ── Combined score ─────────────────────────────────────────────────────
        combined = np.clip(0.65 * mi_norm + 0.35 * corr, 0.0, 1.0)
        order    = np.argsort(combined)[::-1]
        n_rec    = max(2, min(self.MAX_FEATURES, int(len(feature_names) * self.TOP_K_RATIO)))

        features = []
        for rank_idx, col_idx in enumerate(order):
            name  = feature_names[col_idx]
            score = float(combined[col_idx])
            features.append({
                "name":           name,
                "feat_type":      _feat_type(name),
                "mi_score":       round(float(mi_norm[col_idx]), 4),
                "corr_score":     round(float(corr[col_idx]),    4) if not is_cat else None,
                "combined_score": round(score, 4),
                "rank":           rank_idx + 1,
                "recommended":    rank_idx < n_rec,
            })

        return {
            "target":        target_col,
            "target_type":   "categorical" if is_cat else "numeric",
            "method":        "mutual_info_classif" if is_cat
                             else "mutual_info_regression + pearson",
            "sample_size":   int(len(X)),
            "n_features":    len(features),
            "n_recommended": n_rec,
            "features":      features,
        }
