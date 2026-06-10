# DataLens

**DataLens** is a multi-platform data intelligence tool with a built-in web UI
for profiling tables, detecting column relationships, computing correlation
matrices, running ML clustering with AI insights, and managing data-quality
alerts — all without moving data out of your warehouse.

Supports **Snowflake**, **Databricks (Apache Spark SQL)**, and **SQLite**
through a shared platform-abstraction layer. All computations execute
server-side.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Snowflake](#snowflake)
  - [SQLite](#sqlite)
  - [Databricks](#databricks)
  - [Alerts](#alerts)
  - [Environment Variables](#environment-variables)
- [Web UI](#web-ui)
- [CLI Profiling](#cli-profiling)
- [Sample Data](#sample-data)
- [Platform Support Matrix](#platform-support-matrix)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [API Reference](#api-reference)

---

## Features

| Feature | Description |
|---|---|
| **Statistical Profiling** | Row count, null rate, distinct count, uniqueness, min/max, mean, median, stddev, variance, skewness, kurtosis, quartiles for every column |
| **Multi-platform** | Snowflake, Databricks, SQLite — same UI and reports for all three |
| **Relationship Detection** | Auto-detects FK-like references across profiled tables using EXCEPT queries; supports manual annotations |
| **Correlation Matrix** | Pearson coefficients for all numeric column pairs, rendered as a colour-coded heatmap |
| **Drift Alerting** | Threshold-based alerts for null rate, row count change, std dev drift and variance drift; optional Slack notifications |
| **History Tracking** | Persists profile snapshots to JSON; compares current run against previous run for drift detection |
| **Snowflake Comments** | Fetches table/column COMMENT metadata and displays it inline on every column card |
| **Interactive Reports** | Tabbed HTML report with Overview charts (Chart.js), Column Details, Relationships tab, Correlation tab |
| **Sample Data** | Ready-to-run setup scripts for all three platforms (retail star schema: 7 tables, ~11 600 rows) |
| **Test Suite** | 247 unit tests with pytest covering all modules |

---

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                      Web UI (Flask)                        │
│  /config  /profile  /report  /api/*                        │
└───────────────────────┬────────────────────────────────────┘
                        │
          ┌─────────────▼─────────────┐
          │   _get_platform(cfg)      │  ← factory function
          └──┬────────┬──────────┬───┘
             │        │          │
    ┌────────▼─┐  ┌───▼────┐  ┌─▼──────────┐
    │Snowflake │  │SQLite  │  │Databricks  │
    │Platform  │  │Platform│  │Platform    │
    └────────┬─┘  └───┬────┘  └─┬──────────┘
             │        │          │
          ┌──▼────────▼──────────▼──┐
          │   BasePlatform          │  ← abstract interface
          │  fetch_one / fetch_all  │
          │  get_tables / columns   │
          │  SQL builders per type  │
          └──────────┬──────────────┘
                     │
          ┌──────────▼──────────────┐
          │   DataProfiler          │  ← platform-agnostic
          │   RelationshipDetector  │
          │   HistoryManager        │
          │   AlertManager          │
          └─────────────────────────┘
```

**Data flow for a profile run:**

```
Browser → POST /api/profile
        → background thread
        → DataProfiler.profile_table()
            → platform.get_columns()       # metadata
            → platform.basic_stats_sql()   # nulls + distinct
            → platform.numeric_stats_sql() # distribution
            → platform.skew_kurt_sql()     # if stddev > 0
        → save JSON  →  reports/<KEY>.json
        → HistoryManager.save_profile()
        → AlertManager.evaluate_table_profile()
        → (optional) AlertManager.send_slack()
        → GET /report?db=…&schema=…&table=…
```

---

## Quick Start

```powershell
# 1 — Clone / enter the project
cd c:\path\to\datalens

# 2 — Create virtual environment and install dependencies
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3 — Copy the example config and fill in your credentials
copy config.yaml.example config.yaml
# (edit config.yaml — see Configuration section below)

# 4 — Start the web server
python app.py

# 5 — Open http://localhost:5000
```

---

## Installation

### Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.10 or later |
| pip | 23 or later |

### Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1       # Windows
# source .venv/bin/activate        # macOS / Linux

pip install -r requirements.txt
```

### Dependencies

```
snowflake-connector-python>=3.5.0   # Snowflake platform
databricks-sql-connector>=3.0.0     # Databricks platform
flask>=3.0.0                        # web UI
pandas>=2.0.0                       # data manipulation
jinja2>=3.1.0                       # HTML templates
requests>=2.31.0                    # Slack webhook
python-dotenv>=1.0.0                # .env file support
pyyaml>=6.0                         # config file parsing
pytest>=8.0.0                       # test runner
pytest-cov>=5.0.0                   # coverage reporting
```

SQLite support uses Python's built-in `sqlite3` module — no extra package needed.

---

## Configuration

All settings are stored in `config.yaml` (editable via the web UI at `/config`).
Environment variables always take precedence over YAML values.

Copy the template:

```powershell
copy config.yaml.example config.yaml
```

### Snowflake

```yaml
platform: snowflake

snowflake:
  account:   your_account.us-east-1   # Snowflake account identifier
  user:      your_user
  password:  your_password            # or use SNOWFLAKE_PASSWORD env var
  warehouse: COMPUTE_WH
  database:  MY_DATABASE
  schema:    PUBLIC
  role:      SYSADMIN                 # optional
```

### SQLite

```yaml
platform: sqlite

sqlite:
  database_path: ./sample.db          # relative or absolute path
```

No server required. The profiler reads and queries the `.db` file directly.

### Databricks

```yaml
platform: databricks

databricks:
  host:      adb-1234567890123.12.azuredatabricks.net
  http_path: /sql/1.0/warehouses/abcdef1234567890
  token:     dapi...                  # Personal Access Token
  catalog:   main
  schema:    default
```

Environment variable overrides: `DATABRICKS_HOST`, `DATABRICKS_HTTP_PATH`,
`DATABRICKS_TOKEN`, `DATABRICKS_CATALOG`, `DATABRICKS_SCHEMA`.

### Alerts

```yaml
alerts:
  slack_webhook_url: ""               # leave empty to disable Slack
  log_file: profiler_alerts.log       # leave empty to disable file logging
  null_rate_threshold:        0.10    # alert when null% > 10%
  row_count_change_threshold: 0.20    # alert when row count shifts > 20%
  std_dev_change_threshold:   0.30    # alert when std dev shifts > 30%
```

Alert levels:

| Condition | Level |
|---|---|
| Null rate > threshold and ≤ 50% | WARNING |
| Null rate > 50% | CRITICAL |
| Row count change > threshold | WARNING |
| Std dev drift > threshold vs previous run | WARNING |
| Variance drift > threshold vs previous run | WARNING |

### Storage

```yaml
output_dir:   reports                 # where profile JSON files are saved
history_file: profiling_history.json  # drift-detection history
```

### Environment Variables

Create `.env` from the example:

```powershell
copy .env.example .env
```

```dotenv
# Snowflake
SNOWFLAKE_ACCOUNT=your_account.region
SNOWFLAKE_USER=your_user
SNOWFLAKE_PASSWORD=your_password
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=MY_DATABASE
SNOWFLAKE_SCHEMA=PUBLIC
SNOWFLAKE_ROLE=SYSADMIN           # optional

# Databricks
DATABRICKS_HOST=adb-xxx.azuredatabricks.net
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/xxx
DATABRICKS_TOKEN=dapi...
DATABRICKS_CATALOG=main
DATABRICKS_SCHEMA=default

# Alerts
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

---

## Web UI

Start the server:

```powershell
python app.py          # http://localhost:5000
```

### Pages

| URL | Purpose |
|---|---|
| `/` | Dashboard — lists all profiled tables with links to reports |
| `/config` | Configuration editor — platform selector, credentials, alert thresholds, sample-data setup |
| `/profile` | Run a profile — cascading dropdown (database → schema → table), live progress bar |
| `/report?db=X&schema=Y&table=Z` | Full report with 4 tabs |

### Report Tabs

| Tab | Content |
|---|---|
| **Overview** | Compact stat strip (rows, columns, null cols, numeric cols) + Null Rate chart + Uniqueness chart |
| **Columns** | Compact column cards with all stats; "Alerts only" filter |
| **Relationships** | Auto-detected and manually-annotated FK references; run/re-run; × delete; Add Manual modal |
| **Correlation** | Pearson correlation heatmap for all numeric columns |

Both Relationship and Correlation results are **auto-loaded on every page open**
from saved JSON files — no re-execution required.

---

## CLI Profiling

Profile a single table from the command line without starting the web server:

```powershell
# Basic — uses database/schema from config.yaml
python profile_table.py --table ORDERS

# Override any value
python profile_table.py --table ORDERS --schema SALES --database MY_DB

# JSON output (pipe to jq, save to file, etc.)
python profile_table.py --table ORDERS --json > orders_profile.json

# Use a different config file
python profile_table.py --table ORDERS --config /path/to/config.yaml
```

Output example (text mode):

```
Profiling MY_DB.PUBLIC.ORDERS ...

============================================================
Table : MY_DB.PUBLIC.ORDERS
Rows  : 1,234,567
Cols  : 8
At    : 2024-06-03T14:30:00
============================================================

  [ORDER_ID]  type=NUMBER
    nulls=0 (0.0%)  distinct=1,234,567  uniqueness=100.0%
    min=1  max=1,234,567  mean=617,284  median=617,284  stddev=356,343

  [STATUS]  type=VARCHAR
    nulls=120 (0.0%)  distinct=4  uniqueness=0.0%
    len: min=4  max=9  avg=6.3
```

---

## Sample Data

Three ready-to-run scripts create a **Retail Sales Star Schema** on your platform:

```
DIM_DATE  DIM_CUSTOMER  DIM_PROMOTION
    |          |              |
    └──────────┴──────────────┘
               |
           FACT_SALES
               |
    ┌──────────┼──────────┐
DIM_PRODUCT  DIM_STORE  DIM_EMPLOYEE
```

| Table | Rows | Notes |
|---|---|---|
| `DIM_DATE` | 731 | 2023-01-01 → 2024-12-31 with weekend/holiday flags |
| `DIM_CUSTOMER` | 500 | 4 segments; ~5% null `BIRTH_DATE` (intentional data quality test) |
| `DIM_PRODUCT` | 200 | 5 categories; cost + markup → price + margin % |
| `DIM_STORE` | 50 | 10 US cities; 3 store formats |
| `DIM_EMPLOYEE` | 100 | Linked to stores; ~5% inactive |
| `DIM_PROMOTION` | 30 | 4 types (Seasonal/Loyalty/Flash/Clearance); 5–50% discount |
| `FACT_SALES` | 10,000 | All financial measures pre-computed; ~30% have a promotion |

### Snowflake

Run `setup_snowflake.sql` in a Snowflake worksheet, or use the
**Sample Data Setup** card in the web UI at `/config`:

```sql
-- Edit the warehouse name on line 1
USE WAREHOUSE COMPUTE_WH;
-- Then run the rest of the file
```

Creates `SAMPLE_DW.RETAIL.*`.

### SQLite

```powershell
# Creates ./sample.db (default) or a custom path
python setup_sqlite.py
python setup_sqlite.py C:\data\retail.db
```

Or via the web UI: `/config` → Sample Data Setup → **Run SQLite Setup**.

### Databricks

Run `setup_databricks.sql` in a Databricks notebook or SQL editor:

```sql
-- Edit catalog/schema on lines 6-7
USE CATALOG main;
CREATE SCHEMA IF NOT EXISTS retail;
USE SCHEMA retail;
-- Then run the rest
```

Creates `main.retail.*`.

### Metadata / Comments

After the base setup, run `setup_snowflake_comments.sql` to add rich
`COMMENT ON TABLE` and `COMMENT ON COLUMN` descriptions for all 7 tables
and all 65 columns. These appear inline on every column card in the report.

---

## Platform Support Matrix

| Feature | Snowflake | SQLite | Databricks |
|---|:---:|:---:|:---:|
| Basic stats (nulls, distinct, uniqueness) | ✅ | ✅ | ✅ |
| Min / Max / Mean | ✅ | ✅ | ✅ |
| Median | ✅ | — | ✅ |
| Std Dev / Variance | ✅ | ✅ (approx)* | ✅ |
| Q1 / Q3 percentiles | ✅ | — | ✅ |
| Skewness / Kurtosis | ✅ | — | ✅ |
| String length stats | ✅ | ✅ | ✅ |
| Date range stats | ✅ | ✅ | ✅ |
| Column / Table COMMENT | ✅ | — | ✅ |
| Relationship detection | ✅ | ✅ | ✅ |
| Correlation matrix | ✅ (native `CORR()`) | ✅ (Python fallback) | ✅ (native `CORR()`) |
| Historical drift alerts | ✅ | ✅ | ✅ |

*SQLite approximate stddev: `SQRT(E[x²] − E[x]²)` (population stddev via a single-pass aggregate).

---

## Project Structure

```
datalens/
│
├── app.py                    Flask web application — all HTTP routes
├── config.py                 Configuration dataclasses + load_config()
├── connection.py             Low-level Snowflake connector wrapper
├── profiling.py              DataProfiler — platform-agnostic stat engine
├── history.py                HistoryManager — JSON persistence for drift detection
├── alerts.py                 AlertManager — threshold evaluation + Slack
├── relationships.py          RelationshipDetector — FK inference via EXCEPT queries
│
├── platforms/
│   ├── base.py               BasePlatform — abstract interface all connectors implement
│   ├── snowflake_platform.py Snowflake connector (wraps connection.py)
│   ├── sqlite_platform.py    SQLite connector (stdlib sqlite3, no extra deps)
│   └── databricks_platform.py Databricks connector (databricks-sql-connector)
│
├── templates/
│   ├── base.html             Dark-theme layout — topbar, collapsible sidebar, apiFetch helper
│   ├── index.html            Dashboard — table of profiled tables
│   ├── config.html           Configuration editor — platform selector + forms
│   ├── profile.html          Profile runner — cascading dropdowns, live progress
│   └── report.html           Report viewer — 4 tabs (Overview, Columns, Relationships, Correlation)
│
├── tests/
│   ├── conftest.py           sys.path setup for all test modules
│   ├── test_config.py        config.py — dataclasses and load_config branches
│   ├── test_connection.py    connection.py — mocked Snowflake connector
│   ├── test_profiling.py     profiling.py — mock platform + real SQLite
│   ├── test_history.py       history.py — JSON persistence
│   ├── test_alerts.py        alerts.py — all alert types + Slack
│   ├── test_relationships.py relationships.py — detection + quoting
│   ├── test_platform_base.py platforms/base.py — abstract contract
│   ├── test_platform_sqlite.py platforms/sqlite_platform.py — live SQLite
│   ├── test_platform_snowflake.py platforms/snowflake_platform.py — mocked
│   ├── test_platform_databricks.py platforms/databricks_platform.py — mocked
│   └── test_setup_sqlite.py  setup_sqlite.py — row counts, FK integrity
│
├── setup_snowflake.sql       Snowflake DDL + data generation (GENERATOR, UNIFORM, NORMAL)
├── setup_snowflake_comments.sql  COMMENT ON TABLE/COLUMN for all 7 tables
├── setup_sqlite.py           Python script — generates SQLite sample database
├── setup_databricks.sql      Databricks/Spark SQL DDL + data generation
├── profile_table.py          CLI tool — profile a single table, print or output JSON
│
├── config.yaml.example       Template — copy to config.yaml and edit
├── .env.example              Template — copy to .env for env-var-based secrets
├── requirements.txt          All Python dependencies
└── pytest.ini                pytest configuration
```

---

## Testing

```powershell
# Run all 247 tests
pytest

# Run with coverage report
pytest --cov=. --cov-report=term-missing

# Run a single module
pytest tests/test_platform_sqlite.py -v

# Run tests matching a keyword
pytest -k "relationship" -v
```

### Test Coverage per Module

| Module | Tests | Strategy |
|---|---|---|
| `config.py` | 20 | Pure unit — dataclasses, YAML parsing, env overrides |
| `connection.py` | 11 | Mocked `snowflake.connector.connect` |
| `profiling.py` | 19 | Real SQLite + mock platform for edge cases |
| `history.py` | 9 | Real temp files via `tmp_path` fixture |
| `alerts.py` | 27 | Mocked `requests.post`, real threshold logic |
| `relationships.py` | 17 | Temp JSON profile files + mock connection |
| `platforms/base.py` | 7 | Concrete minimal subclass |
| `platforms/sqlite_platform.py` | 37 | Real in-memory SQLite database |
| `platforms/snowflake_platform.py` | 33 | Mocked `SnowflakeConnection` |
| `platforms/databricks_platform.py` | 24 | Mocked `databricks.sql.connect` |
| `setup_sqlite.py` | 19 | Real SQLite file — row counts, FK integrity, idempotency |

---

## API Reference

All API routes return JSON. Non-JSON error responses (e.g. 404 HTML pages) are
caught by the `apiFetch` helper in the browser and shown as readable errors.

### Connection & Metadata

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/test-connection` | Test the active platform connection |
| `GET` | `/api/schemas?database=X` | List schemas in a database |
| `GET` | `/api/tables?database=X&schema=Y` | List base tables |

### Profiling

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/profile` | Start a background profiling job — body: `{database, schema, table}` |
| `GET` | `/api/profile/<job_id>` | Poll job status — returns `{status, row_count, column_count, alert_count}` |

### Relationships

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/relationships` | Start relationship detection — body: `{database, schema, table}` |
| `GET` | `/api/relationships/<job_id>` | Poll job status |
| `GET` | `/api/rel/saved?db=X&schema=Y&table=Z` | Load saved relationship results |
| `DELETE` | `/api/rel/entry?db=X&schema=Y&table=Z&idx=N` | Remove entry by array index |
| `POST` | `/api/rel/manual` | Add a manual relationship annotation |

### Correlation

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/correlation` | Start correlation computation — body: `{database, schema, table}` |
| `GET` | `/api/correlation/<job_id>` | Poll job status — returns `{columns, matrix}` |
| `GET` | `/api/correlation/saved?db=X&schema=Y&table=Z` | Load saved correlation matrix |

### Setup

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/setup` | Run the sample-data setup script — body: `{warehouse}` (Snowflake only) |
| `GET` | `/api/setup/<job_id>` | Poll setup job progress |

---

## Report JSON Format

Profile results are saved as JSON in `reports/<DB>__<SCHEMA>__<TABLE>.json`:

```json
{
  "database":     "SAMPLE_DW",
  "schema":       "RETAIL",
  "table":        "FACT_SALES",
  "platform":     "snowflake",
  "comment":      "Central fact table storing 10,000 sales transactions…",
  "row_count":    10000,
  "column_count": 17,
  "profiled_at":  "2024-06-03T14:30:00+00:00",
  "columns": [
    {
      "name":            "SALE_KEY",
      "data_type":       "NUMBER",
      "comment":         "Surrogate key. Sequential identifier (1–10,000).",
      "row_count":       10000,
      "null_count":      0,
      "null_rate":       0.0,
      "distinct_count":  10000,
      "uniqueness_rate": 1.0,
      "min_val":  1.0,
      "max_val":  10000.0,
      "mean":     5000.5,
      "median":   5000.5,
      "std_dev":  2886.9,
      "variance": 8333858.25,
      "skewness": 0.0,
      "kurtosis": -1.2,
      "q1":       2500.75,
      "q3":       7500.25,
      "error":    null
    }
  ]
}
```

---

## Extending with a New Platform

1. Create `platforms/my_platform.py` implementing all abstract methods from `BasePlatform`:

```python
from platforms.base import BasePlatform

class MyPlatform(BasePlatform):
    dialect = "myplatform"

    def fetch_one(self, sql, params=None): ...
    def fetch_all(self, sql, params=None): ...
    def get_tables(self, database, schema): ...
    def get_columns(self, database, schema, table): ...
    def table_ref(self, database, schema, table): ...
    def categorize_type(self, data_type): ...
    def basic_stats_sql(self, table_ref, q_col): ...
    def numeric_stats_sql(self, table_ref, q_col): ...
    def string_stats_sql(self, table_ref, q_col): ...
    def date_stats_sql(self, table_ref, q_col): ...
```

2. Register it in `app.py` → `_get_platform()`:

```python
if cfg.platform == "myplatform":
    from platforms.my_platform import MyPlatform
    return MyPlatform(cfg.my_platform)
```

3. Add a config dataclass in `config.py` and load it in `load_config()`.

4. Add a platform card in `templates/config.html`.

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `002043: Object does not exist` | Warehouse / database doesn't exist yet | Check the warehouse name in the Setup card |
| `100051: Division by zero` | Column with zero variance (constant column) | Fixed — profiler guards SKEW/KURTOSIS with `if stddev > 0` |
| `100046: Number out of representable range` | Large monetary columns overflow `FIXED(38,12)` | Fixed — all aggregates cast to `FLOAT` |
| `Unexpected token '<'` in browser | Flask app running old code without new routes | Restart `python app.py` |
| `sqlite3.OperationalError: no such table` | `sample.db` not created yet | Run `python setup_sqlite.py` |
| Databricks `ImportError` | `databricks-sql-connector` not installed | Run `pip install databricks-sql-connector` |
