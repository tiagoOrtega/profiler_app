"""
Feature relevance ranking for clustering feature selection.

Given a target column, scores every other numeric column by how much
information it shares with the target, using:

  1. Mutual Information (sklearn) — captures non-linear relationships.
     Uses mutual_info_regression for numeric targets,
     mutual_info_classif for categorical/low-cardinality targets.

  2. Pearson Correlation — linear relationship strength (numeric targets only).

  3. Combined score = 0.65 × norm(MI) + 0.35 × |corr|
     (MI carries more weight; correlation adds a linear bias signal).

The top-k features are flagged as "recommended" for clustering.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


# ── Target-type detection ─────────────────────────────────────────────────────

def _is_categorical(col_name: str, data_type: str, values: np.ndarray, profile_col: dict) -> bool:
    """Heuristic: treat as categorical if low cardinality or non-numeric type."""
    dtype_upper = data_type.upper()
    # Non-numeric types → always categorical
    if any(t in dtype_upper for t in ("CHAR","TEXT","VARCHAR","STRING","BOOL","BOOLEAN")):
        return True
    # Numeric but very few distinct values → treat as categorical
    n_distinct = profile_col.get("distinct_count", 0)
    if n_distinct and n_distinct <= 20:
        return True
    return False


# ── Label encoding for categorical targets ────────────────────────────────────

def _encode_categorical(values: np.ndarray) -> np.ndarray:
    """Integer-encode a string/mixed array."""
    unique = {v: i for i, v in enumerate(sorted(set(str(v) for v in values if v is not None)))}
    return np.array([unique.get(str(v), -1) for v in values], dtype=np.int32)


# ── Main engine ───────────────────────────────────────────────────────────────

class FeatureSelector:

    TOP_K_RATIO  = 0.6     # recommend the top 60% of features (min 2, max 15)
    MAX_FEATURES = 20      # cap analysed features
    SAMPLE_SIZE  = 8_000   # rows fetched for analysis

    def __init__(self, conn, reports_dir: Path):
        self.conn = conn
        self.reports_dir = reports_dir

    def _load_profile(self, db: str, schema: str, table: str) -> dict:
        key = f"{db.upper()}__{schema.upper()}__{table.upper()}"
        rp  = self.reports_dir / f"{key}.json"
        if not rp.exists():
            raise FileNotFoundError(
                f"No saved profile for {db}.{schema}.{table}. Profile the table first."
            )
        with open(rp) as f:
            return json.load(f)

    def _fetch_columns(
        self,
        db: str, schema: str, table: str,
        columns: list[str],
        limit: int,
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

    def analyze(
        self,
        db: str,
        schema: str,
        table: str,
        target_col: str,
        sample_size: int = SAMPLE_SIZE,
    ) -> dict:
        """
        Rank all numeric columns by relevance to *target_col*.

        Returns a dict with:
          target       — the target column name
          target_type  — "numeric" | "categorical"
          method       — description of the scoring method
          features     — list sorted by score desc, each:
            {name, mi_score, corr_score, combined_score, rank, recommended}
        """
        try:
            from sklearn.feature_selection import (
                mutual_info_regression,
                mutual_info_classif,
            )
            from sklearn.preprocessing import StandardScaler
            from sklearn.impute import SimpleImputer
        except ImportError as exc:
            raise ImportError(
                "scikit-learn is required. Run: pip install scikit-learn"
            ) from exc

        profile = self._load_profile(db, schema, table)
        col_meta = {c["name"]: c for c in profile.get("columns", [])}

        # All numeric columns (excluding the target itself)
        numeric_cols = [
            c["name"] for c in profile["columns"]
            if c.get("mean") is not None and c["name"] != target_col
        ][:self.MAX_FEATURES]

        if not numeric_cols:
            raise ValueError("No numeric feature columns found (excluding target).")

        # Determine target type from profile metadata
        target_meta  = col_meta.get(target_col, {})
        target_dtype = target_meta.get("data_type", "")

        # Fetch target + feature columns together
        all_cols = [target_col] + numeric_cols
        raw = self._fetch_columns(db, schema, table, all_cols, sample_size)

        if len(raw) < 20:
            raise ValueError(f"Too few rows ({len(raw)}) for feature importance analysis.")

        # Split target and features
        y_raw = raw[:, 0]
        X_raw = raw[:, 1:].astype(float)

        is_cat = _is_categorical(target_col, target_dtype, y_raw, target_meta)

        if is_cat:
            y = _encode_categorical(y_raw)
        else:
            y = y_raw.astype(float)
            y_nan = np.isnan(y)
            if y_nan.any():
                y[y_nan] = np.nanmean(y)

        # Impute X
        imputer = SimpleImputer(strategy="mean")
        X = imputer.fit_transform(X_raw)

        # Remove rows where target is unknown (encoded as -1 for categorical)
        if is_cat:
            mask = y >= 0
        else:
            mask = ~np.isnan(raw[:, 0].astype(float))
        X, y = X[mask], y[mask]

        if len(X) < 10:
            raise ValueError("Too few complete rows after null filtering.")

        # ── Mutual Information ─────────────────────────────────────────────────
        rs = 42
        if is_cat:
            mi_raw = mutual_info_classif(X, y, random_state=rs)
        else:
            mi_raw = mutual_info_regression(X, y, random_state=rs)

        mi_max  = mi_raw.max() if mi_raw.max() > 0 else 1.0
        mi_norm = mi_raw / mi_max

        # ── Pearson Correlation (numeric target only) ──────────────────────────
        if not is_cat:
            corr = np.array([
                abs(float(np.corrcoef(X[:, j], y)[0, 1]))
                if np.std(X[:, j]) > 0 and np.std(y) > 0 else 0.0
                for j in range(X.shape[1])
            ])
            np.nan_to_num(corr, nan=0.0, copy=False)
        else:
            corr = np.zeros(len(numeric_cols))

        # ── Combined score ─────────────────────────────────────────────────────
        combined = 0.65 * mi_norm + 0.35 * corr
        combined = np.clip(combined, 0.0, 1.0)

        # ── Build ranked result ────────────────────────────────────────────────
        order   = np.argsort(combined)[::-1]
        n_rec   = max(2, min(self.MAX_FEATURES, int(len(numeric_cols) * self.TOP_K_RATIO)))

        features = []
        for rank_idx, col_idx in enumerate(order):
            col_name = numeric_cols[col_idx]
            score    = float(combined[col_idx])
            features.append({
                "name":          col_name,
                "mi_score":      round(float(mi_norm[col_idx]), 4),
                "corr_score":    round(float(corr[col_idx]),    4) if not is_cat else None,
                "combined_score":round(score, 4),
                "rank":          rank_idx + 1,
                "recommended":   rank_idx < n_rec,
            })

        return {
            "target":      target_col,
            "target_type": "categorical" if is_cat else "numeric",
            "method":      "mutual_info_classif" if is_cat else "mutual_info_regression + pearson",
            "sample_size": int(len(X)),
            "n_features":  len(features),
            "n_recommended": n_rec,
            "features":    features,
        }
