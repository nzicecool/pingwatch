"""Probe engine — base classes and result types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class ProbeResult:
    """Result from a single probe run against one target."""

    target: str
    probe_name: str
    timestamps: list[float] = field(default_factory=list)
    latencies: list[float | None] = field(default_factory=list)  # ms, None = lost
    loss_pct: float = 0.0
    jitter: float = 0.0  # ms stddev of successful pings
    error: str | None = None

    @property
    def median(self) -> float | None:
        """Median latency of successful responses."""
        successful = [l for l in self.latencies if l is not None]
        if not successful:
            return None
        successful.sort()
        n = len(successful)
        mid = n // 2
        if n % 2 == 0:
            return (successful[mid - 1] + successful[mid]) / 2
        return successful[mid]

    @property
    def avg(self) -> float | None:
        """Average latency of successful responses."""
        successful = [l for l in self.latencies if l is not None]
        if not successful:
            return None
        return sum(successful) / len(successful)

    @property
    def min(self) -> float | None:
        successful = [l for l in self.latencies if l is not None]
        return min(successful) if successful else None

    @property
    def max(self) -> float | None:
        successful = [l for l in self.latencies if l is not None]
        return max(successful) if successful else None

    @property
    def sent(self) -> int:
        return len(self.latencies)

    @property
    def lost(self) -> int:
        return sum(1 for l in self.latencies if l is None)

    @property
    def received(self) -> int:
        return self.sent - self.lost

    def compute_stats(self) -> None:
        """Compute derived stats (loss, jitter) from raw latencies."""
        if self.sent > 0:
            self.loss_pct = (self.lost / self.sent) * 100
        successful = [l for l in self.latencies if l is not None]
        if len(successful) >= 2:
            import statistics
            self.jitter = statistics.stdev(successful)


class BaseProbe(ABC):
    """Abstract base class for all probes."""

    name: ClassVar[str] = "base"
    requires_binary: ClassVar[str | None] = None

    def __init__(self, timeout: float = 5.0, pings: int = 20, **kwargs):
        self.timeout = timeout
        self.pings = pings

    @abstractmethod
    async def run(self, target: str) -> ProbeResult:
        """Execute probe against target and return result."""
        ...

    @classmethod
    def create(cls, probe_type: str, **kwargs) -> BaseProbe:
        """Factory method to create a probe by type name."""
        probe_map = {
            "icmp": IcmpProbe,
            "fping": FpingProbe,
            "http": HttpProbe,
            "dns": DnsProbe,
            "tcp": TcpProbe,
        }
        probe_cls = probe_map.get(probe_type)
        if not probe_cls:
            raise ValueError(f"Unknown probe type: {probe_type}")
        return probe_cls(**kwargs)


# Import concrete implementations (avoids circular imports at class level)
from pingwatch.probes.icmp import IcmpProbe
from pingwatch.probes.fping import FpingProbe
from pingwatch.probes.http import HttpProbe
from pingwatch.probes.dns import DnsProbe
from pingwatch.probes.tcp import TcpProbe

__all__ = [
    "ProbeResult",
    "BaseProbe",
    "IcmpProbe",
    "FpingProbe",
    "HttpProbe",
    "DnsProbe",
    "TcpProbe",
]
