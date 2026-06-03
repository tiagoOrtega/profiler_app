"""Tests for config.py — all dataclasses and load_config branches."""

import os
import tempfile

import pytest
import yaml

from config import (
    AlertConfig,
    DatabricksConfig,
    ProfilerConfig,
    SQLiteConfig,
    SnowflakeConfig,
    load_config,
)


# ── Dataclass construction ────────────────────────────────────────────────────

class TestSnowflakeConfig:
    def test_required_fields(self):
        cfg = SnowflakeConfig(
            account="acc", user="u", password="p",
            warehouse="wh", database="db", schema="sc",
        )
        assert cfg.account == "acc"
        assert cfg.role is None

    def test_with_role(self):
        cfg = SnowflakeConfig(
            account="a", user="u", password="p",
            warehouse="w", database="d", schema="s", role="SYSADMIN",
        )
        assert cfg.role == "SYSADMIN"


class TestSQLiteConfig:
    def test_default_path(self):
        assert SQLiteConfig().database_path == "./sample.db"

    def test_custom_path(self):
        cfg = SQLiteConfig(database_path="/tmp/mydb.db")
        assert cfg.database_path == "/tmp/mydb.db"


class TestDatabricksConfig:
    def test_defaults(self):
        cfg = DatabricksConfig()
        assert cfg.host == ""
        assert cfg.catalog == "main"
        assert cfg.schema == "default"

    def test_all_fields(self):
        cfg = DatabricksConfig(
            host="host", http_path="/path", token="tok",
            catalog="hive", schema="default",
        )
        assert cfg.token == "tok"


class TestAlertConfig:
    def test_defaults(self):
        cfg = AlertConfig()
        assert cfg.null_rate_threshold == 0.10
        assert cfg.slack_webhook_url is None

    def test_custom(self):
        cfg = AlertConfig(slack_webhook_url="https://hooks.slack.com/x",
                          log_file="", null_rate_threshold=0.20)
        assert cfg.null_rate_threshold == 0.20


class TestProfilerConfig:
    def test_defaults(self):
        sf = SnowflakeConfig("a","u","p","w","d","s")
        al = AlertConfig()
        cfg = ProfilerConfig(snowflake=sf, alerts=al)
        assert cfg.platform == "snowflake"
        assert cfg.output_dir == "reports"
        assert isinstance(cfg.sqlite, SQLiteConfig)
        assert isinstance(cfg.databricks, DatabricksConfig)


# ── load_config ───────────────────────────────────────────────────────────────

class TestLoadConfig:
    def test_no_file_returns_defaults(self):
        cfg = load_config("/nonexistent/path.yaml")
        assert cfg.platform == "snowflake"
        assert cfg.output_dir == "reports"
        assert cfg.history_file == "profiling_history.json"
        assert cfg.sqlite.database_path == "./sample.db"
        assert cfg.databricks.catalog == "main"

    def test_snowflake_section(self):
        data = {
            "snowflake": {
                "account": "my_acct", "user": "me",
                "password": "secret", "warehouse": "WH",
                "database": "DB", "schema": "SC",
            },
        }
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            path = f.name
        try:
            cfg = load_config(path)
            assert cfg.snowflake.account == "my_acct"
            assert cfg.snowflake.warehouse == "WH"
            assert cfg.snowflake.role is None
        finally:
            os.unlink(path)

    def test_role_in_yaml(self):
        data = {"snowflake": {"account":"a","user":"u","password":"p",
                              "warehouse":"w","database":"d","schema":"s",
                              "role": "SYSADMIN"}}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f); path = f.name
        try:
            cfg = load_config(path)
            assert cfg.snowflake.role == "SYSADMIN"
        finally:
            os.unlink(path)

    def test_platform_field(self):
        data = {"platform": "sqlite", "sqlite": {"database_path": "/tmp/x.db"}}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f); path = f.name
        try:
            cfg = load_config(path)
            assert cfg.platform == "sqlite"
            assert cfg.sqlite.database_path == "/tmp/x.db"
        finally:
            os.unlink(path)

    def test_databricks_section(self):
        data = {"platform": "databricks",
                "databricks": {"host":"h","http_path":"/p","token":"t",
                               "catalog":"cat","schema":"sch"}}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f); path = f.name
        try:
            cfg = load_config(path)
            assert cfg.databricks.host == "h"
            assert cfg.databricks.catalog == "cat"
        finally:
            os.unlink(path)

    def test_alert_thresholds(self):
        data = {"alerts": {"null_rate_threshold": 0.05,
                           "row_count_change_threshold": 0.10,
                           "std_dev_change_threshold": 0.25,
                           "log_file": ""}}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f); path = f.name
        try:
            cfg = load_config(path)
            assert cfg.alerts.null_rate_threshold == 0.05
            assert cfg.alerts.std_dev_change_threshold == 0.25
        finally:
            os.unlink(path)

    def test_env_var_overrides_yaml(self, monkeypatch):
        data = {"snowflake": {"account": "yaml_acct", "user": "yaml_user",
                              "password": "p", "warehouse": "w",
                              "database": "d", "schema": "s"}}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f); path = f.name
        monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "env_acct")
        try:
            cfg = load_config(path)
            assert cfg.snowflake.account == "env_acct"   # env wins
        finally:
            os.unlink(path)
            monkeypatch.delenv("SNOWFLAKE_ACCOUNT", raising=False)

    def test_slack_env_override(self, monkeypatch):
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
        cfg = load_config("/nonexistent")
        assert cfg.alerts.slack_webhook_url == "https://hooks.slack.com/test"
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    def test_empty_slack_becomes_none(self):
        data = {"alerts": {"slack_webhook_url": ""}}
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f); path = f.name
        try:
            cfg = load_config(path)
            assert cfg.alerts.slack_webhook_url is None
        finally:
            os.unlink(path)

    def test_databricks_env_overrides(self, monkeypatch):
        monkeypatch.setenv("DATABRICKS_HOST", "env_host")
        monkeypatch.setenv("DATABRICKS_TOKEN", "env_token")
        cfg = load_config("/nonexistent")
        assert cfg.databricks.host == "env_host"
        assert cfg.databricks.token == "env_token"
        for k in ("DATABRICKS_HOST", "DATABRICKS_TOKEN"):
            monkeypatch.delenv(k, raising=False)
