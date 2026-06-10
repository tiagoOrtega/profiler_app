"""DataLens — multi-platform data profiling, ML clustering and AI insights web app."""

import dataclasses
import json
import os
import re
import sys
import threading
import uuid
from pathlib import Path

import snowflake.connector as _sf_connector
import yaml
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

sys.path.insert(0, str(Path(__file__).parent))

from alerts import AlertManager
from config import load_config
from connection import SnowflakeConnection
from history import HistoryManager
from profiling import DataProfiler
from relationships import RelationshipDetector
from clustering import ClusteringEngine, MODELS as CLUSTERING_MODELS
from llm_providers import get_provider
from llm_insights import InsightsEngine
from comment_generator import CommentGenerator
from feature_selector  import FeatureSelector


def _get_platform(cfg):
    """Return the correct BasePlatform for the active platform in cfg."""
    if cfg.platform == "sqlite":
        from platforms.sqlite_platform import SQLitePlatform
        return SQLitePlatform(cfg.sqlite.database_path)
    if cfg.platform == "databricks":
        from platforms.databricks_platform import DatabricksPlatform
        return DatabricksPlatform(cfg.databricks)
    from platforms.snowflake_platform import SnowflakePlatform
    return SnowflakePlatform(cfg.snowflake)

app = Flask(__name__)
app.secret_key = "snowflake-profiler-ui"

_CONFIG_PATH = str(Path(__file__).parent / "config.yaml")
_jobs: dict = {}
_setup_jobs: dict = {}
_rel_jobs: dict = {}
_corr_jobs: dict = {}
_cluster_jobs:  dict = {}
_insights_jobs: dict = {}
_comment_jobs:  dict = {}
_correxp_jobs:  dict = {}
_featimp_jobs:  dict = {}
_SETUP_SQL = Path(__file__).parent / "setup_snowflake.sql"


def _cfg():
    return load_config(_CONFIG_PATH)


def _reports_dir(cfg) -> Path:
    d = Path(cfg.output_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _report_path(cfg, db: str, schema: str, table: str) -> Path:
    key = f"{db.upper()}__{schema.upper()}__{table.upper()}"
    return _reports_dir(cfg) / f"{key}.json"


# ── Error handlers — API routes always return JSON ────────────────────────────

@app.errorhandler(404)
def _err_404(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": f"API endpoint not found: {request.path}"}), 404
    return render_template("base.html", cfg=_cfg()), 404


@app.errorhandler(500)
def _err_500(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": f"Internal server error: {e}"}), 500
    return render_template("base.html", cfg=_cfg()), 500


# ── Dashboard ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    cfg = _cfg()
    reports = []
    for fp in sorted(_reports_dir(cfg).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(fp) as f:
                reports.append(json.load(f))
        except Exception:
            pass
    return render_template("index.html", reports=reports, cfg=cfg)


# ── Config ─────────────────────────────────────────────────────────────────────

@app.route("/config", methods=["GET", "POST"])
def config_page():
    if request.method == "POST":
        role  = request.form.get("sf_role",  "").strip() or None
        slack = request.form.get("alert_slack_webhook", "").strip() or None
        payload = {
            "source_name": request.form.get("source_name", "My Source").strip() or "My Source",
            "platform": request.form.get("platform", "snowflake").strip(),
            "snowflake": {
                "account":   request.form.get("sf_account",   "").strip(),
                "user":      request.form.get("sf_user",      "").strip(),
                "password":  request.form.get("sf_password",  "").strip(),
                "warehouse": request.form.get("sf_warehouse", "").strip(),
                "database":  request.form.get("sf_database",  "").strip(),
                "schema":    request.form.get("sf_schema",    "").strip(),
                "role": role,
            },
            "sqlite": {
                "database_path": request.form.get("sqlite_db_path", "./sample.db").strip(),
            },
            "databricks": {
                "host":      request.form.get("db_host",      "").strip(),
                "http_path": request.form.get("db_http_path", "").strip(),
                "token":     request.form.get("db_token",     "").strip(),
                "catalog":   request.form.get("db_catalog",   "main").strip(),
                "schema":    request.form.get("db_schema",    "default").strip(),
            },
            "alerts": {
                "slack_webhook_url": slack,
                "log_file": request.form.get("alert_log_file", "profiler_alerts.log").strip(),
                "null_rate_threshold": float(request.form.get("alert_null_rate", 0.10)),
                "row_count_change_threshold": float(request.form.get("alert_row_count_change", 0.20)),
                "std_dev_change_threshold": float(request.form.get("alert_std_dev_change", 0.30)),
            },
            "output_dir":   request.form.get("output_dir",   "reports").strip(),
            "history_file": request.form.get("history_file", "profiling_history.json").strip(),
            "llm": {
                "provider":    request.form.get("llm_provider",    "disabled").strip(),
                "model":       request.form.get("llm_model",       "llama3.2").strip(),
                "base_url":    request.form.get("llm_base_url",    "http://localhost:11434").strip(),
                "api_key":     request.form.get("llm_api_key",     "").strip(),
                "temperature": float(request.form.get("llm_temperature", 0.3)),
                "max_tokens":  int(request.form.get("llm_max_tokens", 512)),
            },
        }
        with open(_CONFIG_PATH, "w") as f:
            yaml.dump(payload, f, default_flow_style=False, allow_unicode=True)
        flash("Configuration saved.", "success")
        return redirect(url_for("config_page"))

    raw: dict = {}
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH) as f:
            raw = yaml.safe_load(f) or {}
    return render_template("config.html", raw=raw, cfg=_cfg())


# ── API: connection / metadata ─────────────────────────────────────────────────

@app.route("/api/test-connection", methods=["POST"])
def api_test_connection():
    try:
        result = _get_platform(_cfg()).test_connection()
        status = 200 if result.get("ok") else 400
        return jsonify(result), status
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/schemas")
def api_schemas():
    db = request.args.get("database", "").strip()
    cfg = _cfg()
    try:
        platform = _get_platform(cfg)
        if cfg.platform == "sqlite":
            return jsonify({"schemas": platform.get_schemas(db)})
        if cfg.platform == "databricks":
            return jsonify({"schemas": platform.get_schemas(db)})
        # Snowflake
        rows = platform.fetch_all(f"""
            SELECT SCHEMA_NAME FROM "{db}".INFORMATION_SCHEMA.SCHEMATA
            WHERE SCHEMA_NAME != 'INFORMATION_SCHEMA' ORDER BY SCHEMA_NAME
        """)
        return jsonify({"schemas": [r[0] for r in rows]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/tables")
def api_tables():
    db     = request.args.get("database", "").strip()
    schema = request.args.get("schema",   "").strip()
    try:
        tables = DataProfiler(_get_platform(_cfg())).get_tables(db, schema)
        return jsonify({"tables": tables})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


# ── Profiling jobs ─────────────────────────────────────────────────────────────

def _run_job(job_id: str, db: str, schema: str, table: str) -> None:
    _jobs[job_id]["status"] = "running"
    try:
        cfg = _cfg()
        profile = DataProfiler(_get_platform(cfg)).profile_table(db, schema, table)
        profile.source_name = cfg.source_name   # tag with the active source label

        rp = _report_path(cfg, db, schema, table)
        with open(rp, "w") as f:
            json.dump(dataclasses.asdict(profile), f, indent=2, default=str)

        hm = HistoryManager(cfg.history_file)
        prev = hm.get_history(db, schema, table)
        hm.save_profile(profile)

        am = AlertManager(cfg.alerts)
        am.evaluate_table_profile(profile, prev)
        am.send_slack(f"Profiled {db}.{schema}.{table}")

        _jobs[job_id].update({
            "status":       "done",
            "database":     db,
            "schema":       schema,
            "table":        table,
            "row_count":    profile.row_count,
            "column_count": profile.column_count,
            "alert_count":  len(am.alerts),
            "error_count":  sum(1 for c in profile.columns if c.error),
        })
    except Exception as exc:
        _jobs[job_id].update({"status": "error", "error": str(exc)})


@app.route("/api/profile", methods=["POST"])
def api_profile():
    body = request.get_json(silent=True) or {}
    db = body.get("database", "").strip()
    schema = body.get("schema", "").strip()
    table = body.get("table", "").strip()
    if not all([db, schema, table]):
        return jsonify({"error": "database, schema and table are required"}), 400
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending", "error": None}
    threading.Thread(target=_run_job, args=(job_id, db, schema, table), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/profile/<job_id>")
def api_profile_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.route("/profile")
def profile_page():
    cfg = _cfg()
    recent = []
    try:
        for fp in sorted(_reports_dir(cfg).glob("*.json"),
                         key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
            with open(fp) as f:
                d = json.load(f)
            recent.append({
                "db":          d.get("database", ""),
                "schema":      d.get("schema",   ""),
                "table":       d.get("table",    ""),
                "row_count":   d.get("row_count", 0),
                "profiled_at": (d.get("profiled_at") or "")[:10],
                "source_name": d.get("source_name", ""),
                "platform":    d.get("platform", ""),
            })
    except Exception:
        pass
    return render_template("profile.html", cfg=cfg, recent=recent)


@app.route("/report")
def report_page():
    db = request.args.get("db", "").strip().upper()
    schema = request.args.get("schema", "").strip().upper()
    table = request.args.get("table", "").strip().upper()
    if not all([db, schema, table]):
        return redirect(url_for("index"))
    cfg = _cfg()
    rp = _report_path(cfg, db, schema, table)
    if not rp.exists():
        flash(f"No report found for {db}.{schema}.{table}. Run a profile first.", "warning")
        return redirect(url_for("profile_page"))
    with open(rp) as f:
        profile = json.load(f)
    numeric_count   = sum(1 for c in profile["columns"] if c.get("mean") is not None)
    null_col_count  = sum(1 for c in profile["columns"] if c.get("null_rate", 0) > 0)
    alert_col_count = sum(1 for c in profile["columns"] if c.get("null_rate", 0) > 0.1)
    error_col_count = sum(1 for c in profile["columns"] if c.get("error"))
    chart_height = max(220, min(600, len(profile["columns"]) * 32 + 60))
    return render_template(
        "report.html",
        profile=profile,
        cfg=cfg,
        numeric_count=numeric_count,
        null_col_count=null_col_count,
        alert_col_count=alert_col_count,
        error_col_count=error_col_count,
        chart_height=chart_height,
    )


# ── Setup script execution ────────────────────────────────────────────────────

def _split_sql(text: str) -> list:
    """Split a SQL file into individual executable statements."""
    stmts = []
    for raw in text.split(";"):
        lines = [ln for ln in raw.split("\n")
                 if ln.strip() and not ln.strip().startswith("--")]
        cleaned = "\n".join(lines).strip()
        if cleaned:
            stmts.append(cleaned)
    return stmts


def _run_setup_job(job_id: str, warehouse: str) -> None:
    _setup_jobs[job_id]["status"] = "running"
    cfg = _cfg()

    # ── SQLite: run Python setup script directly ──────────────────────────────
    if cfg.platform == "sqlite":
        try:
            import setup_sqlite
            _setup_jobs[job_id].update({"total": 7, "current": 0, "preview": "Creating tables…"})
            setup_sqlite.setup(cfg.sqlite.database_path)
            _setup_jobs[job_id].update({"status": "done", "current": 7})
        except Exception as exc:
            _setup_jobs[job_id].update({"status": "error", "error": str(exc)})
        return

    # ── Databricks: run setup_databricks.sql ─────────────────────────────────
    if cfg.platform == "databricks":
        sql_file = Path(__file__).parent / "setup_databricks.sql"
        if not sql_file.exists():
            _setup_jobs[job_id].update({"status": "error", "error": "setup_databricks.sql not found"})
            return
        try:
            sql_text = sql_file.read_text(encoding="utf-8")
            stmts = _split_sql(sql_text)
            total = len(stmts)
            _setup_jobs[job_id]["total"] = total
            platform = _get_platform(cfg)
            for i, stmt in enumerate(stmts, 1):
                preview = stmt.replace("\n", " ").strip()[:120]
                _setup_jobs[job_id].update({"current": i, "preview": preview})
                try:
                    platform.fetch_all(stmt)
                except Exception as exc:
                    raise RuntimeError(f"Statement {i}/{total} failed — {exc}\nSQL: {preview}") from exc
            _setup_jobs[job_id]["status"] = "done"
        except Exception as exc:
            _setup_jobs[job_id].update({"status": "error", "error": str(exc)})
        return

    # ── Snowflake: run setup_snowflake.sql via raw connector ──────────────────
    conn = None
    cur  = None
    try:
        if not _SETUP_SQL.exists():
            raise FileNotFoundError("setup_snowflake.sql not found in project directory")

        sql_text = _SETUP_SQL.read_text(encoding="utf-8")
        sql_text = sql_text.replace("USE WAREHOUSE COMPUTE_WH", f"USE WAREHOUSE {warehouse}", 1)
        stmts = _split_sql(sql_text)
        total = len(stmts)
        _setup_jobs[job_id]["total"] = total

        connect_params = {
            "account":  cfg.snowflake.account,
            "user":     cfg.snowflake.user,
            "password": cfg.snowflake.password,
            "client_session_keep_alive": False,
        }
        if cfg.snowflake.role:
            connect_params["role"] = cfg.snowflake.role

        conn = _sf_connector.connect(**connect_params)
        cur  = conn.cursor()

        for i, stmt in enumerate(stmts, 1):
            first_line = stmt.replace("\n", " ").strip()[:120]
            _setup_jobs[job_id].update({"current": i, "preview": first_line})
            try:
                cur.execute(stmt)
            except Exception as stmt_exc:
                raise RuntimeError(
                    f"Statement {i}/{total} failed — {stmt_exc}\n\nSQL: {first_line}"
                ) from stmt_exc

        _setup_jobs[job_id]["status"] = "done"

    except Exception as exc:
        _setup_jobs[job_id].update({"status": "error", "error": str(exc)})
    finally:
        if cur:
            try: cur.close()
            except Exception: pass
        if conn:
            try: conn.close()
            except Exception: pass


@app.route("/api/setup", methods=["POST"])
def api_run_setup():
    body = request.get_json(silent=True) or {}
    warehouse = body.get("warehouse", "").strip()
    if not warehouse:
        warehouse = _cfg().snowflake.warehouse or ""
    if not warehouse:
        return jsonify({"error": "Warehouse name is required"}), 400
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_$]*$", warehouse):
        return jsonify({"error": f"Invalid warehouse name: {warehouse!r}"}), 400

    job_id = str(uuid.uuid4())
    _setup_jobs[job_id] = {
        "status": "pending", "current": 0, "total": 0, "preview": "", "error": None,
    }
    threading.Thread(target=_run_setup_job, args=(job_id, warehouse), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/setup/<job_id>")
def api_setup_status(job_id: str):
    job = _setup_jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


# ── Relationship persistence helpers ─────────────────────────────────────────

def _rel_saved_path(cfg, db: str, schema: str, table: str) -> Path:
    d = _reports_dir(cfg) / "relationships"
    d.mkdir(exist_ok=True)
    key = f"{db.upper()}__{schema.upper()}__{table.upper()}"
    return d / f"{key}.json"


def _load_rel_saved(cfg, db: str, schema: str, table: str) -> dict:
    p = _rel_saved_path(cfg, db, schema, table)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {"database": db, "schema": schema, "table": table,
            "last_tested": None, "relationships": []}


def _save_rel_file(cfg, db: str, schema: str, table: str, data: dict) -> None:
    with open(_rel_saved_path(cfg, db, schema, table), "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── Relationship detection ────────────────────────────────────────────────────

def _run_rel_job(job_id: str, db: str, schema: str, table: str) -> None:
    _rel_jobs[job_id]["status"] = "running"
    try:
        from datetime import datetime as _dt
        cfg = _cfg()
        detector = RelationshipDetector(_get_platform(cfg), _reports_dir(cfg))
        auto_results = [dataclasses.asdict(r) for r in detector.detect(db, schema, table)]
        for r in auto_results:
            r["is_manual"] = False

        # Preserve any manually-added entries from a previous run
        existing = _load_rel_saved(cfg, db, schema, table)
        manual = [r for r in existing.get("relationships", []) if r.get("is_manual")]

        all_rels = auto_results + manual
        save_data = {
            "database": db, "schema": schema, "table": table,
            "last_tested": _dt.utcnow().isoformat(),
            "relationships": all_rels,
        }
        _save_rel_file(cfg, db, schema, table, save_data)
        _rel_jobs[job_id].update({"status": "done", **save_data})
    except Exception as exc:
        _rel_jobs[job_id].update({"status": "error", "error": str(exc)})


@app.route("/api/relationships", methods=["POST"])
def api_relationships():
    body   = request.get_json(silent=True) or {}
    db     = body.get("database", "").strip()
    schema = body.get("schema",   "").strip()
    table  = body.get("table",    "").strip()
    if not all([db, schema, table]):
        return jsonify({"error": "database, schema and table are required"}), 400
    job_id = str(uuid.uuid4())
    _rel_jobs[job_id] = {"status": "pending", "relationships": None, "error": None}
    threading.Thread(target=_run_rel_job, args=(job_id, db, schema, table), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/relationships/<job_id>")
def api_relationships_status(job_id: str):
    job = _rel_jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


@app.route("/api/rel/saved")
def api_rel_saved():
    db     = request.args.get("db",     "").strip().upper()
    schema = request.args.get("schema", "").strip().upper()
    table  = request.args.get("table",  "").strip().upper()
    if not all([db, schema, table]):
        return jsonify({"error": "db, schema, table required"}), 400
    return jsonify(_load_rel_saved(_cfg(), db, schema, table))


@app.route("/api/rel/entry", methods=["DELETE"])
def api_rel_delete_entry():
    db     = request.args.get("db",     "").strip().upper()
    schema = request.args.get("schema", "").strip().upper()
    table  = request.args.get("table",  "").strip().upper()
    try:
        idx = int(request.args.get("idx", -1))
    except ValueError:
        return jsonify({"error": "idx must be integer"}), 400
    if not all([db, schema, table]) or idx < 0:
        return jsonify({"error": "db, schema, table, idx required"}), 400
    cfg  = _cfg()
    data = _load_rel_saved(cfg, db, schema, table)
    rels = data.get("relationships", [])
    if idx >= len(rels):
        return jsonify({"error": "index out of range"}), 400
    rels.pop(idx)
    data["relationships"] = rels
    _save_rel_file(cfg, db, schema, table, data)
    return jsonify(data)


@app.route("/api/rel/manual", methods=["POST"])
def api_rel_add_manual():
    body   = request.get_json(silent=True) or {}
    db     = body.get("database", "").strip().upper()
    schema = body.get("schema",   "").strip().upper()
    table  = body.get("table",    "").strip().upper()
    entry  = body.get("entry",    {})
    if not all([db, schema, table, entry]):
        return jsonify({"error": "database, schema, table, entry required"}), 400
    for field_name in ("source_column", "target_table", "target_column"):
        if not str(entry.get(field_name, "")).strip():
            return jsonify({"error": f"entry.{field_name} is required"}), 400
    manual = {
        "source_column": entry["source_column"].strip(),
        "target_db":     (entry.get("target_db",     db    ) or db    ).strip().upper(),
        "target_schema": (entry.get("target_schema", schema) or schema).strip().upper(),
        "target_table":  entry["target_table"].strip().upper(),
        "target_column": entry["target_column"].strip().upper(),
        "src_distinct": None, "tgt_distinct": None,
        "matched": None, "orphans": None, "match_pct": None,
        "status":    (entry.get("status", "MANUAL") or "MANUAL").strip().upper(),
        "is_manual": True,
        "note":      entry.get("note", "").strip() or None,
    }
    cfg  = _cfg()
    data = _load_rel_saved(cfg, db, schema, table)
    data.setdefault("relationships", []).append(manual)
    _save_rel_file(cfg, db, schema, table, data)
    return jsonify(data)


# ── Correlation persistence helpers ──────────────────────────────────────────

def _corr_saved_path(cfg, db: str, schema: str, table: str) -> Path:
    d = _reports_dir(cfg) / "correlations"
    d.mkdir(exist_ok=True)
    key = f"{db.upper()}__{schema.upper()}__{table.upper()}"
    return d / f"{key}.json"


def _load_corr_saved(cfg, db: str, schema: str, table: str) -> dict:
    p = _corr_saved_path(cfg, db, schema, table)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {"database": db, "schema": schema, "table": table,
            "computed_at": None, "columns": [], "matrix": []}


def _save_corr_file(cfg, db: str, schema: str, table: str, data: dict) -> None:
    with open(_corr_saved_path(cfg, db, schema, table), "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── Correlation matrix ────────────────────────────────────────────────────────

def _run_corr_job(job_id: str, db: str, schema: str, table: str) -> None:
    _corr_jobs[job_id]["status"] = "running"
    try:
        from datetime import datetime as _dt
        cfg = _cfg()
        rp  = _report_path(cfg, db, schema, table)
        if not rp.exists():
            raise FileNotFoundError(f"Profile not found for {db}.{schema}.{table}. Run a profile first.")
        with open(rp) as f:
            profile = json.load(f)

        num_cols = [c["name"] for c in profile["columns"] if c.get("mean") is not None][:15]
        if len(num_cols) < 2:
            result = {
                "status": "done", "columns": num_cols, "matrix": [],
                "message": "Need at least 2 numeric columns for correlation analysis.",
            }
            _corr_jobs[job_id].update(result)
            return

        platform = _get_platform(cfg)
        tbl_ref  = platform.table_ref(db, schema, table)
        n        = len(num_cols)
        corr_sql = platform.corr_sql(tbl_ref, num_cols)

        if corr_sql:
            # Native CORR() — one round-trip
            row = platform.fetch_one(corr_sql)
            matrix, k = [], 0
            for i in range(n):
                matrix.append([
                    round(float(row[k + j]), 4) if row and row[k + j] is not None else None
                    for j in range(n)
                ])
                k += n
        else:
            # Fallback: fetch all numeric data and compute in Python
            # (used for SQLite which has no CORR() function)
            cols_sql = ", ".join(f'"{c}"' for c in num_cols)
            rows = platform.fetch_all(
                f"SELECT {cols_sql} FROM {tbl_ref} WHERE "
                + " AND ".join(f'"{c}" IS NOT NULL' for c in num_cols)
            )
            if not rows:
                raise ValueError("No rows with all-non-null numeric columns.")
            import math
            data = [[float(r[i]) for r in rows] for i in range(n)]
            def pearson(xs, ys):
                xm = sum(xs)/len(xs); ym = sum(ys)/len(ys)
                num = sum((x-xm)*(y-ym) for x,y in zip(xs,ys))
                dx  = math.sqrt(sum((x-xm)**2 for x in xs))
                dy  = math.sqrt(sum((y-ym)**2 for y in ys))
                return round(num/(dx*dy), 4) if dx and dy else None
            matrix = [
                [1.0 if i==j else pearson(data[i], data[j]) for j in range(n)]
                for i in range(n)
            ]

        save_data = {
            "database": db, "schema": schema, "table": table,
            "computed_at": _dt.utcnow().isoformat(),
            "columns": num_cols,
            "matrix":  matrix,
        }
        _save_corr_file(cfg, db, schema, table, save_data)
        _corr_jobs[job_id].update({"status": "done", **save_data})
    except Exception as exc:
        _corr_jobs[job_id].update({"status": "error", "error": str(exc)})


@app.route("/api/correlation", methods=["POST"])
def api_correlation():
    body   = request.get_json(silent=True) or {}
    db     = body.get("database", "").strip()
    schema = body.get("schema",   "").strip()
    table  = body.get("table",    "").strip()
    if not all([db, schema, table]):
        return jsonify({"error": "database, schema and table required"}), 400
    job_id = str(uuid.uuid4())
    _corr_jobs[job_id] = {"status": "pending", "columns": [], "matrix": [], "error": None}
    threading.Thread(target=_run_corr_job, args=(job_id, db, schema, table), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/correlation/saved")
def api_corr_saved():
    db     = request.args.get("db",     "").strip().upper()
    schema = request.args.get("schema", "").strip().upper()
    table  = request.args.get("table",  "").strip().upper()
    if not all([db, schema, table]):
        return jsonify({"error": "db, schema, table required"}), 400
    return jsonify(_load_corr_saved(_cfg(), db, schema, table))


@app.route("/api/correlation/<job_id>")
def api_correlation_status(job_id: str):
    job = _corr_jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


# ── Clustering ────────────────────────────────────────────────────────────────

def _cluster_saved_path(cfg, db: str, schema: str, table: str) -> Path:
    d = _reports_dir(cfg) / "clustering"
    d.mkdir(exist_ok=True)
    key = f"{db.upper()}__{schema.upper()}__{table.upper()}"
    return d / f"{key}.json"


def _load_cluster_saved(cfg, db: str, schema: str, table: str) -> dict:
    p = _cluster_saved_path(cfg, db, schema, table)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {"columns_used": [], "n_clusters": 0, "computed_at": None}


def _save_cluster_file(cfg, db: str, schema: str, table: str, data: dict) -> None:
    with open(_cluster_saved_path(cfg, db, schema, table), "w") as f:
        json.dump(data, f, indent=2, default=str)


def _run_cluster_job(
    job_id: str, db: str, schema: str, table: str,
    model_name: str, params: dict, columns: list,
    feature_ids: list, sample_size: int, target_col: str = "",
) -> None:
    _cluster_jobs[job_id]["status"] = "running"
    try:
        from datetime import datetime as _dt
        cfg    = _cfg()
        engine = ClusteringEngine(_get_platform(cfg), _reports_dir(cfg))
        result = engine.run(
            db, schema, table,
            model_name=model_name,
            params=params,
            columns=columns or None,
            feature_ids=feature_ids or None,
            sample_size=sample_size,
        )
        save_data = {
            **result,
            "database":    db,
            "schema":      schema,
            "table":       table,
            "source_name": cfg.source_name,
            "computed_at": _dt.now().isoformat(),
            # ── Persisted configuration ──────────────────────────────────────
            # Restored on next page open so the user never loses their setup.
            "config": {
                "model":       model_name,
                "params":      params,
                "feature_ids": feature_ids or [],
                "sample_size": sample_size,
                "target_col":  target_col,
            },
        }
        _save_cluster_file(cfg, db, schema, table, save_data)
        _cluster_jobs[job_id].update({"status": "done", **save_data})
    except Exception as exc:
        _cluster_jobs[job_id].update({"status": "error", "error": str(exc)})


@app.route("/api/clustering/models")
def api_clustering_models():
    """Return model registry (params schema) for the frontend."""
    return jsonify(CLUSTERING_MODELS)


@app.route("/api/clustering/auto-columns")
def api_clustering_auto_columns():
    """Return auto-selected numeric columns for a table."""
    db     = request.args.get("db",     "").strip().upper()
    schema = request.args.get("schema", "").strip().upper()
    table  = request.args.get("table",  "").strip().upper()
    if not all([db, schema, table]):
        return jsonify({"error": "db, schema, table required"}), 400
    try:
        cfg    = _cfg()
        engine = ClusteringEngine(_get_platform(cfg), _reports_dir(cfg))
        cols   = engine.auto_select_columns(db, schema, table)
        return jsonify({"columns": cols})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/clustering", methods=["POST"])
def api_run_clustering():
    body        = request.get_json(silent=True) or {}
    db          = body.get("database",    "").strip()
    schema      = body.get("schema",      "").strip()
    table       = body.get("table",       "").strip()
    model_name  = body.get("model",       "kmeans").strip()
    params      = body.get("params",      {})
    columns     = body.get("columns",     [])
    feature_ids = body.get("feature_ids", [])
    sample_size = int(body.get("sample_size", 10_000))
    target_col  = body.get("target_col",  "").strip()

    if not all([db, schema, table]):
        return jsonify({"error": "database, schema and table are required"}), 400
    if model_name not in CLUSTERING_MODELS:
        return jsonify({"error": f"Unknown model: {model_name!r}"}), 400

    job_id = str(uuid.uuid4())
    _cluster_jobs[job_id] = {"status": "pending", "error": None}
    threading.Thread(
        target=_run_cluster_job,
        args=(job_id, db, schema, table, model_name, params, columns,
              feature_ids, sample_size, target_col),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/api/clustering/config", methods=["POST"])
def api_save_cluster_config():
    """Save clustering configuration without re-running the model."""
    body   = request.get_json(silent=True) or {}
    db     = body.get("database", "").strip()
    schema = body.get("schema",   "").strip()
    table  = body.get("table",    "").strip()
    config = body.get("config",   {})
    if not all([db, schema, table]):
        return jsonify({"error": "database, schema and table are required"}), 400

    cfg  = _cfg()
    path = _cluster_saved_path(cfg, db.upper(), schema.upper(), table.upper())

    # Load existing results (keep them) and just update the config section
    existing = {}
    if path.exists():
        with open(path) as f:
            existing = json.load(f)
    existing["config"] = config
    with open(path, "w") as f:
        json.dump(existing, f, indent=2, default=str)

    return jsonify({"ok": True, "saved": config})


@app.route("/api/clustering/<job_id>")
def api_clustering_status(job_id: str):
    job = _cluster_jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


@app.route("/api/clustering/features")
def api_clustering_features():
    """Return auto-suggested features (originals + transforms + ratios) for a table."""
    db     = request.args.get("db",     "").strip().upper()
    schema = request.args.get("schema", "").strip().upper()
    table  = request.args.get("table",  "").strip().upper()
    if not all([db, schema, table]):
        return jsonify({"error": "db, schema, table required"}), 400
    try:
        cfg     = _cfg()
        engine  = ClusteringEngine(_get_platform(cfg), _reports_dir(cfg))
        suggestions = engine.suggest_features(db, schema, table)
        return jsonify({"features": suggestions})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/clustering/saved")
def api_cluster_saved():
    db     = request.args.get("db",     "").strip().upper()
    schema = request.args.get("schema", "").strip().upper()
    table  = request.args.get("table",  "").strip().upper()
    if not all([db, schema, table]):
        return jsonify({"error": "db, schema, table required"}), 400
    return jsonify(_load_cluster_saved(_cfg(), db, schema, table))


# ── LLM provider ──────────────────────────────────────────────────────────────

@app.route("/api/llm/status")
def api_llm_status():
    cfg      = _cfg()
    provider = get_provider(cfg.llm)
    info     = provider.info()
    if cfg.llm.provider == "ollama":
        try:
            info["available_models"] = provider.list_models()
        except Exception:
            info["available_models"] = []
    return jsonify(info)


@app.route("/api/llm/test", methods=["POST"])
def api_llm_test():
    """
    Test the LLM provider.
    Accepts an optional JSON body with inline config so the user can test
    BEFORE saving (form values → body → test, no save required).
    Falls back to the saved config when the body is empty.
    """
    from config import LLMConfig
    body = request.get_json(silent=True) or {}

    if body.get("provider") and body["provider"] != "disabled":
        cfg_llm = LLMConfig(
            provider=body.get("provider", "disabled"),
            model=body.get("model", "llama3.2"),
            base_url=body.get("base_url", "http://localhost:11434"),
            api_key=body.get("api_key", ""),
            temperature=float(body.get("temperature", 0.3)),
            max_tokens=int(body.get("max_tokens", 512)),
        )
    else:
        cfg_llm = _cfg().llm

    try:
        provider = get_provider(cfg_llm)
        if cfg_llm.provider == "disabled":
            return jsonify({
                "ok": False,
                "error": "LLM provider is set to Disabled. "
                         "Select Ollama, OpenAI or Anthropic and save first.",
            }), 400

        if not provider.is_available():
            hint = ""
            if cfg_llm.provider == "ollama":
                hint = " Is Ollama running? Run: scripts\\start_llm.ps1"
            elif cfg_llm.provider in ("openai", "anthropic"):
                hint = " Check your API key."
            return jsonify({
                "ok": False,
                "error": f"Provider '{cfg_llm.provider}' is not reachable.{hint}",
            }), 400

        resp = provider.generate(
            "Reply with exactly two words: connection successful",
            temperature=0.1, max_tokens=20,
        )
        return jsonify({
            "ok":       True,
            "response": resp or "(connected — no text returned)",
            "provider": cfg_llm.provider,
            "model":    cfg_llm.model,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


# ── Clustering insights ────────────────────────────────────────────────────────

def _insights_saved_path(cfg, db: str, schema: str, table: str) -> Path:
    d = _reports_dir(cfg) / "clustering"
    d.mkdir(exist_ok=True)
    key = f"{db.upper()}__{schema.upper()}__{table.upper()}"
    return d / f"{key}_insights.json"


def _run_insights_job(job_id: str, db: str, schema: str, table: str) -> None:
    _insights_jobs[job_id]["status"] = "running"
    try:
        cfg          = _cfg()
        # Load saved clustering result
        saved_path   = _cluster_saved_path(cfg, db, schema, table)
        if not saved_path.exists():
            raise FileNotFoundError("No saved clustering result found. Run clustering first.")
        with open(saved_path) as f:
            cluster_data = json.load(f)
        cluster_data.update({"database": db, "schema": schema, "table": table})

        provider = get_provider(cfg.llm)
        engine   = InsightsEngine(provider)
        result   = engine.generate(cluster_data)

        out_path = _insights_saved_path(cfg, db, schema, table)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2, default=str)

        _insights_jobs[job_id].update({"status": "done", **result})
    except Exception as exc:
        _insights_jobs[job_id].update({"status": "error", "error": str(exc)})


@app.route("/api/insights", methods=["POST"])
def api_run_insights():
    body   = request.get_json(silent=True) or {}
    db     = body.get("database", "").strip()
    schema = body.get("schema",   "").strip()
    table  = body.get("table",    "").strip()
    if not all([db, schema, table]):
        return jsonify({"error": "database, schema and table are required"}), 400
    job_id = str(uuid.uuid4())
    _insights_jobs[job_id] = {"status": "pending", "error": None}
    threading.Thread(
        target=_run_insights_job, args=(job_id, db, schema, table), daemon=True
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/api/insights/<job_id>")
def api_insights_status(job_id: str):
    job = _insights_jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


@app.route("/api/insights/saved")
def api_insights_saved():
    db     = request.args.get("db",     "").strip().upper()
    schema = request.args.get("schema", "").strip().upper()
    table  = request.args.get("table",  "").strip().upper()
    if not all([db, schema, table]):
        return jsonify({"error": "db, schema, table required"}), 400
    cfg  = _cfg()
    path = _insights_saved_path(cfg, db, schema, table)
    if path.exists():
        with open(path) as f:
            return jsonify(json.load(f))
    return jsonify({"cluster_insights": {}, "global_insights": ""})


# ── Comment generation ────────────────────────────────────────────────────────

def _comment_saved_path(cfg, db: str, schema: str, table: str) -> Path:
    d = _reports_dir(cfg) / "comments"
    d.mkdir(exist_ok=True)
    key = f"{db.upper()}__{schema.upper()}__{table.upper()}"
    return d / f"{key}.json"


def _run_comment_job(job_id: str, db: str, schema: str, table: str) -> None:
    _comment_jobs[job_id]["status"] = "running"
    try:
        cfg = _cfg()
        rp  = _report_path(cfg, db, schema, table)
        if not rp.exists():
            raise FileNotFoundError(
                f"No profile found for {db}.{schema}.{table}. Profile the table first."
            )
        with open(rp) as f:
            profile = json.load(f)
        profile.update({"database": db, "schema": schema, "table": table})

        provider = get_provider(cfg.llm)
        result   = CommentGenerator(provider).generate(profile)

        out = _comment_saved_path(cfg, db, schema, table)
        with open(out, "w") as f:
            json.dump(result, f, indent=2, default=str)

        _comment_jobs[job_id].update({"status": "done", **result})
    except Exception as exc:
        _comment_jobs[job_id].update({"status": "error", "error": str(exc)})


@app.route("/api/comments/generate", methods=["POST"])
def api_comments_generate():
    body   = request.get_json(silent=True) or {}
    db     = body.get("database", "").strip()
    schema = body.get("schema",   "").strip()
    table  = body.get("table",    "").strip()
    if not all([db, schema, table]):
        return jsonify({"error": "database, schema and table are required"}), 400
    job_id = str(uuid.uuid4())
    _comment_jobs[job_id] = {"status": "pending", "error": None}
    threading.Thread(
        target=_run_comment_job, args=(job_id, db, schema, table), daemon=True
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/api/comments/generate/<job_id>")
def api_comments_status(job_id: str):
    job = _comment_jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


@app.route("/api/comments/saved")
def api_comments_saved():
    db     = request.args.get("db",     "").strip().upper()
    schema = request.args.get("schema", "").strip().upper()
    table  = request.args.get("table",  "").strip().upper()
    if not all([db, schema, table]):
        return jsonify({"error": "db, schema, table required"}), 400
    cfg  = _cfg()
    path = _comment_saved_path(cfg, db, schema, table)
    if path.exists():
        with open(path) as f:
            return jsonify(json.load(f))
    return jsonify({"table_comment": None, "column_comments": {}})


@app.route("/api/comments/apply", methods=["POST"])
def api_comments_apply():
    """
    Execute COMMENT ON TABLE / COMMENT ON COLUMN statements against the source.
    For SQLite: comments are not supported by the engine — returns a saved-only message.
    """
    body   = request.get_json(silent=True) or {}
    db     = body.get("database", "").strip()
    schema = body.get("schema",   "").strip()
    table  = body.get("table",    "").strip()
    if not all([db, schema, table]):
        return jsonify({"error": "database, schema and table are required"}), 400

    cfg = _cfg()
    if cfg.platform == "sqlite":
        return jsonify({
            "ok": True,
            "skipped": True,
            "message": "SQLite does not support COMMENT ON syntax. Suggestions are saved locally only.",
        })

    # Load saved suggestions
    path = _comment_saved_path(cfg, db, schema, table)
    if not path.exists():
        return jsonify({"error": "No saved suggestions. Generate comments first."}), 400
    with open(path) as f:
        suggestions = json.load(f)

    platform = _get_platform(cfg)
    tbl_ref  = platform.table_ref(db, schema, table)

    def _esc(s: str) -> str:
        return (s or "").replace("'", "''")

    stmts: list[str] = []
    if suggestions.get("table_comment"):
        stmts.append(f"COMMENT ON TABLE {tbl_ref} IS '{_esc(suggestions['table_comment'])}'")
    for col_name, comment in (suggestions.get("column_comments") or {}).items():
        if comment:
            qcol = platform.quote_col(col_name)
            stmts.append(
                f"COMMENT ON COLUMN {tbl_ref}.{qcol} IS '{_esc(comment)}'"
            )

    applied, failed = [], []
    for stmt in stmts:
        try:
            platform.fetch_all(stmt)
            applied.append(stmt)
        except Exception as exc:
            failed.append({"stmt": stmt[:120], "error": str(exc)})

    return jsonify({
        "ok":      len(failed) == 0,
        "applied": len(applied),
        "failed":  failed,
        "message": f"Applied {len(applied)} of {len(stmts)} COMMENT statements.",
    })


# ── Correlation explanation ────────────────────────────────────────────────────

def _run_correxp_job(job_id: str, db: str, schema: str, table: str) -> None:
    _correxp_jobs[job_id]["status"] = "running"
    try:
        from datetime import datetime as _dt
        cfg = _cfg()

        # Load saved correlation matrix
        corr_path = _corr_saved_path(cfg, db, schema, table)
        if not corr_path.exists():
            raise FileNotFoundError("No saved correlation matrix. Run Correlation first.")
        with open(corr_path) as f:
            corr_data = json.load(f)

        columns = corr_data.get("columns", [])
        matrix  = corr_data.get("matrix",  [])
        n = len(columns)

        if n < 2:
            raise ValueError("Need at least 2 columns to explain correlations.")

        # Extract top-N most significant correlations for the prompt
        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                v = matrix[i][j] if matrix and matrix[i] else None
                if v is not None:
                    pairs.append((abs(v), v, columns[i], columns[j]))
        pairs.sort(reverse=True)
        top    = [p for p in pairs if p[0] >= 0.4][:8]
        low    = [p for p in pairs if p[0] <= 0.05][:4]

        def _pair_line(abs_v, v, a, b):
            direction = "positive" if v > 0 else "negative"
            strength  = "strong" if abs_v >= 0.7 else "moderate" if abs_v >= 0.4 else "weak"
            return f"  {a} ↔ {b}: r={v:+.3f} ({strength} {direction})"

        top_block = "\n".join(_pair_line(*p) for p in top) or "  (no strong correlations)"
        low_block = "\n".join(_pair_line(*p) for p in low) or "  (all columns show some correlation)"

        prompt = f"""\
You are a data analyst explaining a Pearson correlation matrix for a business audience.

Table: {db}.{schema}.{table}
Columns analysed: {', '.join(columns)}

Notable correlations (|r| ≥ 0.4):
{top_block}

Near-zero correlations (|r| ≤ 0.05):
{low_block}

Write a clear, concise explanation (4–6 bullet points) covering:
1. What the strongest positive correlations mean for the business
2. What the strongest negative correlations (if any) reveal
3. Which columns are nearly independent (surprising or expected?)
4. One actionable recommendation based on the correlations

Use plain language — no statistical jargon. Start each bullet with •."""

        provider = get_provider(cfg.llm)
        if provider.provider_id == "disabled" or not provider.is_available():
            # Rule-based explanation
            lines = ["• Correlation analysis results:"]
            if top:
                abs_v, v, a, b = top[0]
                direction = "positively" if v > 0 else "negatively"
                lines.append(f"• Strongest correlation: {a} and {b} (r={v:+.3f}) are {direction} correlated — when one increases, the other tends to {'increase' if v > 0 else 'decrease'}.")
            strong_pos = [(a,b,v) for abs_v,v,a,b in top if v >= 0.7]
            strong_neg = [(a,b,v) for abs_v,v,a,b in top if v <= -0.4]
            if strong_pos:
                lines.append(f"• {len(strong_pos)} strong positive relationship(s) detected. These columns may be driven by the same underlying factor.")
            if strong_neg:
                lines.append(f"• {len(strong_neg)} negative relationship(s) detected. These columns tend to move in opposite directions.")
            if low:
                a, b = low[0][2], low[0][3]
                lines.append(f"• {a} and {b} are nearly independent — they carry different information and should both be retained as features.")
            lines.append("• Consider using correlated feature pairs carefully in ML models — one may be redundant.")
            explanation = "\n".join(lines)
            rule_based = True
        else:
            explanation = provider.generate(prompt, temperature=0.3, max_tokens=600)
            rule_based = False

        result = {
            "explanation":  explanation,
            "rule_based":   rule_based,
            "provider":     provider.provider_id,
            "model":        provider.model_name,
            "generated_at": _dt.now().isoformat(),
            "top_pairs":    [(v, a, b) for _, v, a, b in top[:5]],
        }

        exp_path = corr_path.parent / (corr_path.stem + "_explain.json")
        with open(exp_path, "w") as f:
            json.dump(result, f, indent=2, default=str)

        _correxp_jobs[job_id].update({"status": "done", **result})
    except Exception as exc:
        _correxp_jobs[job_id].update({"status": "error", "error": str(exc)})


@app.route("/api/correlation/explain", methods=["POST"])
def api_correxp_start():
    body   = request.get_json(silent=True) or {}
    db     = body.get("database", "").strip()
    schema = body.get("schema",   "").strip()
    table  = body.get("table",    "").strip()
    if not all([db, schema, table]):
        return jsonify({"error": "database, schema and table are required"}), 400
    job_id = str(uuid.uuid4())
    _correxp_jobs[job_id] = {"status": "pending", "error": None}
    threading.Thread(
        target=_run_correxp_job, args=(job_id, db, schema, table), daemon=True
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/api/correlation/explain/<job_id>")
def api_correxp_status(job_id: str):
    job = _correxp_jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


@app.route("/api/correlation/explain/saved")
def api_correxp_saved():
    db     = request.args.get("db",     "").strip().upper()
    schema = request.args.get("schema", "").strip().upper()
    table  = request.args.get("table",  "").strip().upper()
    if not all([db, schema, table]):
        return jsonify({"error": "db, schema, table required"}), 400
    cfg  = _cfg()
    path = _corr_saved_path(cfg, db, schema, table)
    exp  = path.parent / (path.stem + "_explain.json")
    if exp.exists():
        with open(exp) as f:
            return jsonify(json.load(f))
    return jsonify({"explanation": None})


# ── Feature importance ────────────────────────────────────────────────────────

def _run_featimp_job(
    job_id: str, db: str, schema: str, table: str,
    target_col: str, feature_ids: list, sample_size: int,
) -> None:
    _featimp_jobs[job_id]["status"] = "running"
    try:
        cfg    = _cfg()
        result = FeatureSelector(
            _get_platform(cfg), _reports_dir(cfg)
        ).analyze(
            db, schema, table, target_col,
            feature_ids=feature_ids or None,
            sample_size=sample_size,
        )
        _featimp_jobs[job_id].update({"status": "done", **result})
    except Exception as exc:
        _featimp_jobs[job_id].update({"status": "error", "error": str(exc)})


@app.route("/api/clustering/feature-importance", methods=["POST"])
def api_feature_importance():
    body        = request.get_json(silent=True) or {}
    db          = body.get("database",    "").strip()
    schema      = body.get("schema",      "").strip()
    table       = body.get("table",       "").strip()
    target_col  = body.get("target_col",  "").strip()
    feature_ids = body.get("feature_ids", [])    # engineered IDs from frontend
    sample_size = int(body.get("sample_size", 8_000))

    if not all([db, schema, table, target_col]):
        return jsonify({"error": "database, schema, table and target_col are required"}), 400

    job_id = str(uuid.uuid4())
    _featimp_jobs[job_id] = {"status": "pending", "error": None}
    threading.Thread(
        target=_run_featimp_job,
        args=(job_id, db, schema, table, target_col, feature_ids, sample_size),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id})


@app.route("/api/clustering/feature-importance/<job_id>")
def api_feature_importance_status(job_id: str):
    job = _featimp_jobs.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


# ── Column colour tagging ─────────────────────────────────────────────────────

def _colors_path(cfg, db: str, schema: str, table: str) -> Path:
    d = _reports_dir(cfg) / "colors"
    d.mkdir(exist_ok=True)
    key = f"{db.upper()}__{schema.upper()}__{table.upper()}"
    return d / f"{key}.json"


@app.route("/api/columns/colors")
def api_columns_colors_get():
    db     = request.args.get("db",     "").strip().upper()
    schema = request.args.get("schema", "").strip().upper()
    table  = request.args.get("table",  "").strip().upper()
    if not all([db, schema, table]):
        return jsonify({"error": "db, schema, table required"}), 400
    p = _colors_path(_cfg(), db, schema, table)
    if p.exists():
        with open(p) as f:
            return jsonify(json.load(f))
    return jsonify({})


@app.route("/api/columns/colors", methods=["POST"])
def api_columns_colors_set():
    body   = request.get_json(silent=True) or {}
    db     = body.get("database", "").strip()
    schema = body.get("schema",   "").strip()
    table  = body.get("table",    "").strip()
    colors = body.get("colors",   {})
    if not all([db, schema, table]):
        return jsonify({"error": "database, schema, table required"}), 400
    cfg = _cfg()
    p   = _colors_path(cfg, db.upper(), schema.upper(), table.upper())
    with open(p, "w") as f:
        json.dump(colors, f, indent=2)
    return jsonify({"ok": True, "saved": len(colors)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
