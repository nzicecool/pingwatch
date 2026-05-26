"""Scheduler — tick-based probe execution engine."""

from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict

import structlog

from pingwatch.config import PingWatchConfig, ProbeConfig, TargetGroup
from pingwatch.probes import BaseProbe, ProbeResult
from pingwatch.storage import Storage

logger = structlog.get_logger()


class ProbeScheduler:
    """Orchestrates periodic probe execution and result storage."""

    def __init__(self, config: PingWatchConfig, storage: Storage):
        self.config = config
        self.storage = storage
        self._probes: dict[str, BaseProbe] = {}
        self._targets: list[TargetGroup] = []
        self._running = False
        self._task: asyncio.Task | None = None

    def initialise(self) -> None:
        """Build probe instances and target list from config."""
        # Create probe instances
        for pc in self.config.probes:
            kwargs = {"timeout": pc.timeout, "pings": pc.pings}
            if pc.binary:
                kwargs["binary"] = pc.binary
            if pc.port:
                kwargs["port"] = pc.port
            self._probes[pc.name] = BaseProbe.create(pc.type, **kwargs)

        self._targets = self.config.targets
        logger.info(
            "scheduler.initialised",
            probes=list(self._probes.keys()),
            target_groups=[t.name for t in self._targets],
        )

    async def start(self) -> None:
        """Start the scheduler loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("scheduler.started", step=self.config.general.step)

    async def stop(self) -> None:
        """Stop the scheduler loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("scheduler.stopped")

    async def _run_loop(self) -> None:
        """Main tick loop."""
        # Apply offset
        offset = self.config.general.offset
        if offset == "random":
            max_offset = self.config.general.step * 0.1
            wait = random.uniform(0, max_offset)
            logger.debug("scheduler.offset_wait", seconds=wait)
            await asyncio.sleep(wait)

        while self._running:
            tick_start = time.monotonic()
            logger.info("scheduler.tick", ts=time.time())

            try:
                results = await self._execute_tick()
                if results:
                    await self.storage.store_results(results)
                    logger.info(
                        "scheduler.tick_complete",
                        results=len(results),
                        elapsed=f"{time.monotonic() - tick_start:.2f}s",
                    )
            except Exception as e:
                logger.error("scheduler.tick_error", error=str(e))

            # Periodic maintenance (every ~10 ticks ≈ 10 minutes at 60s step)
            if int(time.time()) % (self.config.general.step * 10) < self.config.general.step:
                try:
                    await self.storage.rollup()
                    await self.storage.prune(self.config.storage.retention_days)
                    logger.debug("scheduler.maintenance_complete")
                except Exception as e:
                    logger.error("scheduler.maintenance_error", error=str(e))

            # Sleep until next tick
            elapsed = time.monotonic() - tick_start
            sleep_time = max(0, self.config.general.step - elapsed)
            await asyncio.sleep(sleep_time)

    async def _execute_tick(self) -> list[ProbeResult]:
        """Execute all probes for all targets."""
        results: list[ProbeResult] = []

        # Group targets by probe for batch execution (fping optimisation)
        probe_targets: dict[str, list[tuple[str, str]]] = defaultdict(list)
        # probe_name -> [(target_address, target_label), ...]

        for group in self._targets:
            probe_name = group.probe
            if probe_name not in self._probes:
                logger.warning("scheduler.unknown_probe", probe=probe_name, group=group.name)
                continue
            for host in group.hosts:
                probe_targets[probe_name].append((host.address, host.label))

        if self.config.general.concurrent_probes:
            # Run all probe groups concurrently
            tasks = []
            for probe_name, targets in probe_targets.items():
                tasks.append(self._run_probe_group(probe_name, targets))
            group_results = await asyncio.gather(*tasks, return_exceptions=True)
            for gr in group_results:
                if isinstance(gr, list):
                    results.extend(gr)
                else:
                    logger.error("scheduler.probe_group_error", error=str(gr))
        else:
            # Run sequentially
            for probe_name, targets in probe_targets.items():
                group_results = await self._run_probe_group(probe_name, targets)
                results.extend(group_results)

        return results

    async def _run_probe_group(
        self, probe_name: str, targets: list[tuple[str, str]]
    ) -> list[ProbeResult]:
        """Run a single probe against its targets."""
        probe = self._probes[probe_name]
        results: list[ProbeResult] = []

        # Special case: fping supports batch mode
        if probe_name == "fping" and len(targets) > 1:
            batch_results = await probe.run_batch([t[0] for t in targets])
            for addr, label in targets:
                if addr in batch_results:
                    r = batch_results[addr]
                    r.target = label or addr
                    results.append(r)
            return results

        # Run individual targets, optionally with concurrency limit
        if self.config.general.concurrent_probes:
            sem = asyncio.Semaphore(self.config.general.max_parallel)
            tasks = [self._run_single(sem, probe, addr, label) for addr, label in targets]
            results = await asyncio.gather(*tasks)
            return list(results)
        else:
            for addr, label in targets:
                r = await probe.run(addr)
                r.target = label or addr
                results.append(r)
            return results

    async def _run_single(
        self, sem: asyncio.Semaphore, probe: BaseProbe, address: str, label: str
    ) -> ProbeResult:
        """Run a single probe with semaphore limiting."""
        async with sem:
            result = await probe.run(address)
            result.target = label or address
            return result

    async def run_once(self) -> list[ProbeResult]:
        """Execute a single tick (for testing / CLI)."""
        results = await self._execute_tick()
        if results:
            await self.storage.store_results(results)
        return results
