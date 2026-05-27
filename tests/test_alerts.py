"""Tests for PingWatch Alerting Engine."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pingwatch.alerts.engine import (
    ActiveAlert,
    AlertEngine,
    AlertState,
    PatternParser,
    parse_duration,
)
from pingwatch.config import AlertConfig, AlertNotify
from pingwatch.probes import ProbeResult


def _make_result(
    target: str = "Google DNS",
    probe_name: str = "fping",
    loss_pct: float = 0.0,
    median: float = 10.0,
    jitter: float = 2.0,
) -> ProbeResult:
    """Create a ProbeResult with given metrics."""
    r = ProbeResult(
        target=target,
        probe_name=probe_name,
        latencies=[median] * 10,
    )
    r.compute_stats()
    # Override specific values for testing
    r.loss_pct = loss_pct
    r.jitter = jitter
    return r


# --- Pattern parsing ---

class TestPatternParser:
    def test_single_condition_loss(self):
        conditions = PatternParser.parse("loss > 10")
        assert len(conditions) == 1
        assert conditions[0] == ("loss_pct", ">", 10.0)

    def test_single_condition_median(self):
        conditions = PatternParser.parse("median > 100")
        assert conditions[0] == ("median_ms", ">", 100.0)

    def test_single_condition_jitter(self):
        conditions = PatternParser.parse("jitter > 50")
        assert conditions[0] == ("jitter_ms", ">", 50.0)

    def test_compound_condition_and(self):
        conditions = PatternParser.parse("loss > 10 and median > 100")
        assert len(conditions) == 2
        assert conditions[0] == ("loss_pct", ">", 10.0)
        assert conditions[1] == ("median_ms", ">", 100.0)

    def test_operators(self):
        for op in [">", ">=", "<", "<=", "==", "!="]:
            conditions = PatternParser.parse(f"loss {op} 5")
            assert conditions[0][1] == op

    def test_unknown_metric_raises(self):
        with pytest.raises(ValueError, match="Unknown metric"):
            PatternParser.parse("foobar > 10")

    def test_empty_pattern_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            PatternParser.parse("")

    def test_all_metrics(self):
        for metric in ["loss", "median", "avg", "min", "max", "jitter"]:
            conditions = PatternParser.parse(f"{metric} > 50")
            assert len(conditions) == 1

    def test_evaluate_true(self):
        conditions = PatternParser.parse("loss > 10")
        metrics = {"loss_pct": 15.0, "median_ms": 100.0}
        assert PatternParser.evaluate(conditions, metrics) is True

    def test_evaluate_false(self):
        conditions = PatternParser.parse("loss > 10")
        metrics = {"loss_pct": 5.0, "median_ms": 100.0}
        assert PatternParser.evaluate(conditions, metrics) is False

    def test_evaluate_compound_both_true(self):
        conditions = PatternParser.parse("loss > 10 and median > 100")
        metrics = {"loss_pct": 15.0, "median_ms": 120.0}
        assert PatternParser.evaluate(conditions, metrics) is True

    def test_evaluate_compound_one_false(self):
        conditions = PatternParser.parse("loss > 10 and median > 100")
        metrics = {"loss_pct": 15.0, "median_ms": 50.0}
        assert PatternParser.evaluate(conditions, metrics) is False

    def test_evaluate_none_value(self):
        conditions = PatternParser.parse("median > 100")
        metrics = {"loss_pct": 5.0, "median_ms": None}
        assert PatternParser.evaluate(conditions, metrics) is False

    def test_extract_values(self):
        conditions = PatternParser.parse("loss > 10 and median > 100")
        metrics = {"loss_pct": 15.2, "median_ms": 120.5}
        values = PatternParser.extract_values(conditions, metrics)
        assert values == {"loss": 15.2, "median": 120.5}


# --- Duration parsing ---

class TestDurationParsing:
    def test_seconds(self):
        assert parse_duration("30s", step_seconds=60) == 1  # 30s < 60s step = 1 tick

    def test_minutes(self):
        assert parse_duration("5m", step_seconds=60) == 5  # 5 min = 5 ticks at 60s step

    def test_hours(self):
        assert parse_duration("1h", step_seconds=60) == 60  # 60 min = 60 ticks

    def test_minimum_1_tick(self):
        assert parse_duration("1s", step_seconds=60) == 1

    def test_custom_step(self):
        assert parse_duration("10m", step_seconds=120) == 5  # 600s / 120s = 5 ticks

    def test_invalid_duration(self):
        with pytest.raises(ValueError, match="Could not parse duration"):
            parse_duration("invalid")


# --- Alert Engine ---

class TestAlertEngine:
    def _make_engine(self, rules, step=60):
        storage = MagicMock()
        return AlertEngine(rules=rules, storage=storage, step_seconds=step)

    @pytest.mark.asyncio
    async def test_no_rules(self):
        engine = self._make_engine([])
        results = [_make_result(loss_pct=50)]
        transitioned = await engine.evaluate(results)
        assert transitioned == []

    @pytest.mark.asyncio
    async def test_alert_fires_immediately_no_duration(self):
        rule = AlertConfig(
            name="high_loss",
            pattern="loss > 10",
            duration="0s",  # 0 ticks required
            notify=[AlertNotify(type="log")],
        )
        engine = self._make_engine([rule])
        results = [_make_result(target="Google DNS", loss_pct=50)]
        transitioned = await engine.evaluate(results)
        assert len(transitioned) == 1
        assert transitioned[0].name == "high_loss"
        assert transitioned[0].target == "Google DNS"

    @pytest.mark.asyncio
    async def test_alert_fires_after_duration(self):
        rule = AlertConfig(
            name="high_loss",
            pattern="loss > 10",
            duration="2m",  # 2 ticks at 60s step
            notify=[AlertNotify(type="log")],
        )
        engine = self._make_engine([rule])

        # Tick 1: condition met but not enough duration
        results = [_make_result(target="Google DNS", loss_pct=50)]
        transitioned = await engine.evaluate(results)
        assert len(transitioned) == 0

        # Tick 2: now duration met → fires
        transitioned = await engine.evaluate(results)
        assert len(transitioned) == 1
        assert transitioned[0].name == "high_loss"

    @pytest.mark.asyncio
    async def test_dont_renotify_while_firing(self):
        rule = AlertConfig(
            name="high_loss",
            pattern="loss > 10",
            duration="0s",
            notify=[AlertNotify(type="log")],
        )
        engine = self._make_engine([rule])
        results = [_make_result(target="Google DNS", loss_pct=50)]

        # Tick 1: fires
        await engine.evaluate(results)
        # Tick 2: still firing, no re-notification
        transitioned = await engine.evaluate(results)
        assert len(transitioned) == 0

    @pytest.mark.asyncio
    async def test_recovery_transition(self):
        rule = AlertConfig(
            name="high_loss",
            pattern="loss > 10",
            duration="0s",
            notify=[AlertNotify(type="log")],
        )
        engine = self._make_engine([rule])

        # Fire the alert
        await engine.evaluate([_make_result(target="Google DNS", loss_pct=50)])

        # Recover: loss drops below threshold
        transitioned = await engine.evaluate([_make_result(target="Google DNS", loss_pct=2)])
        assert len(transitioned) == 1
        assert transitioned[0].state == AlertState.CLEAR

    @pytest.mark.asyncio
    async def test_condition_resets_duration_counter(self):
        rule = AlertConfig(
            name="high_loss",
            pattern="loss > 10",
            duration="2m",  # needs 2 consecutive
            notify=[AlertNotify(type="log")],
        )
        engine = self._make_engine([rule])

        # Tick 1: matches
        await engine.evaluate([_make_result(target="Google DNS", loss_pct=50)])
        # Tick 2: no match → counter resets
        await engine.evaluate([_make_result(target="Google DNS", loss_pct=2)])
        # Tick 3: matches again (counter = 1)
        transitioned = await engine.evaluate([_make_result(target="Google DNS", loss_pct=50)])
        assert len(transitioned) == 0  # Not yet, need one more

    @pytest.mark.asyncio
    async def test_multiple_targets_independent(self):
        rule = AlertConfig(
            name="high_loss",
            pattern="loss > 10",
            duration="0s",
            notify=[AlertNotify(type="log")],
        )
        engine = self._make_engine([rule])

        results = [
            _make_result(target="Google DNS", loss_pct=50),
            _make_result(target="Cloudflare DNS", loss_pct=2),
        ]
        transitioned = await engine.evaluate(results)
        assert len(transitioned) == 1
        assert transitioned[0].target == "Google DNS"

    @pytest.mark.asyncio
    async def test_webhook_notification(self):
        rule = AlertConfig(
            name="high_loss",
            pattern="loss > 10",
            duration="0s",
            notify=[AlertNotify(type="webhook", url="https://hooks.example.com/alert")],
        )
        engine = self._make_engine([rule])

        with patch("pingwatch.alerts.engine.httpx.AsyncClient") as mock_client:
            mock_post = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_ctx.post = mock_post
            mock_client.return_value = mock_ctx

            await engine.evaluate([_make_result(target="Google DNS", loss_pct=50)])

            # Verify webhook was called
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            assert call_kwargs.kwargs["json"]["alert"] == "high_loss"
            assert call_kwargs.kwargs["json"]["target"] == "Google DNS"
            assert call_kwargs.kwargs["json"]["state"] == "firing"

    @pytest.mark.asyncio
    async def test_active_alerts_property(self):
        rule = AlertConfig(
            name="high_loss",
            pattern="loss > 10",
            duration="0s",
            notify=[],
        )
        engine = self._make_engine([rule])

        assert len(engine.active_alerts) == 0
        await engine.evaluate([_make_result(target="Google DNS", loss_pct=50)])
        assert len(engine.active_alerts) == 1
