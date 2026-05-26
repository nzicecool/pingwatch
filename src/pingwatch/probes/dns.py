"""DNS probe — measure DNS query latency."""

from __future__ import annotations

import time

import dns.resolver

from pingwatch.probes import BaseProbe, ProbeResult


class DnsProbe(BaseProbe):
    """DNS query latency probe."""

    name = "dns"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.timeout = kwargs.get("timeout", 5.0)

    async def run(self, target: str) -> ProbeResult:
        """Send DNS queries and measure round-trip time."""
        result = ProbeResult(target=target, probe_name=self.name)

        resolver = dns.resolver.Resolver()
        resolver.lifetime = self.timeout
        resolver.timeout = self.timeout

        for _ in range(self.pings):
            try:
                start = time.monotonic()
                resolver.resolve(target, "A")
                elapsed = (time.monotonic() - start) * 1000  # ms
                result.latencies.append(elapsed)
                result.timestamps.append(time.time())
            except dns.resolver.Timeout:
                result.latencies.append(None)
                result.timestamps.append(time.time())
            except dns.resolver.NXDOMAIN:
                result.latencies.append(None)
                result.timestamps.append(time.time())
            except Exception as e:
                result.latencies.append(None)
                result.timestamps.append(time.time())
                if result.error is None:
                    result.error = str(e)

        result.compute_stats()
        return result
