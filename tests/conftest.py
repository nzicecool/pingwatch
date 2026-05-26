"""Test fixtures and shared utilities."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from pingwatch.config import (
    GeneralConfig,
    HostConfig,
    PingWatchConfig,
    ProbeConfig,
    StorageConfig,
    TargetGroup,
)
from pingwatch.storage import Storage


@pytest.fixture
def tmp_db(tmp_path):
    """Temporary SQLite database."""
    return str(tmp_path / "test.db")


@pytest.fixture
async def storage(tmp_db):
    """Connected storage instance."""
    s = Storage(tmp_db)
    await s.connect()
    yield s
    await s.close()


@pytest.fixture
def sample_config():
    """Sample PingWatch config for testing."""
    return PingWatchConfig(
        general=GeneralConfig(step=60, pings=5, concurrent_probes=True),
        probes=[
            ProbeConfig(name="test_http", type="http", timeout=3.0, pings=3),
            ProbeConfig(name="test_tcp", type="tcp", timeout=3.0, pings=3, port=443),
            ProbeConfig(name="test_dns", type="dns", timeout=3.0, pings=3),
        ],
        targets=[
            TargetGroup(
                name="Web",
                probe="test_http",
                hosts=[
                    HostConfig(label="Google", address="https://www.google.com"),
                ],
            ),
            TargetGroup(
                name="TCP",
                probe="test_tcp",
                hosts=[
                    HostConfig(label="Google TCP", address="www.google.com"),
                ],
            ),
            TargetGroup(
                name="DNS",
                probe="test_dns",
                hosts=[
                    HostConfig(label="Google DNS", address="google.com"),
                ],
            ),
        ],
        storage=StorageConfig(path="./data/test.db"),
    )
