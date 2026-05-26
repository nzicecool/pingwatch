"""Tests for configuration loading."""

import tempfile
from pathlib import Path

import pytest
import yaml

from pingwatch.config import PingWatchConfig, load_config


class TestConfig:
    def test_load_minimal_config(self, tmp_path):
        config_data = {
            "general": {"step": 60, "pings": 10},
            "probes": [
                {"name": "my_http", "type": "http", "timeout": 5.0},
            ],
            "targets": [
                {
                    "name": "Test",
                    "probe": "my_http",
                    "hosts": [{"label": "Google", "address": "https://google.com"}],
                },
            ],
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert config.general.step == 60
        assert len(config.probes) == 1
        assert config.probes[0].name == "my_http"
        assert len(config.targets) == 1
        assert config.targets[0].hosts[0].label == "Google"

    def test_default_values(self, tmp_path):
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")

        config = load_config(config_file)
        assert config.general.step == 60
        assert config.general.concurrent_probes is True
        assert config.storage.backend == "sqlite"
        assert config.storage.retention_days == 365

    def test_full_config(self, tmp_path):
        config_data = {
            "general": {"step": 120, "pings": 20, "owner": "ops"},
            "probes": [
                {"name": "fp", "type": "fping", "binary": "/usr/bin/fping", "pings": 20},
                {"name": "hp", "type": "http"},
                {"name": "dp", "type": "dns"},
                {"name": "tp", "type": "tcp", "port": 443},
            ],
            "targets": [
                {
                    "name": "DNS",
                    "probe": "fp",
                    "hosts": [
                        {"label": "CF", "address": "1.1.1.1"},
                        {"label": "Google", "address": "8.8.8.8"},
                    ],
                },
            ],
            "storage": {"backend": "sqlite", "path": "/tmp/pw.db", "retention_days": 180},
        }
        config_file = tmp_path / "full.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert config.general.step == 120
        assert len(config.probes) == 4
        assert config.targets[0].hosts[1].address == "8.8.8.8"
        assert config.storage.retention_days == 180

    def test_invalid_probe_type(self):
        from pingwatch.probes import BaseProbe

        with pytest.raises(ValueError, match="Unknown probe type"):
            BaseProbe.create("nonexistent")

    def test_probe_factory(self):
        from pingwatch.probes import BaseProbe, HttpProbe, IcmpProbe

        probe = BaseProbe.create("http", timeout=3.0, pings=5)
        assert isinstance(probe, HttpProbe)
        assert probe.pings == 5

        probe2 = BaseProbe.create("icmp", pings=10)
        assert isinstance(probe2, IcmpProbe)
