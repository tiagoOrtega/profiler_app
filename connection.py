"""Snowflake connection wrapper."""

from contextlib import contextmanager
from typing import Iterator

import snowflake.connector

from config import SnowflakeConfig


class SnowflakeConnection:
    def __init__(self, config: SnowflakeConfig):
        self.config = config

    def _connect(self) -> snowflake.connector.SnowflakeConnection:
        params = {
            "account": self.config.account,
            "user": self.config.user,
            "password": self.config.password,
            "warehouse": self.config.warehouse,
            "database": self.config.database,
            "schema": self.config.schema,
            "client_session_keep_alive": False,
        }
        if self.config.role:
            params["role"] = self.config.role
        return snowflake.connector.connect(**params)

    @contextmanager
    def cursor(self) -> Iterator[snowflake.connector.cursor.SnowflakeCursor]:
        conn = self._connect()
        cur = conn.cursor()
        try:
            yield cur
        finally:
            cur.close()
            conn.close()

    def fetch_all(self, query: str, params=None) -> list:
        with self.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()

    def fetch_one(self, query: str, params=None):
        with self.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()
