"""Fping probe — batch ICMP via fping binary (efficient for many targets)."""

from __future__ import annotations

import asyncio
import shutil
import time

from pingwatch.probes import BaseProbe, ProbeResult


class FpingProbe(BaseProbe):
    """Batch ICMP probe using fping binary."""

    name = "fping"
    requires_binary = "fping"

    def __init__(self, binary: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.binary = binary or shutil.which("fping") or "/usr/bin/fping"

    async def run(self, target: str) -> ProbeResult:
        """Ping single target via fping."""
        results = await self.run_batch([target])
        return results.get(target, ProbeResult(target=target, probe_name=self.name, error="no response"))

    async def run_batch(self, targets: list[str]) -> dict[str, ProbeResult]:
        """Ping multiple targets efficiently in one fping call."""
        results = {
            t: ProbeResult(target=t, probe_name=self.name) for t in targets
        }

        if not targets:
            return results

        cmd = [
            self.binary,
            "-C", str(self.pings),  # count
            "-q",                   # quiet
            "-B", "1",              # backoff factor
            "-t", str(int(self.timeout * 1000)),  # timeout in ms
        ] + targets

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.communicate(proc, timeout=self.pings * self.timeout + 5)
            output = stderr.decode().strip()

            for line in output.splitlines():
                parts = line.strip().split()
                if not parts:
                    continue
                host = parts[0].rstrip(":")
                if host not in results:
                    continue

                latencies = []
                for val in parts[1:]:
                    if val == "-":
                        latencies.append(None)
                    else:
                        try:
                            latencies.append(float(val))
                        except ValueError:
                            latencies.append(None)

                results[host].latencies = latencies
                results[host].timestamps = [time.time()] * len(latencies)
                results[host].compute_stats()

        except FileNotFoundError:
            for r in results.values():
                r.error = f"fping not found at {self.binary}"
        except asyncio.TimeoutError:
            for r in results.values():
                r.error = "fping timed out"
        except Exception as e:
            for r in results.values():
                r.error = str(e)

        return results
