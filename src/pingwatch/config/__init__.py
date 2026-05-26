"""PingWatch configuration loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class ProbeConfig(BaseModel):
    """Probe definition."""

    name: str
    type: str  # icmp | fping | http | dns | tcp
    binary: str | None = None  # for fping: path to binary
    timeout: float = 5.0
    pings: int = 20
    port: int | None = None  # for tcp probe


class HostConfig(BaseModel):
    """Individual target host."""

    label: str
    address: str


class TargetGroup(BaseModel):
    """Group of targets sharing a probe."""

    name: str
    probe: str  # references ProbeConfig.name
    hosts: list[HostConfig]


class AlertNotify(BaseModel):
    """Notification channel for an alert."""

    type: str  # webhook | email | slack | discord
    url: str | None = None
    to: str | None = None


class AlertConfig(BaseModel):
    """Alert rule definition."""

    name: str
    pattern: str  # e.g., "loss > 10"
    duration: str = "5m"  # e.g., "5m", "1h"
    notify: list[AlertNotify] = Field(default_factory=list)


class StorageConfig(BaseModel):
    """Storage backend configuration."""

    backend: str = "sqlite"
    path: str = "/var/lib/pingwatch/data.db"
    retention_days: int = 365


class GeneralConfig(BaseModel):
    """General settings."""

    step: int = 60
    pings: int = 20
    owner: str = "pingwatch"
    concurrent_probes: bool = True
    max_parallel: int = 10
    offset: str = "random"  # or "random" or percentage


class PingWatchConfig(BaseModel):
    """Top-level PingWatch configuration."""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    probes: list[ProbeConfig] = Field(default_factory=list)
    targets: list[TargetGroup] = Field(default_factory=list)
    alerts: list[AlertConfig] = Field(default_factory=list)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    @field_validator("probes", "targets", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list:
        return v if isinstance(v, list) else []


def load_config(path: str | Path) -> PingWatchConfig:
    """Load and validate config from YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return PingWatchConfig(**raw)
