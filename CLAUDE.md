# CLAUDE.md — DataLens

## Project at a glance

**DataLens** is a Python data-intelligence web app with two UIs (Flask + Streamlit) that profiles tables, detects column relationships, runs ML clustering, anomaly detection, and dimensionality reduction, and manages drift alerts — all without moving data out of the warehouse.

Supported platforms: **Snowflake**, **Databricks**, **SQLite**, **Snowflake Snowpark** (Streamlit in Snowflake).

Primary entry points: `streamlit_app.py` (Streamlit/SiS), `app.py` (Flask), `pages/` (Streamlit multi-page app).

---

## How I work — respect this

- **Show me results fast.** Make the change, run it, tell me what happened. I don't need a plan document or a design discussion for most things — just do it and show the diff.
- **One focused change at a time.** Don't refactor surrounding code, rename things, or clean up style unless I asked for that. Fix the thing I pointed at.
- **No filler.** No "Great question!", no summaries of what you just did, no "Let me know if you need anything else." Get in, do the work, get out.
- **Short responses.** One or two sentences after the tool calls is enough. If I want more explanation I'll ask.
- **Commit messages in Portuguese when I ask for commits.** Short, lowercase, descriptive. Example: `adiciona detecção de drift por coluna`.

---

## Tech stack

| Layer | Tool |
|---|---|
| Language | Python 3.x |
| Web UI (primary) | Streamlit (`streamlit_app.py` + `pages/`) |
| Web UI (API) | Flask (`app.py`) |
| Data platforms | Snowflake, Databricks, SQLite, Snowflake Snowpark (SiS) |
| ML | scikit-learn (clustering, anomaly detection, dim reduction, feature selection) |
| Charts | Plotly (Streamlit pages) / Chart.js (Flask templates) |
| LLM | Anthropic Claude, OpenAI, Ollama (`llm_providers.py`); Snowflake Cortex (`sis_cortex.py`) |
| Config | `config.yaml` + `.env` (env vars override YAML) |
| Tests | pytest (`pytest-cov`) |

---

## Key modules

| File | Responsibility |
|---|---|
| `config.py` | Load config, all dataclasses |
| `platforms/base.py` | `BasePlatform` abstract interface |
| `platforms/snowflake_platform.py` | Snowflake implementation |
| `platforms/databricks_platform.py` | Databricks implementation |
| `platforms/sqlite_platform.py` | SQLite implementation |
| `platforms/snowpark_platform.py` | Snowpark / Streamlit in Snowflake adapter |
| `profiling.py` | `DataProfiler` — column-level stats engine |
| `relationships.py` | `RelationshipDetector` — FK-like auto-detection |
| `clustering.py` | `ClusteringEngine` — clustering, anomaly detection, dim reduction |
| `llm_providers.py` | Multi-provider LLM wrapper (Ollama, OpenAI, Anthropic) |
| `llm_insights.py` | AI-powered data insights |
| `alerts.py` | `AlertManager` — drift detection + Slack |
| `history.py` | `HistoryManager` — profile snapshots |
| `feature_selector.py` | Feature selection for ML |
| `comment_generator.py` | Auto-generates column descriptions via LLM |
| `sis_session.py` | Snowpark session factory (SiS) |
| `sis_persistence.py` | Snowflake-native JSON persistence (SiS) |
| `sis_cortex.py` | Snowflake Cortex AI integration (SiS) |
| `streamlit_app.py` | SiS entry point — navigation router + home dashboard |
| `app.py` | Flask app + background job management |
| `pages/` | Streamlit pages (Profile, Report, Relationships, Clustering, Config, Help) |

---

## Architecture rules — never violate these

1. **No data transfer.** All computation runs as SQL inside the warehouse. The Python layer only receives scalar aggregates (counts, means, min/max), never raw rows.
2. **Platform abstraction.** New databases go into `platforms/` as a subclass of `BasePlatform`. The profiling engine and UI are never touched for a new platform.
3. **Domain modules are independent.** `profiling`, `history`, `alerts`, `relationships`, `clustering` do not import each other. Only `app.py` and the Streamlit pages import from multiple domain modules.
4. **Background jobs follow the standard pattern.** `POST /api/<action>` → allocate `job_id` → daemon thread → poll `GET /api/<action>/<job_id>`. Results persist to disk immediately.
5. **Persistence-first.** Every result (profile JSON, relationships JSON, correlation JSON, history JSON) is written to `reports/` right after completion. Subsequent page loads read from disk.
6. **Error isolation per column.** A bad column must never abort the whole table profile — it gets caught and stored in `ColumnProfile.error`.

---

## File naming

| Type | Path | Key format |
|---|---|---|
| Profile | `reports/<KEY>.json` | `DB__SCHEMA__TABLE` (uppercase, double underscore) |
| Relationships | `reports/relationships/<KEY>.json` | same |
| Correlations | `reports/correlations/<KEY>.json` | same |
| History | `profiling_history.json` | `DB.SCHEMA.TABLE` (dot-separated) |

---

## Testing

```bash
pytest                    # run all 247 tests
pytest --cov=. --cov-report=term-missing   # with coverage
```

**Testing strategy:** use real SQLite + temp files wherever cheap; mock only at network boundaries (Snowflake connector, Databricks SQL, HTTP requests). Never mock the database when a real in-memory SQLite can do the job.

---

## Running the app

```bash
# Streamlit (primary UI)
streamlit run streamlit_app.py

# Flask (API + legacy UI)
python app.py
```

Config is loaded from `config.yaml`. Secrets go in `.env` (env vars override YAML values).

---

## LLM usage

Configured in `config.yaml` under the `llm` key. Supported providers: `ollama`, `openai`, `anthropic`. The `llm_providers.py` module handles provider selection — add new providers there, nowhere else.

---

## What NOT to do

- Don't add comments that explain what the code does — names should do that.
- Don't add error handling for scenarios that can't happen inside these modules.
- Don't introduce new abstractions unless I explicitly ask for them.
- Don't move or rename files without being told to.
- Don't add backwards-compatibility shims for removed code.
- Don't touch tests unless the change explicitly requires it or I ask.
