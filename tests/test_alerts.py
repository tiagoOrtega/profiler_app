"""Tests for alerts.py — AlertManager evaluation and Slack integration."""

import logging
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import requests

from alerts import Alert, AlertManager
from config import AlertConfig
from profiling import ColumnProfile, TableProfile


def _cfg(**kw) -> AlertConfig:
    defaults = dict(
        log_file="",           # empty → no FileHandler (post-refactor guard)
        null_rate_threshold=0.10,
        row_count_change_threshold=0.20,
        std_dev_change_threshold=0.30,
        slack_webhook_url=None,
    )
    defaults.update(kw)
    return AlertConfig(**defaults)


def _col(name="c", null_rate=0.0, std_dev=None, variance=None, null_count=0, row_count=100):
    return ColumnProfile(
        name=name, data_type="NUMBER",
        row_count=row_count, null_count=null_count,
        null_rate=null_rate, distinct_count=10, uniqueness_rate=0.1,
        std_dev=std_dev, variance=variance,
    )


def _profile(row_count=100, columns=None) -> TableProfile:
    return TableProfile(
        database="DB", schema="SC", table="T",
        row_count=row_count, column_count=1,
        columns=columns or [_col()],
    )


# ── Logger setup ──────────────────────────────────────────────────────────────

class TestSetupLogger:
    def test_no_file_handler_when_log_file_empty(self):
        am = AlertManager(_cfg(log_file=""))
        file_handlers = [h for h in am._logger.handlers
                         if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 0

    def test_file_handler_created_when_log_file_set(self):
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            path = f.name
        try:
            am = AlertManager(_cfg(log_file=path))
            file_handlers = [h for h in am._logger.handlers
                             if isinstance(h, logging.FileHandler)]
            assert len(file_handlers) == 1
        finally:
            # Close handlers before unlinking
            for h in am._logger.handlers:
                h.close()
            os.unlink(path)


# ── _add ─────────────────────────────────────────────────────────────────────

class TestAdd:
    def test_adds_alert_to_list(self):
        am = AlertManager(_cfg())
        am._add(Alert("WARNING","T","c","metric","msg",0.5,0.3))
        assert len(am.alerts) == 1

    def test_critical_uses_error_log(self):
        am = AlertManager(_cfg())
        with patch.object(am._logger, "error") as mock_err:
            am._add(Alert("CRITICAL","T","c","metric","msg",0.9,0.5))
        mock_err.assert_called_once()

    def test_warning_uses_warning_log(self):
        am = AlertManager(_cfg())
        with patch.object(am._logger, "warning") as mock_warn:
            am._add(Alert("WARNING","T",None,"metric","msg",0.3,0.2))
        mock_warn.assert_called_once()

    def test_no_column_in_location(self):
        am = AlertManager(_cfg())
        with patch.object(am._logger, "warning") as mock_warn:
            am._add(Alert("WARNING","T",None,"row_count_change","msg",0.25,0.20))
        call_args = mock_warn.call_args[0][0]
        assert "None" not in call_args


# ── evaluate_table_profile ────────────────────────────────────────────────────

class TestEvaluateNullRate:
    def test_no_alert_below_threshold(self):
        am = AlertManager(_cfg(null_rate_threshold=0.10))
        am.evaluate_table_profile(_profile(columns=[_col(null_rate=0.05)]), None)
        assert len(am.alerts) == 0

    def test_warning_above_threshold(self):
        am = AlertManager(_cfg(null_rate_threshold=0.10))
        am.evaluate_table_profile(_profile(columns=[_col(null_rate=0.30)]), None)
        assert len(am.alerts) == 1
        assert am.alerts[0].level == "WARNING"
        assert am.alerts[0].metric == "null_rate"

    def test_critical_above_50pct(self):
        am = AlertManager(_cfg(null_rate_threshold=0.10))
        am.evaluate_table_profile(_profile(columns=[_col(null_rate=0.60)]), None)
        assert am.alerts[0].level == "CRITICAL"

    def test_exactly_at_threshold_no_alert(self):
        am = AlertManager(_cfg(null_rate_threshold=0.10))
        am.evaluate_table_profile(_profile(columns=[_col(null_rate=0.10)]), None)
        assert len(am.alerts) == 0


class TestEvaluateRowCount:
    def test_no_alert_without_history(self):
        am = AlertManager(_cfg())
        am.evaluate_table_profile(_profile(row_count=100), None)
        assert len(am.alerts) == 0

    def test_no_alert_small_change(self):
        am = AlertManager(_cfg(row_count_change_threshold=0.20))
        am.evaluate_table_profile(_profile(row_count=110), {"row_count": 100})
        assert len(am.alerts) == 0

    def test_alert_on_large_drop(self):
        am = AlertManager(_cfg(row_count_change_threshold=0.20))
        am.evaluate_table_profile(_profile(row_count=50), {"row_count": 100})
        assert any(a.metric == "row_count_change" for a in am.alerts)

    def test_alert_on_large_increase(self):
        am = AlertManager(_cfg(row_count_change_threshold=0.20))
        am.evaluate_table_profile(_profile(row_count=200), {"row_count": 100})
        assert any(a.metric == "row_count_change" for a in am.alerts)

    def test_no_alert_when_prev_row_count_zero(self):
        am = AlertManager(_cfg())
        am.evaluate_table_profile(_profile(row_count=100), {"row_count": 0})
        assert len(am.alerts) == 0

    def test_no_alert_when_prev_row_count_none(self):
        am = AlertManager(_cfg())
        am.evaluate_table_profile(_profile(row_count=100), {"row_count": None})
        assert len(am.alerts) == 0


class TestEvaluateStdDevDrift:
    def test_no_drift_without_history(self):
        am = AlertManager(_cfg())
        am.evaluate_table_profile(
            _profile(columns=[_col(std_dev=10.0)]), None
        )
        assert len(am.alerts) == 0

    def test_alert_on_significant_drift(self):
        history = {"columns": {"c": {"std_dev": 10.0}}}
        am = AlertManager(_cfg(std_dev_change_threshold=0.30))
        am.evaluate_table_profile(
            _profile(columns=[_col(std_dev=20.0)]),  # +100% drift
            history
        )
        assert any(a.metric == "std_dev_drift" for a in am.alerts)

    def test_no_alert_within_threshold(self):
        history = {"columns": {"c": {"std_dev": 10.0}}}
        am = AlertManager(_cfg(std_dev_change_threshold=0.30))
        am.evaluate_table_profile(
            _profile(columns=[_col(std_dev=11.0)]),  # +10% drift
            history
        )
        assert not any(a.metric == "std_dev_drift" for a in am.alerts)

    def test_no_alert_when_prev_std_zero(self):
        history = {"columns": {"c": {"std_dev": 0.0}}}
        am = AlertManager(_cfg())
        am.evaluate_table_profile(
            _profile(columns=[_col(std_dev=5.0)]),
            history
        )
        assert not any(a.metric == "std_dev_drift" for a in am.alerts)


class TestEvaluateVarianceDrift:
    def test_alert_on_variance_drift(self):
        history = {"columns": {"c": {"variance": 100.0, "std_dev": 10.0}}}
        am = AlertManager(_cfg(std_dev_change_threshold=0.30))
        am.evaluate_table_profile(
            _profile(columns=[_col(variance=400.0, std_dev=20.0)]),
            history
        )
        assert any(a.metric == "variance_drift" for a in am.alerts)

    def test_no_variance_alert_when_none(self):
        history = {"columns": {"c": {"variance": None}}}
        am = AlertManager(_cfg())
        am.evaluate_table_profile(
            _profile(columns=[_col(variance=None)]),
            history
        )
        assert not any(a.metric == "variance_drift" for a in am.alerts)


# ── send_slack ────────────────────────────────────────────────────────────────

class TestSendSlack:
    def test_no_op_when_no_webhook(self):
        am = AlertManager(_cfg(slack_webhook_url=None))
        with patch("requests.post") as mock_post:
            am.send_slack("summary")
        mock_post.assert_not_called()

    def test_posts_to_webhook(self):
        am = AlertManager(_cfg(slack_webhook_url="https://hooks.slack.com/x"))
        am.alerts.append(Alert("WARNING","T","c","metric","msg",0.5,0.3))
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=mock_resp) as mock_post:
            am.send_slack("test run")
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs[0][0] == "https://hooks.slack.com/x"

    def test_handles_request_exception(self):
        am = AlertManager(_cfg(slack_webhook_url="https://hooks.slack.com/x"))
        with patch("requests.post", side_effect=Exception("network error")):
            am.send_slack("summary")   # must not raise

    def test_handles_http_error(self):
        am = AlertManager(_cfg(slack_webhook_url="https://hooks.slack.com/x"))
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("403")
        with patch("requests.post", return_value=mock_resp):
            am.send_slack("summary")   # must not raise

    def test_blocks_include_all_alerts(self):
        am = AlertManager(_cfg(slack_webhook_url="https://hooks.slack.com/x"))
        am.alerts.append(Alert("WARNING","T","c","m","msg1",0.5,0.3))
        am.alerts.append(Alert("CRITICAL","T",None,"m","msg2",0.9,0.5))
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        with patch("requests.post", return_value=mock_resp) as mp:
            am.send_slack("summary")
        payload = mp.call_args[1]["json"]
        # Header + summary + context + divider + 2 alert blocks = 6 blocks
        assert len(payload["blocks"]) == 6
