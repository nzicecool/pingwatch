"""TCP probe — measure TCP handshake latency."""

from __future__ import annotations

import asyncio
import socket
import time

from pingwatch.probes import BaseProbe, ProbeResult


class TcpProbe(BaseProbe):
    """TCP connection latency probe."""

    name = "tcp"

    def __init__(self, port: int = 80, **kwargs):
        super().__init__(**kwargs)
        self.port = port

    async def run(self, target: str) -> ProbeResult:
        """Open TCP connections and measure handshake time."""
        result = ProbeResult(target=target, probe_name=self.name)

        for _ in range(self.pings):
            try:
                start = time.monotonic()
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(target, self.port),
                    timeout=self.timeout,
                )
                elapsed = (time.monotonic() - start) * 1000  # ms
                writer.close()
                await writer.wait_closed()
                result.latencies.append(elapsed)
                result.timestamps.append(time.time())
            except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                result.latencies.append(None)
                result.timestamps.append(time.time())
            except Exception as e:
                result.latencies.append(None)
                result.timestamps.append(time.time())
                if result.error is None:
                    result.error = str(e)

        result.compute_stats()
        return result
