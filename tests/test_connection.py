"""Tests for connection.py — SnowflakeConnection (mocked connector)."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch, call

import pytest

from config import SnowflakeConfig
from connection import SnowflakeConnection


@pytest.fixture()
def sf_config():
    return SnowflakeConfig(
        account="acct", user="user", password="pass",
        warehouse="WH", database="DB", schema="SC",
    )


@pytest.fixture()
def mock_snowflake():
    """Patch snowflake.connector.connect and yield (mock_connect, mock_cursor)."""
    mock_cur  = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur
    with patch("connection.snowflake.connector.connect", return_value=mock_conn) as mc:
        yield mc, mock_conn, mock_cur


class TestConnect:
    def test_passes_credentials(self, sf_config, mock_snowflake):
        mock_connect, _, _ = mock_snowflake
        conn = SnowflakeConnection(sf_config)
        conn.fetch_one("SELECT 1")
        kwargs = mock_connect.call_args[1]
        assert kwargs["account"] == "acct"
        assert kwargs["user"] == "user"

    def test_no_role_when_none(self, sf_config, mock_snowflake):
        mock_connect, _, _ = mock_snowflake
        conn = SnowflakeConnection(sf_config)
        conn.fetch_one("SELECT 1")
        kwargs = mock_connect.call_args[1]
        assert "role" not in kwargs

    def test_role_included_when_set(self, sf_config, mock_snowflake):
        sf_config.role = "SYSADMIN"
        mock_connect, _, _ = mock_snowflake
        conn = SnowflakeConnection(sf_config)
        conn.fetch_one("SELECT 1")
        kwargs = mock_connect.call_args[1]
        assert kwargs["role"] == "SYSADMIN"


class TestFetchOne:
    def test_returns_first_row(self, sf_config, mock_snowflake):
        _, mock_conn, mock_cur = mock_snowflake
        mock_cur.fetchone.return_value = (42,)
        conn = SnowflakeConnection(sf_config)
        result = conn.fetch_one("SELECT 42")
        assert result == (42,)

    def test_executes_query(self, sf_config, mock_snowflake):
        _, _, mock_cur = mock_snowflake
        mock_cur.fetchone.return_value = None
        conn = SnowflakeConnection(sf_config)
        conn.fetch_one("SELECT 1")
        mock_cur.execute.assert_called_once_with("SELECT 1", None)

    def test_closes_cursor_after_fetch(self, sf_config, mock_snowflake):
        _, mock_conn, mock_cur = mock_snowflake
        mock_cur.fetchone.return_value = (1,)
        conn = SnowflakeConnection(sf_config)
        conn.fetch_one("SELECT 1")
        mock_cur.close.assert_called_once()

    def test_closes_connection_after_fetch(self, sf_config, mock_snowflake):
        _, mock_conn, mock_cur = mock_snowflake
        mock_cur.fetchone.return_value = (1,)
        conn = SnowflakeConnection(sf_config)
        conn.fetch_one("SELECT 1")
        mock_conn.close.assert_called_once()


class TestFetchAll:
    def test_returns_all_rows(self, sf_config, mock_snowflake):
        _, _, mock_cur = mock_snowflake
        mock_cur.fetchall.return_value = [(1,), (2,), (3,)]
        conn = SnowflakeConnection(sf_config)
        result = conn.fetch_all("SELECT id FROM t")
        assert result == [(1,), (2,), (3,)]


class TestCursorContextManager:
    def test_cursor_closed_on_success(self, sf_config, mock_snowflake):
        _, mock_conn, mock_cur = mock_snowflake
        conn = SnowflakeConnection(sf_config)
        with conn.cursor() as cur:
            pass
        mock_cur.close.assert_called_once()
        mock_conn.close.assert_called_once()

    def test_cursor_closed_on_exception(self, sf_config, mock_snowflake):
        _, mock_conn, mock_cur = mock_snowflake
        conn = SnowflakeConnection(sf_config)
        try:
            with conn.cursor() as cur:
                raise ValueError("test error")
        except ValueError:
            pass
        mock_cur.close.assert_called_once()
        mock_conn.close.assert_called_once()
