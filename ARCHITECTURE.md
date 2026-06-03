# Architecture

Technical design reference for contributors and platform integrators.

---

## Design Principles

1. **No data transfer** — all statistical computations run as SQL inside the
   warehouse. The profiler only receives scalar aggregates (counts, means,
   min/max), never rows of raw data.

2. **Platform abstraction** — `BasePlatform` is the single integration point.
   Adding a new database requires implementing one class; the profiling engine,
   alert system, relationship detector and web UI are untouched.

3. **Background jobs** — long-running operations (profile, relationships,
   correlation, setup) are executed in daemon threads. The browser polls a
   job-status endpoint and receives progress updates.

4. **Persistence-first** — every result (profile JSON, relationship JSON,
   correlation JSON, history JSON) is written to disk immediately after
   completion so pages auto-load saved state on every visit without
   re-executing queries.

5. **Graceful column failures** — a single bad column (e.g. `Division by zero`
   on a constant column) is isolated to that column's `error` field; profiling
   of all other columns continues.

---

## Module Dependency Graph

```
app.py
 ├── config.py            (load_config, all config dataclasses)
 ├── platforms/           (via _get_platform factory)
 │    ├── base.py
 │    ├── snowflake_platform.py → connection.py
 │    ├── sqlite_platform.py
 │    └── databricks_platform.py
 ├── profiling.py         (DataProfiler)
 ├── history.py           (HistoryManager)
 ├── alerts.py            (AlertManager)
 └── relationships.py     (RelationshipDetector)
```

`app.py` is the only module that imports from all others. The domain modules
(`profiling`, `history`, `alerts`, `relationships`) are independent of each
other and can be used standalone.

---

## Platform Abstraction Layer

### `BasePlatform` (platforms/base.py)

The abstract base class defines the contract every platform must fulfil:

```
BasePlatform
│
├── Execution
│    fetch_one(sql) → tuple | None
│    fetch_all(sql) → list[tuple]
│
├── Metadata
│    get_tables(database, schema) → list[str]
│    get_columns(database, schema, table) → list[(name, type, comment)]
│    get_table_comment(database, schema, table) → str | None
│    test_connection() → {"ok": bool, ...}
│
├── SQL builders  (return SQL strings — no execution)
│    table_ref(database, schema, table) → str
│    quote_col(name) → str
│    categorize_type(data_type) → "numeric" | "string" | "date" | None
│    basic_stats_sql(table_ref, q_col) → str
│    numeric_stats_sql(table_ref, q_col) → str
│    skew_kurt_sql(table_ref, q_col) → str | None
│    string_stats_sql(table_ref, q_col) → str
│    date_stats_sql(table_ref, q_col) → str
│    corr_sql(table_ref, col_names) → str | None
```

**Why SQL builders instead of direct execution?** It separates concerns: the
profiling engine decides *when* to call each query and how to handle errors; the
platform decides *what SQL* to use. This makes both testable in isolation.

### Platform Identifier Quoting

| Platform | Column quote | Table ref format |
|---|---|---|
| Snowflake | `"col"` | `"db"."schema"."table"` |
| SQLite | `"col"` | `"table"` (no db/schema) |
| Databricks | `` `col` `` | `` `catalog`.`schema`.`table` `` |

### Type Categorization

Each platform maps its native types to one of three categories used by the
profiling engine:

| Category | Stats computed |
|---|---|
| `"numeric"` | min, max, mean, median, stddev, variance, q1, q3, skewness, kurtosis |
| `"string"` | min_length, max_length, avg_length |
| `"date"` | min_date, max_date |
| `None` | Only basic stats (nulls, distinct, uniqueness) |

SQLite types follow the [affinity rules](https://www.sqlite.org/datatype3.html):
any type containing `INT` is numeric, any containing `CHAR`/`TEXT`/`CLOB` is
string, etc.

---

## Profiling Engine

### `DataProfiler` (profiling.py)

The profiler is fully platform-agnostic — it holds a reference to any
`BasePlatform` and calls only the methods defined in the abstract interface.

**Column profiling sequence:**

```
_profile_column(table_ref, col_name, data_type, row_count)
│
├─ 1. basic_stats_sql  → (null_count, distinct_count)
│
├─ 2. [if numeric]
│     numeric_stats_sql → (min, max, avg, median, stddev, variance, q1, q3)
│     [if stddev > 0]
│       skew_kurt_sql → (skewness, kurtosis)
│       [exception silently caught — guards Snowflake 100051]
│
├─ 3. [if string]
│     string_stats_sql → (min_length, max_length, avg_length)
│
└─ 4. [if date]
      date_stats_sql → (min_date, max_date)
```

**Error isolation:**

```python
for col_name, data_type, col_comment in columns:
    try:
        col_profiles.append(self._profile_column(...))
    except Exception as exc:
        col_profiles.append(ColumnProfile(..., error=str(exc)))
```

A per-column try/except ensures one bad column (e.g. `100051: Division by zero`
on a constant column) is captured in `ColumnProfile.error` without aborting the
whole table profile.

---

## Relationship Detection

### Algorithm (relationships.py)

For each column `C` in the source table:

1. Scan all saved profile JSONs for other tables that also have a column named `C`.
2. For each matching `(target_table, target_column)` pair, run a single
   round-trip query:

```sql
SELECT
    (SELECT COUNT(DISTINCT "C") FROM source WHERE "C" IS NOT NULL),
    (SELECT COUNT(DISTINCT "C") FROM target WHERE "C" IS NOT NULL),
    (SELECT COUNT(*) FROM (
        SELECT DISTINCT "C" AS v FROM source WHERE "C" IS NOT NULL
        EXCEPT
        SELECT DISTINCT "C" AS v FROM target WHERE "C" IS NOT NULL
    ))
```

3. Derive `matched = src_distinct - orphans`, `match_pct = matched / src_distinct`.
4. Classify: `PASS` (0 orphans), `WARN` (≥ 90% match), `FAIL` (< 90%).

**Platform quoting:** the detector uses `conn.table_ref()` and `conn.quote_col()`
when available (i.e. when `conn` is a `BasePlatform`), falling back to ANSI
double-quote style for legacy or mock connections.

---

## Correlation Matrix

### Snowflake / Databricks

One round-trip with n² SELECT expressions:

```sql
SELECT
    1.0,                                    -- diagonal (i=j)
    CORR("col_a"::FLOAT, "col_b"::FLOAT),  -- off-diagonal
    ...
FROM "db"."schema"."table"
```

### SQLite Fallback

SQLite has no `CORR()` function. When `platform.corr_sql()` returns `None`,
the app fetches all numeric column data into Python and computes Pearson r:

```
r(x, y) = Σ(xᵢ − x̄)(yᵢ − ȳ) / (σₓ · σᵧ)
```

This is acceptable for SQLite since it is designed for small datasets.
For large tables (> 100 k rows) consider using Snowflake or Databricks instead.

---

## Background Job Pattern

All long-running operations follow the same pattern:

```
POST /api/<action>   → validate input
                     → allocate job_id
                     → start daemon thread: _run_<action>_job(job_id, ...)
                     → return {"job_id": "..."}

GET  /api/<action>/<job_id>  → return _<action>_jobs[job_id]
                               # {"status": "pending"|"running"|"done"|"error",
                               #  ...result fields...}
```

The browser uses `setInterval(1500ms)` to poll until `status == "done"` or
`status == "error"`. Results are saved to disk inside the job function so
subsequent page loads retrieve them immediately without re-executing.

In-memory job state (`_jobs`, `_rel_jobs`, `_corr_jobs`, `_setup_jobs`) is
lost when the process restarts. This is intentional — the persisted JSON files
are the authoritative source of truth.

---

## File Naming Conventions

| Type | Path | Key format |
|---|---|---|
| Profile | `reports/<KEY>.json` | `DB__SCHEMA__TABLE` (uppercase, double underscore) |
| Relationships | `reports/relationships/<KEY>.json` | same |
| Correlations | `reports/correlations/<KEY>.json` | same |
| History | `profiling_history.json` (configurable) | `DB.SCHEMA.TABLE` (dot-separated) |
| Alert log | `profiler_alerts.log` (configurable) | — |

---

## Alert Evaluation Flow

```
AlertManager.evaluate_table_profile(profile, history)
│
├── Row count drift
│    if history and prev_count > 0:
│        change = |current - prev| / prev
│        if change > threshold → WARNING
│
└── Per column:
     ├── Null rate
     │    if null_rate > threshold:
     │        level = CRITICAL if > 50%, else WARNING
     │
     ├── Std dev drift
     │    if col.std_dev and prev_std_dev > 0:
     │        drift = |current - prev| / prev
     │        if drift > threshold → WARNING
     │
     └── Variance drift
          (same logic as std dev drift)
```

---

## Configuration Loading Priority

```
1. Environment variable          (highest priority)
2. config.yaml value
3. Built-in default              (lowest priority)
```

`load_config()` always calls `load_dotenv()` first so `.env` files are merged
into the environment before the YAML is read.

---

## Template Layout

```
base.html  (layout shell)
│  ├── topbar (fixed, 44px): hamburger + brand + db pill
│  ├── sidebar (fixed, 200px, collapsible): Dashboard / Run Profile / Configuration
│  ├── main (margin-left: 200px, transitions): page content + flash messages
│  └── apiFetch() JS helper (available on all pages)
│
├── index.html       extends base — stat cards + profiled-tables table
├── config.html      extends base — platform selector + connection forms + setup card
├── profile.html     extends base — cascading dropdowns + progress panel
└── report.html      extends base — 4-tab report (custom tab CSS + Chart.js)
```

The sidebar collapses on desktop (state persisted in `localStorage`) and slides
over content on mobile (< 768 px) with a semi-transparent overlay.

---

## Testing Strategy

| Layer | Approach |
|---|---|
| Config / dataclasses | Pure unit tests; `monkeypatch` for env vars |
| Snowflake connection | Mock `snowflake.connector.connect` entirely |
| Snowflake platform | Mock `SnowflakeConnection`; test SQL string content |
| SQLite platform | Real in-memory SQLite database via `tmp_path` fixtures |
| Databricks platform | Mock `databricks.sql.connect`; test SQL + quoting |
| DataProfiler | Real SQLite for integration; mock platform for edge cases |
| HistoryManager | Real temp files via `tmp_path` |
| AlertManager | Mock `requests.post`; real threshold arithmetic |
| RelationshipDetector | Real JSON files in `tmp_path`; mock `conn.fetch_one` |
| setup_sqlite | Real SQLite file; asserts row counts and FK integrity |

The key principle: **use real I/O where cheap** (SQLite, temp files) and
**mock at the network boundary** (Snowflake connector, Databricks SQL,
HTTP requests).
