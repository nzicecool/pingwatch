"""Tests for PingWatch REST API."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from pingwatch.api.app import create_app
from pingwatch.storage import Storage


@pytest.fixture
def mock_storage():
    """Create a mock Storage instance."""
    storage = MagicMock(spec=Storage)
    storage._db = MagicMock()
    return storage


@pytest.fixture
def app(mock_storage):
    """Create a test FastAPI app."""
    return create_app(mock_storage)


@pytest.fixture
def client(app):
    """Create a TestClient."""
    return TestClient(app)


# --- Health ---

class TestHealth:
    def test_health_ok(self, client, mock_storage):
        mock_storage.get_targets_list = AsyncMock(return_value=[])
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["storage"] == "connected"
        assert data["uptime_seconds"] >= 0

    def test_health_degraded_on_db_error(self, client, mock_storage):
        mock_storage.get_targets_list = AsyncMock(side_effect=Exception("db down"))
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["storage"] == "error"


# --- Targets ---

class TestTargets:
    def test_list_targets_empty(self, client, mock_storage):
        mock_storage.get_targets_list = AsyncMock(return_value=[])
        resp = client.get("/api/targets")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["targets"] == []

    def test_list_targets_with_data(self, client, mock_storage):
        mock_storage.get_targets_list = AsyncMock(return_value=[
            {
                "target_name": "Google DNS",
                "probe_type": "fping",
                "median_ms": 12.5,
                "loss_pct": 0.0,
                "jitter_ms": 3.2,
                "timestamp": time.time(),
            },
            {
                "target_name": "Cloudflare",
                "probe_type": "http",
                "median_ms": 447.0,
                "loss_pct": 0.0,
                "jitter_ms": 109.0,
                "timestamp": time.time(),
            },
        ])
        resp = client.get("/api/targets")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 2
        assert data["targets"][0]["target_name"] == "Google DNS"


# --- Measurements ---

class TestMeasurements:
    def test_get_measurements_empty(self, client, mock_storage):
        mock_storage.get_measurements = AsyncMock(return_value=[])
        mock_storage.get_targets_list = AsyncMock(return_value=[
            {"target_name": "Google DNS", "probe_type": "fping", "median_ms": 10, "loss_pct": 0, "jitter_ms": 2, "timestamp": time.time()},
        ])
        resp = client.get("/api/targets/Google DNS/measurements")
        assert resp.status_code == 200
        data = resp.json()
        assert data["target"] == "Google DNS"
        assert data["count"] == 0

    def test_get_measurements_with_data(self, client, mock_storage):
        now = time.time()
        mock_storage.get_measurements = AsyncMock(return_value=[
            {
                "id": 1,
                "target_name": "Google DNS",
                "probe_type": "fping",
                "timestamp": now,
                "median_ms": 12.5,
                "avg_ms": 13.0,
                "min_ms": 10.0,
                "max_ms": 20.0,
                "loss_pct": 0.0,
                "jitter_ms": 3.2,
                "sent": 20,
                "received": 20,
                "latencies_json": "[10,12,15]",
                "error": None,
            },
        ])
        resp = client.get("/api/targets/Google DNS/measurements")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["data"][0]["median_ms"] == 12.5

    def test_get_measurements_404(self, client, mock_storage):
        mock_storage.get_measurements = AsyncMock(return_value=[])
        mock_storage.get_targets_list = AsyncMock(return_value=[])
        resp = client.get("/api/targets/NonExistent/measurements")
        assert resp.status_code == 404

    def test_get_measurements_with_params(self, client, mock_storage):
        mock_storage.get_measurements = AsyncMock(return_value=[])
        resp = client.get("/api/targets/test/measurements?since=1000&until=2000&limit=500")
        assert resp.status_code == 200
        mock_storage.get_measurements.assert_called_once_with(
            "test", since=1000.0, until=2000.0, limit=500
        )


# --- Rollup ---

class TestRollup:
    def test_get_rollup_empty(self, client, mock_storage):
        mock_storage.get_rollup = AsyncMock(return_value=[])
        mock_storage.get_targets_list = AsyncMock(return_value=[
            {"target_name": "test", "probe_type": "fping", "median_ms": 10, "loss_pct": 0, "jitter_ms": 2, "timestamp": time.time()},
        ])
        resp = client.get("/api/targets/test/rollup?period=5min")
        assert resp.status_code == 200
        data = resp.json()
        assert data["period"] == "5min"

    def test_get_rollup_with_data(self, client, mock_storage):
        now = time.time()
        mock_storage.get_rollup = AsyncMock(return_value=[
            {
                "id": 1,
                "target_name": "test",
                "probe_type": "fping",
                "period_start": now - 300,
                "median_ms": 15.0,
                "avg_ms": 16.0,
                "min_ms": 10.0,
                "max_ms": 25.0,
                "loss_pct": 0.0,
                "jitter_ms": 4.0,
                "sample_count": 5,
            },
        ])
        resp = client.get("/api/targets/test/rollup?period=5min")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1

    def test_get_rollup_404(self, client, mock_storage):
        mock_storage.get_rollup = AsyncMock(return_value=[])
        mock_storage.get_targets_list = AsyncMock(return_value=[])
        resp = client.get("/api/targets/NonExistent/rollup?period=5min")
        assert resp.status_code == 404

    def test_get_rollup_invalid_period(self, client, mock_storage):
        mock_storage.get_rollup = AsyncMock(side_effect=ValueError("Invalid period 'invalid'"))
        mock_storage.get_targets_list = AsyncMock(return_value=[
            {"target_name": "test", "probe_type": "fping", "median_ms": 10, "loss_pct": 0, "jitter_ms": 2, "timestamp": time.time()},
        ])
        resp = client.get("/api/targets/test/rollup?period=invalid")
        assert resp.status_code == 422  # FastAPI validation rejects invalid pattern


# --- Summary ---

class TestSummary:
    def test_summary_empty(self, client, mock_storage):
        mock_storage.get_all_targets_summary = AsyncMock(return_value=[])
        resp = client.get("/api/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["period_hours"] == 1

    def test_summary_with_data(self, client, mock_storage):
        mock_storage.get_all_targets_summary = AsyncMock(return_value=[
            {
                "target_name": "Google DNS",
                "probe_type": "fping",
                "sample_count": 60,
                "avg_median": 12.5,
                "overall_min": 8.0,
                "overall_max": 25.0,
                "avg_loss": 0.0,
                "avg_jitter": 3.2,
            },
        ])
        resp = client.get("/api/summary?period_hours=6")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["period_hours"] == 6


# --- Dashboard ---

class TestDashboard:
    def test_dashboard_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "PingWatch" in resp.text
        assert "plotly" in resp.text.lower()

    def test_dashboard_has_chart_area(self, client):
        resp = client.get("/")
        assert "detail-chart" in resp.text
        assert "overview-grid" in resp.text
