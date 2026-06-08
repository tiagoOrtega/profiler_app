"""Configuration — loaded from config.yaml with env-var overrides."""

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass
class SnowflakeConfig:
    account:   str
    user:      str
    password:  str
    warehouse: str
    database:  str
    schema:    str
    role:      Optional[str] = None


@dataclass
class SQLiteConfig:
    database_path: str = "./sample.db"


@dataclass
class DatabricksConfig:
    host:      str = ""
    http_path: str = ""
    token:     str = ""
    catalog:   str = "main"
    schema:    str = "default"


@dataclass
class AlertConfig:
    slack_webhook_url:         Optional[str] = None
    log_file:                  str   = "profiler_alerts.log"
    null_rate_threshold:       float = 0.10
    row_count_change_threshold: float = 0.20
    std_dev_change_threshold:  float = 0.30


@dataclass
class LLMConfig:
    provider:    str   = "disabled"              # ollama | openai | anthropic | disabled
    model:       str   = "llama3.2"
    base_url:    str   = "http://localhost:11434" # Ollama or OpenAI-compatible URL
    api_key:     str   = ""                      # cloud providers (prefer env vars)
    temperature: float = 0.3
    max_tokens:  int   = 512


@dataclass
class ProfilerConfig:
    snowflake:   SnowflakeConfig
    alerts:      AlertConfig
    source_name: str        = "My Source"   # human-readable label shown in the UI
    platform:    str        = "snowflake"   # snowflake | sqlite | databricks
    sqlite:      SQLiteConfig     = field(default_factory=SQLiteConfig)
    databricks:  DatabricksConfig = field(default_factory=DatabricksConfig)
    llm:         LLMConfig        = field(default_factory=LLMConfig)
    output_dir:   str = "reports"
    history_file: str = "profiling_history.json"


def load_config(config_path: str = "config.yaml") -> ProfilerConfig:
    cfg: dict = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}

    sf  = cfg.get("snowflake",   {})
    al  = cfg.get("alerts",      {})
    sl  = cfg.get("sqlite",      {})
    db  = cfg.get("databricks",  {})

    snowflake = SnowflakeConfig(
        account=os.getenv("SNOWFLAKE_ACCOUNT",   sf.get("account",   "")),
        user=os.getenv("SNOWFLAKE_USER",          sf.get("user",      "")),
        password=os.getenv("SNOWFLAKE_PASSWORD",  sf.get("password",  "")),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE",sf.get("warehouse", "")),
        database=os.getenv("SNOWFLAKE_DATABASE",  sf.get("database",  "")),
        schema=os.getenv("SNOWFLAKE_SCHEMA",      sf.get("schema",    "")),
        role=os.getenv("SNOWFLAKE_ROLE",          sf.get("role")) or None,
    )

    alerts = AlertConfig(
        slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL", al.get("slack_webhook_url") or "") or None,
        log_file=al.get("log_file", "profiler_alerts.log"),
        null_rate_threshold=float(al.get("null_rate_threshold", 0.10)),
        row_count_change_threshold=float(al.get("row_count_change_threshold", 0.20)),
        std_dev_change_threshold=float(al.get("std_dev_change_threshold", 0.30)),
    )

    sqlite = SQLiteConfig(
        database_path=sl.get("database_path", "./sample.db"),
    )

    databricks = DatabricksConfig(
        host=os.getenv("DATABRICKS_HOST",      db.get("host",      "")),
        http_path=os.getenv("DATABRICKS_HTTP_PATH", db.get("http_path", "")),
        token=os.getenv("DATABRICKS_TOKEN",    db.get("token",     "")),
        catalog=os.getenv("DATABRICKS_CATALOG",db.get("catalog",   "main")),
        schema=os.getenv("DATABRICKS_SCHEMA",  db.get("schema",    "default")),
    )

    lm = cfg.get("llm", {})
    llm = LLMConfig(
        provider=os.getenv("LLM_PROVIDER",     lm.get("provider",    "disabled")),
        model=os.getenv("LLM_MODEL",           lm.get("model",       "llama3.2")),
        base_url=os.getenv("LLM_BASE_URL",     lm.get("base_url",    "http://localhost:11434")),
        api_key=os.getenv("LLM_API_KEY",       lm.get("api_key",     "")) or
                os.getenv("OPENAI_API_KEY",    "") or
                os.getenv("ANTHROPIC_API_KEY", ""),
        temperature=float(lm.get("temperature", 0.3)),
        max_tokens=int(lm.get("max_tokens", 512)),
    )

    return ProfilerConfig(
        snowflake=snowflake,
        alerts=alerts,
        source_name=cfg.get("source_name", "My Source"),
        platform=cfg.get("platform", "snowflake"),
        sqlite=sqlite,
        databricks=databricks,
        llm=llm,
        output_dir=cfg.get("output_dir", "reports"),
        history_file=cfg.get("history_file", "profiling_history.json"),
    )
