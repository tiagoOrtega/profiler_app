"""Alert system: generates alerts from profile results, logs them, and posts to Slack."""

import json
import logging
import logging.handlers
from dataclasses import dataclass
from typing import List, Optional

import requests

from config import AlertConfig
from profiling import TableProfile


@dataclass
class Alert:
    level: str          # "WARNING" | "CRITICAL"
    table: str
    column: Optional[str]
    metric: str
    message: str
    value: float
    threshold: float


class AlertManager:
    def __init__(self, config: AlertConfig):
        self.config = config
        self.alerts: List[Alert] = []
        self._logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger("data_profiler.alerts")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()

        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        if self.config.log_file:
            fh = logging.FileHandler(self.config.log_file, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            logger.addHandler(fh)

        return logger

    def _add(self, alert: Alert) -> None:
        self.alerts.append(alert)
        loc = f"{alert.table}" + (f".{alert.column}" if alert.column else "")
        msg = f"{loc} | {alert.metric}: {alert.message}"
        if alert.level == "CRITICAL":
            self._logger.error(msg)
        else:
            self._logger.warning(msg)

    def evaluate_table_profile(self, profile: TableProfile, history: Optional[dict]) -> None:
        """Raise alerts for a profiled table, optionally comparing against historical run."""

        # Row count drift
        if history and history.get("row_count") is not None:
            prev = history["row_count"]
            if prev > 0:
                change = abs(profile.row_count - prev) / prev
                if change > self.config.row_count_change_threshold:
                    self._add(Alert(
                        level="WARNING",
                        table=profile.table,
                        column=None,
                        metric="row_count_change",
                        message=(
                            f"Row count changed {change:.1%} "
                            f"(prev {prev:,} → now {profile.row_count:,})"
                        ),
                        value=change,
                        threshold=self.config.row_count_change_threshold,
                    ))

        for col in profile.columns:
            # High null rate
            if col.null_rate > self.config.null_rate_threshold:
                level = "CRITICAL" if col.null_rate > 0.50 else "WARNING"
                self._add(Alert(
                    level=level,
                    table=profile.table,
                    column=col.name,
                    metric="null_rate",
                    message=(
                        f"Null rate {col.null_rate:.1%} exceeds threshold "
                        f"{self.config.null_rate_threshold:.1%} "
                        f"({col.null_count:,} of {col.row_count:,} rows)"
                    ),
                    value=col.null_rate,
                    threshold=self.config.null_rate_threshold,
                ))

            # Std dev drift vs previous run
            if col.std_dev is not None and history:
                prev_std = (
                    history.get("columns", {})
                    .get(col.name, {})
                    .get("std_dev")
                )
                if prev_std and prev_std > 0:
                    drift = abs(col.std_dev - prev_std) / prev_std
                    if drift > self.config.std_dev_change_threshold:
                        self._add(Alert(
                            level="WARNING",
                            table=profile.table,
                            column=col.name,
                            metric="std_dev_drift",
                            message=(
                                f"Std dev shifted {drift:.1%} "
                                f"(prev {prev_std:.4g} → now {col.std_dev:.4g})"
                            ),
                            value=drift,
                            threshold=self.config.std_dev_change_threshold,
                        ))

            # Variance drift vs previous run
            if col.variance is not None and history:
                prev_var = (
                    history.get("columns", {})
                    .get(col.name, {})
                    .get("variance")
                )
                if prev_var and prev_var > 0:
                    drift = abs(col.variance - prev_var) / prev_var
                    if drift > self.config.std_dev_change_threshold:
                        self._add(Alert(
                            level="WARNING",
                            table=profile.table,
                            column=col.name,
                            metric="variance_drift",
                            message=(
                                f"Variance shifted {drift:.1%} "
                                f"(prev {prev_var:.4g} → now {col.variance:.4g})"
                            ),
                            value=drift,
                            threshold=self.config.std_dev_change_threshold,
                        ))

    def send_slack(self, run_summary: str) -> None:
        if not self.config.slack_webhook_url:
            return

        critical = [a for a in self.alerts if a.level == "CRITICAL"]
        warnings = [a for a in self.alerts if a.level == "WARNING"]

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": ":bar_chart: Data Quality Alerts"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": run_summary},
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f":red_circle: {len(critical)} critical  "
                            f":warning: {len(warnings)} warnings"
                        ),
                    }
                ],
            },
            {"type": "divider"},
        ]

        for alert in self.alerts:
            emoji = ":red_circle:" if alert.level == "CRITICAL" else ":warning:"
            col_part = f" / `{alert.column}`" if alert.column else ""
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji} *{alert.level}* — `{alert.table}`{col_part}\n"
                        f"*{alert.metric}*: {alert.message}"
                    ),
                },
            })

        try:
            resp = requests.post(
                self.config.slack_webhook_url,
                json={"blocks": blocks},
                timeout=10,
            )
            resp.raise_for_status()
            self._logger.info(f"Slack notification sent ({len(self.alerts)} alerts)")
        except Exception as exc:
            self._logger.error(f"Failed to send Slack alert: {exc}")
