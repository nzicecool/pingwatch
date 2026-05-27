"""Tests for PingWatch AI Network Briefer."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from pingwatch.ai.briefer import NetworkBriefer
from pingwatch.api.app import create_app
from pingwatch.storage import Storage


# --- Helpers ---


def mock_storage_with_data():
    """Create a mock Storage with sample data responses."""
    storage = MagicMock(spec=Storage)
    storage._db = MagicMock()

    storage.get_all_targets_summary = AsyncMock(return_value=[
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
        {
            "target_name": "Cloudflare",
            "probe_type": "http",
            "sample_count": 60,
            "avg_median": 447.0,
            "overall_min": 200.0,
            "overall_max": 800.0,
            "avg_loss": 2.5,
            "avg_jitter": 109.0,
        },
    ])

    storage.get_anomalies = AsyncMock(return_value=[
        {
            "target_name": "Cloudflare",
            "type": "high_latency",
            "median_ms": 900.0,
            "baseline_median": 400.0,
            "timestamp": time.time(),
        },
    ])

    storage.get_top_latency = AsyncMock(return_value=[
        {"target_name": "Cloudflare", "avg_median": 447.0, "peak": 800.0},
    ])

    storage.get_top_loss = AsyncMock(return_value=[
        {"target_name": "Cloudflare", "avg_loss": 2.5, "peak_loss": 10.0},
    ])

    storage.get_target_detail = AsyncMock(return_value={
        "target_name": "Google DNS",
        "probe_type": "fping",
        "sample_count": 1440,
        "avg_median": 12.5,
        "overall_min": 8.0,
        "overall_max": 25.0,
        "avg_loss": 0.0,
        "avg_jitter": 3.2,
        "measurements": [
            {"timestamp": time.time() - 60, "median_ms": 12.0, "loss_pct": 0, "jitter_ms": 2.5},
            {"timestamp": time.time(), "median_ms": 13.0, "loss_pct": 0, "jitter_ms": 3.0},
        ],
    })

    return storage


def ai_config(enabled=True):
    return {
        "enabled": enabled,
        "model": "test-model",
        "api_base": "https://api.test.com/v1",
        "api_key_env": "TEST_API_KEY",
        "max_context_tokens": 4000,
    }


# --- Context Gathering ---

class TestContextGathering:
    @pytest.mark.asyncio
    async def test_gather_context(self):
        storage = mock_storage_with_data()
        briefer = NetworkBriefer(storage, ai_config())

        context = await briefer.gather_context(1)
        assert context["period_hours"] == 1
        assert context["target_count"] == 2
        assert len(context["targets"]) == 2
        assert len(context["anomalies"]) == 1

    @pytest.mark.asyncio
    async def test_gather_context_no_data(self):
        storage = MagicMock(spec=Storage)
        storage._db = MagicMock()
        storage.get_all_targets_summary = AsyncMock(return_value=[])
        storage.get_anomalies = AsyncMock(return_value=[])
        storage.get_top_latency = AsyncMock(return_value=[])
        storage.get_top_loss = AsyncMock(return_value=[])

        briefer = NetworkBriefer(storage, ai_config())
        context = await briefer.gather_context(1)
        assert context["target_count"] == 0


# --- Score Calculation ---

class TestScoreCalculation:
    @pytest.mark.asyncio
    async def test_perfect_score(self):
        storage = MagicMock(spec=Storage)
        storage._db = MagicMock()
        storage.get_all_targets_summary = AsyncMock(return_value=[
            {
                "target_name": "Google DNS",
                "probe_type": "fping",
                "sample_count": 60,
                "avg_median": 10.0,
                "overall_min": 8.0,
                "overall_max": 15.0,
                "avg_loss": 0.0,
                "avg_jitter": 3.0,
            },
        ])

        briefer = NetworkBriefer(storage, ai_config())
        result = await briefer.compute_network_score(1)
        assert result["score"] == 100.0
        assert result["grade"] == "A+"

    @pytest.mark.asyncio
    async def test_degraded_score(self):
        storage = MagicMock(spec=Storage)
        storage._db = MagicMock()
        storage.get_all_targets_summary = AsyncMock(return_value=[
            {
                "target_name": "Bad Target",
                "probe_type": "fping",
                "sample_count": 60,
                "avg_median": 200.0,
                "overall_min": 50.0,
                "overall_max": 500.0,
                "avg_loss": 15.0,
                "avg_jitter": 50.0,
            },
        ])

        briefer = NetworkBriefer(storage, ai_config())
        result = await briefer.compute_network_score(1)
        # Loss: -min(50, 15*5) = -50, Latency: -min(30, (200-50)/10) = -15, Jitter: -min(20, (50-10)/5) = -8
        # 100 - 50 - 15 - 8 = 27
        assert result["score"] == 27.0
        assert result["grade"] == "F"

    @pytest.mark.asyncio
    async def test_no_targets(self):
        storage = MagicMock(spec=Storage)
        storage._db = MagicMock()
        storage.get_all_targets_summary = AsyncMock(return_value=[])

        briefer = NetworkBriefer(storage, ai_config())
        result = await briefer.compute_network_score(1)
        assert result["score"] == 100
        assert "No targets" in result["note"]

    @pytest.mark.asyncio
    async def test_multiple_targets_averaged(self):
        storage = MagicMock(spec=Storage)
        storage._db = MagicMock()
        storage.get_all_targets_summary = AsyncMock(return_value=[
            {
                "target_name": "Good",
                "probe_type": "fping",
                "sample_count": 60,
                "avg_median": 10.0,
                "overall_min": 8.0,
                "overall_max": 15.0,
                "avg_loss": 0.0,
                "avg_jitter": 3.0,
            },
            {
                "target_name": "OK",
                "probe_type": "fping",
                "sample_count": 60,
                "avg_median": 30.0,
                "overall_min": 20.0,
                "overall_max": 40.0,
                "avg_loss": 2.0,
                "avg_jitter": 5.0,
            },
        ])

        briefer = NetworkBriefer(storage, ai_config())
        result = await briefer.compute_network_score(1)
        # Good: 100 - 0 - 0 - 0 = 100
        # OK: 100 - min(50, 2*5)=10 - 0 - 0 = 90
        # Average = 95
        assert result["score"] == 95.0
        assert result["grade"] == "A+"
        assert result["target_count"] == 2


# --- API endpoints when AI disabled ---

class TestAIEndpointsDisabled:
    def setup_method(self):
        storage = MagicMock(spec=Storage)
        storage._db = MagicMock()
        self.app = create_app(storage, ai_config=None)
        self.client = TestClient(self.app)

    def test_briefing_disabled(self):
        resp = self.client.get("/api/ai/briefing")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert "not configured" in data["message"]

    def test_analyze_disabled(self):
        resp = self.client.get("/api/ai/analyze/Google%20DNS")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False

    def test_score_disabled(self):
        resp = self.client.get("/api/ai/score")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False

    def test_ask_disabled(self):
        resp = self.client.post("/api/ai/ask", json={"question": "How's my network?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False


# --- API endpoints with mocked LLM ---

class TestAIEndpointsEnabled:
    def setup_method(self):
        self.storage = mock_storage_with_data()
        self.app = create_app(self.storage, ai_config(enabled=True))
        self.client = TestClient(self.app)
        # Set env var so briefer.enabled returns True
        os.environ["TEST_API_KEY"] = "test-key-123"

    def teardown_method(self):
        os.environ.pop("TEST_API_KEY", None)

    def test_score_works_without_llm(self):
        resp = self.client.get("/api/ai/score")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["score"] is not None
        assert isinstance(data["score"], float)
        assert "grade" in data

    @patch("pingwatch.ai.briefer.NetworkBriefer.generate_briefing")
    def test_briefing_with_mock_llm(self, mock_briefing):
        mock_briefing.return_value = "Network is healthy. All targets showing normal latency."
        resp = self.client.get("/api/ai/briefing?period=1h")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert "healthy" in data["briefing"]

    @patch("pingwatch.ai.briefer.NetworkBriefer.analyze_target")
    def test_analyze_with_mock_llm(self, mock_analyze):
        mock_analyze.return_value = "Google DNS is performing well with 12ms median."
        resp = self.client.get("/api/ai/analyze/Google%20DNS?period_hours=24")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert "Google DNS" in data["analysis"]

    @patch("pingwatch.ai.briefer.NetworkBriefer.ask")
    def test_ask_with_mock_llm(self, mock_ask):
        mock_ask.return_value = "Your network looks good overall."
        resp = self.client.post("/api/ai/ask", json={"question": "How's my network?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["question"] == "How's my network?"
        assert "good" in data["answer"]


# --- Config integration ---

class TestAIConfig:
    def test_ai_config_defaults(self):
        from pingwatch.config import PingWatchConfig
        config = PingWatchConfig()
        assert config.ai.enabled is False
        assert config.ai.model == "zai/glm-4.5-air"
        assert config.ai.api_key_env == "ZAI_API_KEY"

    def test_ai_config_from_yaml(self):
        from pingwatch.config import load_config
        import tempfile
        import yaml

        cfg = {
            "ai": {
                "enabled": True,
                "model": "custom-model",
                "api_base": "https://custom.api.com/v1",
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(cfg, f)
            config = load_config(f.name)

        assert config.ai.enabled is True
        assert config.ai.model == "custom-model"
        assert config.ai.api_base == "https://custom.api.com/v1"
