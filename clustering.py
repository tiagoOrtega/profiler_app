"""
ML clustering engine with automatic feature engineering.

Pipeline
--------
1. Load profile JSON → discover numeric columns.
2. Fetch sample rows from the platform (LIMIT N).
3. FeatureEngineer:
   a. Detect and apply log-transform to right-skewed columns.
   b. Compute ratio features between naturally paired columns.
   c. Return an annotated feature list with transform descriptions.
4. Impute NaN (column mean), StandardScale.
5. Fit chosen model (K-Means, DBSCAN, Agglomerative, GMM, Bisecting K-Means).
6. Compute quality metrics (Silhouette, Davies-Bouldin, Calinski-Harabász).
7. PCA 2-D projection for scatter-plot visualisation.
8. Cluster centroid statistics in the original (pre-transform) feature space.

All results are JSON-serialisable and persisted to
  reports/clustering/<DB>__<SCHEMA>__<TABLE>.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

# ── Model registry ─────────────────────────────────────────────────────────────

MODELS: dict[str, dict] = {
    "kmeans": {
        "label": "K-Means",
        "icon":  "bi-bullseye",
        "desc":  "Partitions data into k spherical clusters. Fast and well-understood. Best when clusters are roughly equal-sized and convex.",
        "params": [
            {"id": "n_clusters",   "label": "Clusters (k)",     "type": "int",   "default": 3,   "min": 2, "max": 50,   "hint": "Number of clusters to form"},
            {"id": "n_init",       "label": "Initializations",  "type": "int",   "default": 10,  "min": 1, "max": 50,   "hint": "Runs with different seeds — picks best inertia"},
            {"id": "max_iter",     "label": "Max iterations",   "type": "int",   "default": 300, "min": 10,"max": 1000, "hint": "Per initialisation"},
            {"id": "random_state", "label": "Random seed",      "type": "int",   "default": 42,  "min": 0, "max": 9999, "hint": "For reproducibility"},
        ],
    },
    "bisecting_kmeans": {
        "label": "Bisecting K-Means",
        "icon":  "bi-diagram-2",
        "desc":  "Recursively bisects the largest cluster. Often more balanced than standard K-Means.",
        "params": [
            {"id": "n_clusters",   "label": "Clusters (k)",     "type": "int",   "default": 3,  "min": 2, "max": 50,   "hint": "Total number of leaf clusters"},
            {"id": "n_init",       "label": "Initializations",  "type": "int",   "default": 3,  "min": 1, "max": 20,   "hint": "Runs per bisection step"},
            {"id": "random_state", "label": "Random seed",      "type": "int",   "default": 42, "min": 0, "max": 9999, "hint": "For reproducibility"},
        ],
    },
    "dbscan": {
        "label": "DBSCAN",
        "icon":  "bi-cloud",
        "desc":  "Density-based clustering. Discovers arbitrary-shaped clusters and flags outliers as noise. No need to specify k.",
        "params": [
            {"id": "eps",         "label": "ε (neighbourhood)", "type": "float", "default": 0.5, "min": 0.01, "max": 100, "step": 0.1, "hint": "Max distance between two points in the same neighbourhood (post-scaling)"},
            {"id": "min_samples", "label": "Min samples",       "type": "int",   "default": 5,   "min": 1,    "max": 200,              "hint": "Minimum points to form a dense region"},
        ],
    },
    "agglomerative": {
        "label": "Agglomerative",
        "icon":  "bi-diagram-3",
        "desc":  "Bottom-up hierarchical clustering. Good for discovering nested structure.",
        "params": [
            {"id": "n_clusters", "label": "Clusters (k)",      "type": "int",    "default": 3,      "min": 2, "max": 50, "hint": "Number of clusters in the flat partition"},
            {"id": "linkage",    "label": "Linkage criterion",  "type": "select", "default": "ward",
             "options": ["ward", "complete", "average", "single"],
             "hint": "Ward minimises intra-cluster variance; complete uses max distances"},
        ],
    },
    "gmm": {
        "label": "Gaussian Mixture",
        "icon":  "bi-distribute-vertical",
        "desc":  "Probabilistic model — fits k Gaussians. Handles elliptical clusters well; produces soft memberships.",
        "params": [
            {"id": "n_components",    "label": "Components",      "type": "int",    "default": 3,     "min": 2, "max": 50,   "hint": "Number of Gaussian components (≈ clusters)"},
            {"id": "covariance_type", "label": "Covariance type", "type": "select", "default": "full",
             "options": ["full", "tied", "diag", "spherical"],
             "hint": "'full' most flexible; 'spherical' similar to K-Means"},
            {"id": "random_state",    "label": "Random seed",     "type": "int",    "default": 42,    "min": 0, "max": 9999, "hint": "For reproducibility"},
        ],
    },
}

_PALETTE = [
    "#58a6ff", "#f85149", "#3fb950", "#d29922", "#bc8cff",
    "#ff7b72", "#79c0ff", "#56d364", "#e3b341", "#d2a679",
    "#54aeff", "#fb8500", "#90e0ef", "#b5e48c", "#e9c46a",
]
_NOISE_COLOR = "#6e7681"


# ── Feature engineering ─────────────────────────────────────────────────────────

class FeatureEngineer:
    """
    Analyses a raw numeric data matrix and suggests / applies feature transforms.

    Transforms applied
    ------------------
    log1p     Applied to positively-skewed columns (skewness > 1.0).
              log1p(x) = log(x + 1) — safe for x ≥ 0.

    ratio     Column-pair ratios suggested when columns share a common keyword
              prefix (e.g. UNIT_PRICE / UNIT_COST → MARGIN_RATIO).
    """

    SKEW_THRESHOLD = 1.0    # above this → log1p transform
    MAX_RATIO_PAIRS = 6     # limit generated ratio features

    def __init__(self, col_names: list[str], X: np.ndarray):
        self.col_names = col_names
        self.X         = X                     # shape (n, len(col_names))

    def suggest(self) -> list[dict]:
        """
        Return a list of feature-suggestion dicts, each with:
          id          unique feature name
          type        'original' | 'log1p' | 'ratio'
          description human-readable explanation
          source_cols list of original column names involved
          selected    bool — True = include by default
        """
        suggestions = []

        # ── Original columns ──────────────────────────────────────────────────
        for i, col in enumerate(self.col_names):
            col_data = self.X[:, i]
            col_data = col_data[~np.isnan(col_data)]
            skew = float(self._skewness(col_data))

            suggestions.append({
                "id":          col,
                "type":        "original",
                "description": f"Original column (skewness {skew:.2f})",
                "source_cols": [col],
                "selected":    True,
                "skewness":    round(skew, 3),
            })

            # Suggest log transform for skewed columns
            if skew > self.SKEW_THRESHOLD and (col_data >= 0).all():
                suggestions.append({
                    "id":          f"log_{col}",
                    "type":        "log1p",
                    "description": f"log(1 + {col})  — compresses right-skew ({skew:.2f} → ~0)",
                    "source_cols": [col],
                    "selected":    skew > 2.0,   # auto-select if very skewed
                    "skewness":    round(skew, 3),
                })

            # Quartile bin: always suggest — useful for skewed and multimodal columns
            suggestions.append({
                "id":          f"{col}_Q",
                "type":        "quartile_bin",
                "description": f"Quartile rank of {col}  (1=bottom 25% … 4=top 25%)",
                "source_cols": [col],
                "selected":    False,
                "skewness":    round(skew, 3),
            })

            # Outlier flag: suggest when column has measurable outlier mass
            col_std = float(col_data.std()) if len(col_data) > 1 else 0.0
            if col_std > 0:
                z_scores    = np.abs((col_data - col_data.mean()) / col_std)
                outlier_pct = float(np.mean(z_scores > 2))
                if outlier_pct > 0.01:
                    suggestions.append({
                        "id":          f"{col}_out",
                        "type":        "outlier_flag",
                        "description": (
                            f"1 if {col} is an outlier (|z|>2)  "
                            f"— {outlier_pct:.1%} of values flagged"
                        ),
                        "source_cols": [col],
                        "selected":    False,
                        "skewness":    round(skew, 3),
                    })

        # ── Ratio features ───────────────────────────────────────────────────
        ratio_count = 0
        for i, a in enumerate(self.col_names):
            if ratio_count >= self.MAX_RATIO_PAIRS:
                break
            for j, b in enumerate(self.col_names):
                if i >= j or ratio_count >= self.MAX_RATIO_PAIRS:
                    break
                if not self._are_ratio_candidates(a, b):
                    continue
                feat_id = f"{a}_div_{b}"
                suggestions.append({
                    "id":          feat_id,
                    "type":        "ratio",
                    "description": f"{a} ÷ {b}  — relative proportion / margin",
                    "source_cols": [a, b],
                    "selected":    False,
                    "skewness":    None,
                })
                ratio_count += 1

        return suggestions

    def apply(self, selected_ids: list[str]) -> tuple[np.ndarray, list[str]]:
        """
        Build a feature matrix from *selected_ids*.
        Returns (X_engineered, feature_names).
        """
        col_idx   = {c: i for i, c in enumerate(self.col_names)}
        out_cols: list[np.ndarray] = []
        out_names: list[str] = []

        suggestions = {s["id"]: s for s in self.suggest()}

        for fid in selected_ids:
            if fid not in suggestions:
                continue
            s = suggestions[fid]

            if s["type"] == "original":
                ci = col_idx.get(s["source_cols"][0])
                if ci is not None:
                    out_cols.append(self.X[:, ci])
                    out_names.append(fid)

            elif s["type"] == "log1p":
                ci = col_idx.get(s["source_cols"][0])
                if ci is not None:
                    col = self.X[:, ci].copy()
                    col = np.where(col < 0, 0, col)   # clip negative to 0
                    out_cols.append(np.log1p(col))
                    out_names.append(fid)

            elif s["type"] == "ratio":
                ai = col_idx.get(s["source_cols"][0])
                bi = col_idx.get(s["source_cols"][1])
                if ai is not None and bi is not None:
                    denom = self.X[:, bi].copy()
                    denom[np.abs(denom) < 1e-9] = np.nan
                    ratio = self.X[:, ai] / denom
                    out_cols.append(ratio)
                    out_names.append(fid)

            elif s["type"] == "quartile_bin":
                ci = col_idx.get(s["source_cols"][0])
                if ci is not None:
                    col  = self.X[:, ci].copy()
                    q25, q50, q75 = np.nanquantile(col, [0.25, 0.50, 0.75])
                    bins = np.ones(len(col), dtype=np.float64)
                    bins[col > q25] = 2.0
                    bins[col > q50] = 3.0
                    bins[col > q75] = 4.0
                    bins[np.isnan(col)] = np.nan
                    out_cols.append(bins)
                    out_names.append(fid)

            elif s["type"] == "outlier_flag":
                ci = col_idx.get(s["source_cols"][0])
                if ci is not None:
                    col  = self.X[:, ci].copy()
                    mean = np.nanmean(col)
                    std  = np.nanstd(col)
                    if std > 0:
                        flag = (np.abs((col - mean) / std) > 2).astype(np.float64)
                    else:
                        flag = np.zeros(len(col), dtype=np.float64)
                    flag[np.isnan(col)] = np.nan
                    out_cols.append(flag)
                    out_names.append(fid)

        if not out_cols:
            raise ValueError("No valid features selected after engineering.")

        X_eng = np.column_stack(out_cols)
        return X_eng, out_names

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _skewness(x: np.ndarray) -> float:
        if len(x) < 3:
            return 0.0
        try:
            from scipy.stats import skew
            return float(skew(x, bias=False))
        except ImportError:
            # Pure numpy fallback
            m = x.mean()
            s = x.std()
            if s == 0:
                return 0.0
            return float(np.mean(((x - m) / s) ** 3))

    @staticmethod
    def _are_ratio_candidates(a: str, b: str) -> bool:
        """Heuristic: suggest ratios for columns that share a keyword stem."""
        _PAIRS = [
            ("price", "cost"), ("amount", "cost"), ("net", "gross"),
            ("discount", "gross"), ("margin", "price"), ("revenue", "cost"),
            ("sale", "purchase"), ("income", "expense"),
        ]
        al, bl = a.lower(), b.lower()
        return any(
            (p in al and q in bl) or (q in al and p in bl)
            for p, q in _PAIRS
        )


# ── Model builder ──────────────────────────────────────────────────────────────

def _build_model(model_name: str, params: dict) -> Any:
    try:
        import sklearn  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "scikit-learn is required for clustering. Run: pip install scikit-learn"
        ) from exc

    p = params
    if model_name == "kmeans":
        from sklearn.cluster import KMeans
        return KMeans(
            n_clusters=int(p.get("n_clusters", 3)),
            n_init=int(p.get("n_init", 10)),
            max_iter=int(p.get("max_iter", 300)),
            random_state=int(p.get("random_state", 42)),
        )
    if model_name == "bisecting_kmeans":
        from sklearn.cluster import BisectingKMeans
        return BisectingKMeans(
            n_clusters=int(p.get("n_clusters", 3)),
            n_init=int(p.get("n_init", 3)),
            random_state=int(p.get("random_state", 42)),
        )
    if model_name == "dbscan":
        from sklearn.cluster import DBSCAN
        return DBSCAN(eps=float(p.get("eps", 0.5)), min_samples=int(p.get("min_samples", 5)))
    if model_name == "agglomerative":
        from sklearn.cluster import AgglomerativeClustering
        return AgglomerativeClustering(
            n_clusters=int(p.get("n_clusters", 3)),
            linkage=str(p.get("linkage", "ward")),
        )
    if model_name == "gmm":
        from sklearn.mixture import GaussianMixture
        return GaussianMixture(
            n_components=int(p.get("n_components", 3)),
            covariance_type=str(p.get("covariance_type", "full")),
            random_state=int(p.get("random_state", 42)),
        )
    raise ValueError(f"Unknown model: {model_name!r}. Choose from: {list(MODELS)}")


# ── Engine ─────────────────────────────────────────────────────────────────────

class ClusteringEngine:
    """Run clustering with optional feature engineering on a profiled table."""

    def __init__(self, conn, reports_dir: Path):
        self.conn = conn
        self.reports_dir = reports_dir

    # ── Data helpers ─────────────────────────────────────────────────────────

    def _load_profile(self, db: str, schema: str, table: str) -> dict:
        key = f"{db.upper()}__{schema.upper()}__{table.upper()}"
        rp  = self.reports_dir / f"{key}.json"
        if not rp.exists():
            raise FileNotFoundError(
                f"No saved profile for {db}.{schema}.{table}. "
                "Profile the table first (Run Profile tab)."
            )
        with open(rp) as f:
            return json.load(f)

    def auto_select_columns(self, db: str, schema: str, table: str) -> list[str]:
        """Return all numeric column names from the saved profile (up to 20)."""
        profile = self._load_profile(db, schema, table)
        return [c["name"] for c in profile["columns"] if c.get("mean") is not None][:20]

    def suggest_features(
        self, db: str, schema: str, table: str,
        base_columns: list[str] | None = None,
        sample_size: int = 5000,
    ) -> list[dict]:
        """
        Fetch a small sample and return feature suggestions with transform metadata.
        Called by /api/clustering/features before the user runs a model.
        """
        if not base_columns:
            base_columns = self.auto_select_columns(db, schema, table)
        X = self._fetch_sample(db, schema, table, base_columns, sample_size)
        if len(X) == 0:
            return [{"id": c, "type": "original", "source_cols": [c],
                     "description": c, "selected": True, "skewness": None}
                    for c in base_columns]
        fe = FeatureEngineer(base_columns, X)
        return fe.suggest()

    def _fetch_sample(
        self, db: str, schema: str, table: str,
        columns: list[str], limit: "int | None",
        cat_columns: "list[str] | None" = None,
    ):
        """
        Fetch numeric columns as a float array.
        If cat_columns is given, also fetches those string columns in the same
        query and returns (X_num, cat_data) where cat_data is {col: [values]}.
        Without cat_columns, returns X_num only (backwards-compatible).
        """
        tbl_ref  = self.conn.table_ref(db, schema, table)
        qcols    = [self.conn.quote_col(c) for c in columns]
        where    = " AND ".join(f"{qc} IS NOT NULL" for qc in qcols)
        limit_clause = f" LIMIT {limit}" if limit else ""

        if cat_columns:
            cat_qcols = [self.conn.quote_col(c) for c in cat_columns]
            cols_sql  = ", ".join(qcols + cat_qcols)
        else:
            cols_sql  = ", ".join(qcols)

        sql  = f"SELECT {cols_sql} FROM {tbl_ref} WHERE {where}{limit_clause}"
        rows = self.conn.fetch_all(sql)

        n_num = len(columns)
        if not rows:
            X_num = np.zeros((0, n_num), dtype=np.float64)
            return (X_num, {}) if cat_columns else X_num

        X_num = np.array(
            [[float(row[j]) if row[j] is not None else np.nan for j in range(n_num)]
             for row in rows],
            dtype=np.float64,
        )

        if not cat_columns:
            return X_num

        cat_data = {
            col: [row[n_num + i] for row in rows]
            for i, col in enumerate(cat_columns)
        }
        return X_num, cat_data

    def _encode_cat_features(
        self, cat_feature_ids: list, cat_data: dict
    ) -> "tuple[list, list]":
        """Encode categorical feature IDs (_freq / _ord / _01) into float arrays."""
        from collections import Counter
        out_cols: list = []
        out_names: list = []
        for fid in cat_feature_ids:
            for sfx, kind in [("_freq", "freq"), ("_ord", "ord"), ("_01", "bin")]:
                if not fid.endswith(sfx):
                    continue
                base = fid[: -len(sfx)]
                vals = cat_data.get(base)
                if vals is None:
                    break
                n = len(vals)
                if kind == "freq":
                    counts = Counter(v for v in vals if v is not None)
                    arr = np.array(
                        [counts.get(v, 0) / n if v is not None else np.nan for v in vals],
                        dtype=np.float64,
                    )
                elif kind == "ord":
                    unique  = sorted(set(v for v in vals if v is not None))
                    ord_map = {v: float(i) for i, v in enumerate(unique)}
                    arr = np.array(
                        [ord_map.get(v, np.nan) if v is not None else np.nan for v in vals],
                        dtype=np.float64,
                    )
                else:  # bin
                    unique = sorted(set(v for v in vals if v is not None))
                    if len(unique) != 2:
                        break
                    arr = np.array(
                        [0.0 if v == unique[0] else (1.0 if v == unique[1] else np.nan)
                         for v in vals],
                        dtype=np.float64,
                    )
                out_cols.append(arr)
                out_names.append(fid)
                break
        return out_cols, out_names

    # ── Main ─────────────────────────────────────────────────────────────────

    def run(
        self,
        db:               str,
        schema:           str,
        table:            str,
        model_name:       str              = "kmeans",
        params:           dict | None      = None,
        columns:          list[str] | None = None,
        feature_ids:      list[str] | None = None,
        cat_feature_ids:  list[str] | None = None,  # "{col}_freq" / "_ord" / "_01"
        sample_size:      int              = 10_000,
        scaler_type:      str              = "standard",
    ) -> dict:
        """
        Full pipeline: fetch → engineer features → scale → cluster → metrics → PCA.

        Parameters
        ----------
        columns          Base numeric columns to fetch (auto-selected if None).
        feature_ids      Selected feature IDs after engineering (all originals if None).
        cat_feature_ids  Encoded categorical feature IDs (_freq / _ord / _01 suffixes).
        """
        from sklearn.preprocessing import StandardScaler
        from sklearn.decomposition import PCA
        from sklearn.impute import SimpleImputer

        if params is None:
            params = {}

        # 1 ─ base columns
        if not columns:
            columns = self.auto_select_columns(db, schema, table)
        if len(columns) < 2:
            raise ValueError(
                f"Need at least 2 numeric columns. Found {len(columns)}."
            )

        # Derive categorical base columns from cat_feature_ids
        cat_base_cols: list[str] = []
        if cat_feature_ids:
            _seen: set[str] = set()
            for _fid in cat_feature_ids:
                for _sfx in ("_freq", "_ord", "_01"):
                    if _fid.endswith(_sfx):
                        _base = _fid[: -len(_sfx)]
                        if _base not in _seen:
                            cat_base_cols.append(_base)
                            _seen.add(_base)
                        break

        # 2 ─ fetch raw sample
        if cat_base_cols:
            X_raw, _cat_data = self._fetch_sample(
                db, schema, table, columns, sample_size, cat_columns=cat_base_cols
            )
        else:
            X_raw     = self._fetch_sample(db, schema, table, columns, sample_size)
            _cat_data: dict = {}
        if len(X_raw) < 10:
            raise ValueError(
                f"Too few rows after null filtering ({len(X_raw)}). "
                "Lower sample_size or check column nullability."
            )

        # 3 ─ feature engineering
        fe           = FeatureEngineer(columns, X_raw)
        suggestions  = fe.suggest()

        if feature_ids:
            selected_ids = feature_ids
        else:
            # Default: all original columns (no transforms applied unless chosen)
            selected_ids = [s["id"] for s in suggestions if s["selected"] and s["type"] == "original"]

        X_eng, feature_names = fe.apply(selected_ids)
        feat_meta = {s["id"]: s for s in suggestions}

        # Append categorical encodings if requested
        if cat_feature_ids and _cat_data:
            _cat_arrs, _cat_names = self._encode_cat_features(cat_feature_ids, _cat_data)
            if _cat_arrs:
                X_eng         = np.column_stack([X_eng] + _cat_arrs)
                feature_names = feature_names + _cat_names

        # 4 ─ impute + scale
        imputer = SimpleImputer(strategy="mean")
        X_imp   = imputer.fit_transform(X_eng)

        if scaler_type == "minmax":
            from sklearn.preprocessing import MinMaxScaler
            X_sc = MinMaxScaler().fit_transform(X_imp)
        elif scaler_type == "robust":
            from sklearn.preprocessing import RobustScaler
            X_sc = RobustScaler().fit_transform(X_imp)
        elif scaler_type == "none":
            X_sc = X_imp.copy()
        else:
            X_sc = StandardScaler().fit_transform(X_imp)

        # 5 ─ fit model
        model  = _build_model(model_name, params)
        labels = (model.fit_predict(X_sc)
                  if hasattr(model, "fit_predict")
                  else (model.fit(X_sc) or model.predict(X_sc)))
        labels     = labels.tolist()
        uniq       = sorted(set(labels))
        real_idx   = [i for i, l in enumerate(labels) if l >= 0]
        real_lbl   = [labels[i] for i in real_idx]
        n_clusters = len(set(real_lbl))
        n_noise    = sum(1 for l in labels if l == -1)

        # 6 ─ quality metrics
        metrics: dict = {}
        if n_clusters >= 2 and len(real_idx) > n_clusters:
            from sklearn.metrics import (
                silhouette_score, davies_bouldin_score, calinski_harabasz_score,
            )
            X_real = X_sc[real_idx]
            try:
                metrics["silhouette_score"]        = round(float(silhouette_score(X_real, real_lbl)), 4)
                metrics["davies_bouldin_score"]    = round(float(davies_bouldin_score(X_real, real_lbl)), 4)
                metrics["calinski_harabasz_score"] = round(float(calinski_harabasz_score(X_real, real_lbl)), 2)
            except Exception:
                pass
        if n_noise:
            metrics["noise_points"] = n_noise
            metrics["noise_pct"]    = round(n_noise / len(labels) * 100, 2)

        # 7 ─ PCA 2-D
        n_comp = min(2, X_sc.shape[1])
        pca    = PCA(n_components=n_comp, random_state=42)
        X_2d   = pca.fit_transform(X_sc)
        exp_var = pca.explained_variance_ratio_.tolist()

        MAX_SCATTER     = 5_000
        pts_per_cluster = max(20, MAX_SCATTER // max(1, len(uniq)))
        cluster_pts: dict = {}
        for i, lbl in enumerate(labels):
            pts = cluster_pts.setdefault(lbl, [])
            if len(pts) < pts_per_cluster:
                pts.append({
                    "x": round(float(X_2d[i, 0]), 4),
                    "y": round(float(X_2d[i, 1]) if n_comp > 1 else 0.0, 4),
                })

        scatter_datasets = []
        for li, lbl in enumerate(sorted(cluster_pts)):
            is_noise = lbl == -1
            color    = _NOISE_COLOR if is_noise else _PALETTE[li % len(_PALETTE)]
            name     = "Noise" if is_noise else f"Cluster {lbl}"
            total    = sum(1 for l in labels if l == lbl)
            scatter_datasets.append({
                "cluster": int(lbl),
                "label":   f"{name} ({total:,})",
                "color":   color,
                "points":  cluster_pts[lbl],
            })

        # 8 ─ cluster stats in *imputed* (pre-scale) feature space
        cluster_stats = []
        for lbl in sorted(l for l in uniq if l >= 0):
            idx  = [i for i, l in enumerate(labels) if l == lbl]
            size = len(idx)
            centroid = {
                fname: round(float(X_imp[idx, j].mean()), 4)
                for j, fname in enumerate(feature_names)
            }
            cluster_stats.append({
                "cluster":  int(lbl),
                "size":     size,
                "pct":      round(size / len(labels) * 100, 2),
                "centroid": centroid,
            })

        # Compact stratified sample (pre-scale) for the Data tab
        rows_per_cluster = max(5, 500 // max(1, n_clusters))
        data_sample: list[dict] = []
        cluster_seen: dict[int, int] = {}
        for i, lbl in enumerate(labels):
            if lbl < 0:
                continue
            cnt = cluster_seen.get(lbl, 0)
            if cnt < rows_per_cluster:
                row = {"cluster": int(lbl)}
                for j, fn in enumerate(feature_names):
                    row[fn] = round(float(X_imp[i, j]), 4)
                data_sample.append(row)
                cluster_seen[lbl] = cnt + 1

        return {
            "model":            model_name,
            "scaler_type":      scaler_type,
            "data_sample":      data_sample,
            "model_label":      MODELS.get(model_name, {}).get("label", model_name),
            "params":           params,
            "columns_used":     feature_names,
            "base_columns":     columns,
            "feature_suggestions": suggestions,
            "applied_feature_ids": selected_ids,
            "sample_size":      len(X_raw),
            "n_clusters":       n_clusters,
            "metrics":          metrics,
            "cluster_stats":    cluster_stats,
            "scatter": {
                "datasets": scatter_datasets,
                "x_label":  f"PC1 ({exp_var[0]*100:.1f}% var)" if exp_var else "PC1",
                "y_label":  f"PC2 ({exp_var[1]*100:.1f}% var)" if len(exp_var) > 1 else "PC2",
            },
            "pca_explained_variance": exp_var,
        }
