"""Alerting engine — evaluates alert rules against probe results."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx
import structlog

from pingwatch.config import AlertConfig, AlertNotify
from pingwatch.probes import ProbeResult
from pingwatch.storage import Storage

logger = structlog.get_logger()


class AlertState(str, Enum):
    """Alert firing state."""

    CLEAR = "clear"
    FIRING = "firing"


@dataclass
class ActiveAlert:
    """Tracks an alert that is currently firing."""

    name: str
    target: str
    condition: str
    values: dict[str, float]
    since: float  # epoch when alert started firing
    fired_at: float  # epoch when notification was sent
    state: AlertState = AlertState.FIRING


class PatternParser:
    """Parses simple alert pattern expressions.

    Supported syntax:
        - Single condition: "loss > 10"
        - Multiple conditions with 'and': "loss > 10 and median > 100"
        - Operators: >, >=, <, <=, ==, !=
        - Metrics: loss (loss_pct), median, avg, min, max, jitter
    """

    _METRIC_MAP = {
        "loss": "loss_pct",
        "median": "median_ms",
        "avg": "avg_ms",
        "min": "min_ms",
        "max": "max_ms",
        "jitter": "jitter_ms",
    }

    _TOKEN_RE = re.compile(
        r"(\w+)\s*(>=|<=|!=|==|>|<)\s*([\d.]+)\s*(?:and\s+)?",
        re.IGNORECASE,
    )

    @classmethod
    def parse(cls, pattern: str) -> list[tuple[str, str, float]]:
        """Parse a pattern string into a list of (metric, operator, value) tuples.

        Args:
            pattern: e.g., "loss > 10 and median > 100"

        Returns:
            List of (metric_db_name, operator, threshold_value) tuples.
        """
        conditions = []
        for match in cls._TOKEN_RE.finditer(pattern):
            metric_name = match.group(1).lower()
            operator = match.group(2)
            value = float(match.group(3))

            db_metric = cls._METRIC_MAP.get(metric_name)
            if not db_metric:
                raise ValueError(
                    f"Unknown metric '{metric_name}'. "
                    f"Supported: {', '.join(cls._METRIC_MAP.keys())}"
                )
            conditions.append((db_metric, operator, value))

        if not conditions:
            raise ValueError(f"Could not parse pattern: '{pattern}'")

        return conditions

    @classmethod
    def evaluate(cls, conditions: list[tuple[str, str, float]], metrics: dict[str, Any]) -> bool:
        """Evaluate all conditions against a metrics dict.

        All conditions must be true (AND logic).

        Args:
            conditions: Parsed conditions from parse().
            metrics: Dict with keys like 'loss_pct', 'median_ms', etc.

        Returns:
            True if all conditions match.
        """
        for metric_key, operator, threshold in conditions:
            value = metrics.get(metric_key)
            if value is None:
                return False  # Can't evaluate without data

            if not cls._compare(value, operator, threshold):
                return False

        return True

    @staticmethod
    def _compare(value: float, operator: str, threshold: float) -> bool:
        """Compare a value against a threshold using the given operator."""
        ops = {
            ">": lambda v, t: v > t,
            ">=": lambda v, t: v >= t,
            "<": lambda v, t: v < t,
            "<=": lambda v, t: v <= t,
            "==": lambda v, t: abs(v - t) < 1e-9,
            "!=": lambda v, t: abs(v - t) >= 1e-9,
        }
        op_fn = ops.get(operator)
        if not op_fn:
            raise ValueError(f"Unknown operator: {operator}")
        return op_fn(value, threshold)

    @classmethod
    def extract_values(cls, conditions: list[tuple[str, str, float]], metrics: dict[str, Any]) -> dict[str, float]:
        """Extract actual metric values for the conditions, using human-readable names."""
        reverse_map = {v: k for k, v in cls._METRIC_MAP.items()}
        values = {}
        for metric_key, _, _ in conditions:
            human_name = reverse_map.get(metric_key, metric_key)
            val = metrics.get(metric_key)
            if val is not None:
                values[human_name] = round(val, 2)
        return values


def parse_duration(duration_str: str, step_seconds: int = 60) -> int:
    """Parse a duration string like '5m', '1h', '30s' into number of ticks.

    Args:
        duration_str: Duration string (e.g., "5m", "1h").
        step_seconds: Scheduler step in seconds.

    Returns:
        Number of consecutive ticks required.
    """
    duration_str = duration_str.strip().lower()
    match = re.match(r"^(\d+)\s*(s|m|h)?$", duration_str)
    if not match:
        raise ValueError(f"Could not parse duration: '{duration_str}'")

    amount = int(match.group(1))
    unit = match.group(2) or "s"

    multiplier = {"s": 1, "m": 60, "h": 3600}
    total_seconds = amount * multiplier.get(unit, 1)
    return max(1, total_seconds // step_seconds)


class AlertEngine:
    """Evaluates alert rules after each scheduler tick.

    Tracks alert state (clear/firing) and only notifies on transitions.
    """

    def __init__(self, rules: list[AlertConfig], storage: Storage, step_seconds: int = 60):
        self._rules = rules
        self._storage = storage
        self._step_seconds = step_seconds
        self._parsed_rules: dict[str, list[tuple[str, str, float]]] = {}
        self._duration_ticks: dict[str, int] = {}
        self._active_alerts: dict[str, ActiveAlert] = {}  # key = "{rule_name}:{target}"
        self._match_counts: dict[str, int] = {}  # key = "{rule_name}:{target}" → consecutive matches

        # Parse rules upfront
        for rule in self._rules:
            self._parsed_rules[rule.name] = PatternParser.parse(rule.pattern)
            self._duration_ticks[rule.name] = parse_duration(rule.duration, step_seconds)

    @property
    def active_alerts(self) -> list[ActiveAlert]:
        """Currently firing alerts."""
        return list(self._active_alerts.values())

    async def evaluate(self, results: list[ProbeResult]) -> list[ActiveAlert]:
        """Evaluate all alert rules against the latest probe results.

        Called after each scheduler tick. Checks each result against each rule,
        tracks duration, and fires notifications on state transitions.

        Args:
            results: Latest probe results from this tick.

        Returns:
            List of alerts that transitioned state this tick (newly fired or recovered).
        """
        transitioned: list[ActiveAlert] = []

        for rule in self._rules:
            conditions = self._parsed_rules.get(rule.name, [])
            if not conditions:
                continue

            required_ticks = self._duration_ticks.get(rule.name, 1)

            for result in results:
                key = f"{rule.name}:{result.target}"

                # Build metrics dict from ProbeResult
                metrics = {
                    "loss_pct": result.loss_pct,
                    "median_ms": result.median,
                    "avg_ms": result.avg,
                    "min_ms": result.min,
                    "max_ms": result.max,
                    "jitter_ms": result.jitter,
                }

                matches = PatternParser.evaluate(conditions, metrics)

                if matches:
                    self._match_counts[key] = self._match_counts.get(key, 0) + 1
                else:
                    self._match_counts[key] = 0

                is_firing = key in self._active_alerts
                should_fire = self._match_counts[key] >= required_ticks

                # State transition: clear → firing
                if should_fire and not is_firing:
                    values = PatternParser.extract_values(conditions, metrics)
                    alert = ActiveAlert(
                        name=rule.name,
                        target=result.target,
                        condition=rule.pattern,
                        values=values,
                        since=time.time() - (required_ticks * self._step_seconds),
                        fired_at=time.time(),
                    )
                    self._active_alerts[key] = alert
                    transitioned.append(alert)
                    await self._notify(alert, rule.notify)
                    logger.warning(
                        "alert.firing",
                        alert=rule.name,
                        target=result.target,
                        values=values,
                    )

                # State transition: firing → clear (recovery)
                elif not should_fire and is_firing:
                    alert = self._active_alerts.pop(key)
                    alert.state = AlertState.CLEAR
                    transitioned.append(alert)
                    await self._notify_recovery(alert, rule.notify)
                    logger.info(
                        "alert.recovered",
                        alert=rule.name,
                        target=result.target,
                    )

        return transitioned

    async def _notify(self, alert: ActiveAlert, channels: list[AlertNotify]) -> None:
        """Send alert notification to all configured channels."""
        from datetime import datetime, timezone

        payload = {
            "alert": alert.name,
            "target": alert.target,
            "condition": alert.condition,
            "values": alert.values,
            "since": datetime.fromtimestamp(alert.since, tz=timezone.utc).isoformat(),
            "fired_at": datetime.fromtimestamp(alert.fired_at, tz=timezone.utc).isoformat(),
            "state": "firing",
        }

        for channel in channels:
            try:
                if channel.type == "webhook":
                    await self._send_webhook(channel.url, payload)
                elif channel.type == "log":
                    logger.warning("alert.notification", **payload)
                else:
                    logger.debug("alert.skip_channel", type=channel.type)
            except Exception as e:
                logger.error("alert.notify_failed", channel=channel.type, error=str(e))

    async def _notify_recovery(self, alert: ActiveAlert, channels: list[AlertNotify]) -> None:
        """Send recovery notification."""
        from datetime import datetime, timezone

        payload = {
            "alert": alert.name,
            "target": alert.target,
            "condition": alert.condition,
            "values": alert.values,
            "since": datetime.fromtimestamp(alert.since, tz=timezone.utc).isoformat(),
            "recovered_at": datetime.fromtimestamp(time.time(), tz=timezone.utc).isoformat(),
            "state": "recovered",
        }

        for channel in channels:
            try:
                if channel.type == "webhook":
                    await self._send_webhook(channel.url, payload)
                elif channel.type == "log":
                    logger.info("alert.recovery_notification", **payload)
            except Exception as e:
                logger.error("alert.notify_failed", channel=channel.type, error=str(e))

    @staticmethod
    async def _send_webhook(url: str | None, payload: dict[str, Any]) -> None:
        """Send webhook notification via HTTP POST."""
        if not url:
            return
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json=payload,
                timeout=10.0,
            )
            if resp.status_code >= 400:
                logger.error(
                    "alert.webhook_error",
                    url=url,
                    status=resp.status_code,
                    body=resp.text[:200],
                )
