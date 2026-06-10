"""
DataLens — Snowpark session factory.

Returns an active session regardless of execution environment:
  - Streamlit in Snowflake (SiS): uses get_active_session()
  - Local Streamlit dev:          uses st.connection("snowflake") → .streamlit/secrets.toml
  - Headless / CLI:               builds from SNOWFLAKE_* env vars or config.yaml
"""

from __future__ import annotations


def get_session():
    """Return a Snowpark Session. Works in SiS, local Streamlit, and CLI."""

    # ── 1. Streamlit in Snowflake (SiS) ───────────────────────────────────────
    try:
        from snowflake.snowpark.context import get_active_session
        return get_active_session()
    except Exception:
        pass

    # ── 2. Local Streamlit via .streamlit/secrets.toml ────────────────────────
    try:
        import streamlit as st
        conn = st.connection("snowflake", type="snowflake")
        return conn.session()
    except Exception:
        pass

    # ── 3. Environment variables ──────────────────────────────────────────────
    import os
    from snowflake.snowpark import Session

    env = {
        "account":   os.getenv("SNOWFLAKE_ACCOUNT"),
        "user":      os.getenv("SNOWFLAKE_USER"),
        "password":  os.getenv("SNOWFLAKE_PASSWORD"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
        "database":  os.getenv("SNOWFLAKE_DATABASE"),
        "schema":    os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC"),
        "role":      os.getenv("SNOWFLAKE_ROLE"),
    }
    env = {k: v for k, v in env.items() if v}
    if env.get("account") and env.get("user") and env.get("password"):
        return Session.builder.configs(env).create()

    # ── 4. config.yaml fallback ────────────────────────────────────────────────
    try:
        import yaml
        from pathlib import Path
        from snowflake.snowpark import Session

        cfg_path = Path(__file__).parent / "config.yaml"
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}

        sf = cfg.get("snowflake", {})
        params = {
            "account":   sf.get("account"),
            "user":      sf.get("user"),
            "password":  sf.get("password"),
            "warehouse": sf.get("warehouse"),
            "database":  sf.get("database"),
            "schema":    sf.get("schema", "PUBLIC"),
            "role":      sf.get("role"),
        }
        params = {k: v for k, v in params.items() if v}
        if params.get("account") and params.get("user"):
            return Session.builder.configs(params).create()
    except Exception:
        pass

    raise RuntimeError(
        "Could not create a Snowflake session.\n\n"
        "For local development, create .streamlit/secrets.toml:\n\n"
        "  [connections.snowflake]\n"
        "  account   = 'orgname-accountname'\n"
        "  user      = 'your_user'\n"
        "  password  = 'your_password'\n"
        "  warehouse = 'COMPUTE_WH'\n"
        "  database  = 'SAMPLE_DW'\n"
        "  role      = 'SYSADMIN'\n\n"
        "Then run:  streamlit run streamlit_app.py"
    )
