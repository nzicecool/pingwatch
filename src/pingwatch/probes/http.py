"""HTTP probe — measure HTTP/HTTPS round-trip time."""

from __future__ import annotations

import time

import httpx

from pingwatch.probes import BaseProbe, ProbeResult


class HttpProbe(BaseProbe):
    """HTTP/HTTPS latency probe."""

    name = "http"

    async def run(self, target: str) -> ProbeResult:
        """Send HTTP requests and measure round-trip time."""
        result = ProbeResult(target=target, probe_name=self.name)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=self.timeout),
            follow_redirects=True,
            verify=False,
        ) as client:
            for _ in range(self.pings):
                try:
                    start = time.monotonic()
                    resp = await client.get(target)
                    elapsed = (time.monotonic() - start) * 1000  # ms

                    if 200 <= resp.status_code < 400:
                        result.latencies.append(elapsed)
                    else:
                        result.latencies.append(None)  # non-success = loss
                    result.timestamps.append(time.time())

                except (httpx.TimeoutException, httpx.ConnectError):
                    result.latencies.append(None)
                    result.timestamps.append(time.time())
                except Exception as e:
                    result.latencies.append(None)
                    result.timestamps.append(time.time())
                    if result.error is None:
                        result.error = str(e)

        result.compute_stats()
        return result
