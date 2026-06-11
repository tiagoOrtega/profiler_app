"""
DataLens — Time-Series Trend Analysis.

Pipeline
--------
1. Detect date/timestamp columns from a saved profile.
2. For each selected metric column, run a GROUP BY DATE_TRUNC query in the warehouse.
3. Compute trend direction (linear regression on period aggregates).
4. Detect spikes (values > threshold × rolling std from rolling mean).
5. Compute period-over-period % change for the most recent period.
6. Return JSON-serialisable result persisted to TREND_RESULTS.
"""

from __future__ import annotations

from typing import Any

import numpy as np

DATE_TYPES = {"date", "timestamp", "datetime", "timestamp_ntz", "timestamp_ltz", "timestamp_tz"}

PERIODS = {
    "day":   "DAY",
    "week":  "WEEK",
    "month": "MONTH",
    "quarter": "QUARTER",
    "year":  "YEAR",
}

TREND_UP   = "up"
TREND_DOWN = "down"
TREND_FLAT = "flat"

SLOPE_THRESHOLD = 1.0   # % change per period to call a trend "up" or "down"
SPIKE_THRESHOLD = 2.0   # standard deviations from mean to flag a spike


# ── Profile helpers ────────────────────────────────────────────────────────────

def detect_date_columns(profile: dict) -> list[str]:
    """Return column names whose data_type looks like a date or timestamp."""
    return [
        c["name"] for c in profile.get("columns", [])
        if any(dt in c.get("data_type", "").lower() for dt in DATE_TYPES)
    ]


def detect_metric_columns(profile: dict) -> list[str]:
    """Return numeric column names (those with a mean) — up to 20."""
    return [c["name"] for c in profile.get("columns", [])
            if c.get("mean") is not None][:20]


# ── Engine ─────────────────────────────────────────────────────────────────────

class TrendAnalyzer:
    """Run time-series trend analysis on a profiled table."""

    def __init__(self, conn, profile: dict):
        self.conn    = conn
        self.profile = profile

    def run(
        self,
        db:          str,
        schema:      str,
        table:       str,
        date_col:    str,
        metric_cols: list[str],
        period:      str = "month",
    ) -> dict:
        """
        Fetch period aggregates and compute trends for each metric column.

        Parameters
        ----------
        date_col     Column to truncate by period (must be date/timestamp).
        metric_cols  Numeric columns to aggregate (AVG per period).
        period       'day' | 'week' | 'month' | 'quarter' | 'year'.
        """
        if period not in PERIODS:
            raise ValueError(f"Unknown period {period!r}. Choose from: {list(PERIODS)}")

        trunc_fn  = PERIODS[period]
        tbl_ref   = self.conn.table_ref(db, schema, table)
        date_q    = self.conn.quote_col(date_col)
        metrics: dict[str, Any] = {}

        for col in metric_cols:
            col_q = self.conn.quote_col(col)
            sql   = (
                f"SELECT DATE_TRUNC('{trunc_fn}', {date_q})::DATE AS period_start, "
                f"AVG({col_q}) AS avg_val, COUNT(*) AS row_count "
                f"FROM {tbl_ref} "
                f"WHERE {date_q} IS NOT NULL AND {col_q} IS NOT NULL "
                f"GROUP BY 1 ORDER BY 1"
            )
            try:
                rows = self.conn.fetch_all(sql)
            except Exception as exc:
                metrics[col] = {"error": str(exc)}
                continue

            if not rows or len(rows) < 2:
                metrics[col] = {"error": "Insufficient data points (< 2 periods)."}
                continue

            periods_str = [str(r[0]) for r in rows]
            values      = [float(r[1]) if r[1] is not None else float("nan") for r in rows]
            row_counts  = [int(r[2]) for r in rows]

            trend      = _compute_trend(values)
            spikes     = _detect_spikes(values)
            mom_change = _period_over_period(values)

            metrics[col] = {
                "periods":      periods_str,
                "values":       [round(v, 4) for v in values],
                "row_counts":   row_counts,
                "trend":        trend,
                "spikes":       spikes,
                "mom_change_pct": mom_change,
                "min":          round(float(np.nanmin(values)), 4),
                "max":          round(float(np.nanmax(values)), 4),
                "mean":         round(float(np.nanmean(values)), 4),
            }

        return {
            "date_col":    date_col,
            "period":      period,
            "table":       table,
            "schema":      schema,
            "database":    db,
            "n_periods":   len(set(
                r for m in metrics.values()
                if isinstance(m, dict) and "periods" in m
                for r in m["periods"]
            )),
            "metrics":     metrics,
        }


# ── Math helpers ───────────────────────────────────────────────────────────────

def _compute_trend(values: list[float]) -> dict:
    y = np.array(values, dtype=np.float64)
    valid = y[~np.isnan(y)]
    if len(valid) < 2:
        return {"direction": TREND_FLAT, "slope_pct": 0.0, "r2": 0.0}

    x      = np.arange(len(valid), dtype=np.float64)
    coeffs = np.polyfit(x, valid, 1)
    slope  = float(coeffs[0])

    # % change per period relative to the series mean
    baseline  = abs(float(np.nanmean(valid))) or 1.0
    slope_pct = round(slope / baseline * 100, 2)

    # R² from residuals
    y_hat  = np.polyval(coeffs, x)
    ss_res = float(np.sum((valid - y_hat) ** 2))
    ss_tot = float(np.sum((valid - valid.mean()) ** 2))
    r2     = round(1 - ss_res / ss_tot, 3) if ss_tot > 0 else 0.0

    direction = (TREND_UP   if slope_pct >  SLOPE_THRESHOLD else
                 TREND_DOWN if slope_pct < -SLOPE_THRESHOLD else
                 TREND_FLAT)

    return {"direction": direction, "slope_pct": slope_pct, "r2": r2}


def _detect_spikes(values: list[float], threshold: float = SPIKE_THRESHOLD) -> list[int]:
    y = np.array(values, dtype=np.float64)
    if np.isnan(y).all():
        return []
    mean = float(np.nanmean(y))
    std  = float(np.nanstd(y))
    if std == 0:
        return []
    return [i for i, v in enumerate(values)
            if not np.isnan(v) and abs(v - mean) > threshold * std]


def _period_over_period(values: list[float]) -> float | None:
    clean = [(i, v) for i, v in enumerate(values) if not np.isnan(v)]
    if len(clean) < 2:
        return None
    _, v_curr = clean[-1]
    _, v_prev = clean[-2]
    if v_prev == 0:
        return None
    return round((v_curr - v_prev) / abs(v_prev) * 100, 2)
