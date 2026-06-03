"""Flask web app — Snowflake Profiler config editor + HTML report viewer."""

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
            "status": "done",
            "database": db,
            "schema": schema,
            "table": table,
            "row_count": profile.row_count,
            "column_count": profile.column_count,
            "alert_count": len(am.alerts),
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
    return render_template("profile.html", cfg=_cfg())


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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
