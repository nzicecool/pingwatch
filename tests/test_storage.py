"""Tests for SQLite storage."""

import time

import pytest

from pingwatch.probes import ProbeResult
from pingwatch.storage import Storage


class TestStorage:
    @pytest.mark.asyncio
    async def test_connect_creates_schema(self, tmp_db):
        storage = Storage(tmp_db)
        await storage.connect()
        # Check tables exist
        cursor = await storage.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = [row[0] for row in await cursor.fetchall()]
        assert "measurements" in tables
        assert "targets" in tables
        assert "measurements_5min" in tables
        await storage.close()

    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, storage):
        result = ProbeResult(
            target="1.1.1.1",
            probe_name="icmp",
            latencies=[10.5, 11.2, 12.0],
        )
        result.compute_stats()

        await storage.store_result(result)

        measurements = await storage.get_measurements("1.1.1.1")
        assert len(measurements) == 1
        m = measurements[0]
        assert m["target_name"] == "1.1.1.1"
        assert m["probe_type"] == "icmp"
        assert m["loss_pct"] == 0.0

    @pytest.mark.asyncio
    async def test_store_multiple(self, storage):
        results = [
            ProbeResult(target="1.1.1.1", probe_name="icmp", latencies=[10, 11]),
            ProbeResult(target="8.8.8.8", probe_name="icmp", latencies=[20, 21]),
        ]
        for r in results:
            r.compute_stats()

        await storage.store_results(results)

        m1 = await storage.get_measurements("1.1.1.1")
        m2 = await storage.get_measurements("8.8.8.8")
        assert len(m1) == 1
        assert len(m2) == 1

    @pytest.mark.asyncio
    async def test_get_latest(self, storage):
        for i in range(5):
            r = ProbeResult(
                target="test", probe_name="icmp",
                latencies=[float(i) * 10],
            )
            r.compute_stats()
            await storage.store_result(r)

        latest = await storage.get_latest("test", count=3)
        assert len(latest) == 3
        # Should be in DESC order by timestamp
        assert latest[0]["median_ms"] >= latest[1]["median_ms"]

    @pytest.mark.asyncio
    async def test_summary(self, storage):
        results = [
            ProbeResult(target="host1", probe_name="icmp", latencies=[10, 11]),
            ProbeResult(target="host2", probe_name="icmp", latencies=[50, 60]),
        ]
        for r in results:
            r.compute_stats()
        await storage.store_results(results)

        summary = await storage.get_all_targets_summary(period_hours=1)
        assert len(summary) == 2
        names = {s["target_name"] for s in summary}
        assert "host1" in names
        assert "host2" in names

    @pytest.mark.asyncio
    async def test_store_with_loss(self, storage):
        result = ProbeResult(
            target="lossy",
            probe_name="icmp",
            latencies=[10, None, 12, None, 14],
        )
        result.compute_stats()

        await storage.store_result(result)

        m = await storage.get_measurements("lossy")
        assert len(m) == 1
        assert m[0]["loss_pct"] == 40.0
        assert m[0]["sent"] == 5
        assert m[0]["received"] == 3

    @pytest.mark.asyncio
    async def test_prune(self, storage):
        # Insert an old measurement
        old_result = ProbeResult(
            target="old", probe_name="icmp", latencies=[10],
        )
        old_result.compute_stats()

        # Manually insert with old timestamp
        import json
        await storage.db.execute(
            """
            INSERT INTO measurements
                (target_name, probe_type, timestamp, median_ms, avg_ms,
                 min_ms, max_ms, loss_pct, jitter_ms, sent, received,
                 latencies_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("old", "icmp", time.time() - 400 * 86400, 10, 10, 10, 10,
             0, 0, 1, 1, json.dumps([10]), None),
        )
        await storage.db.commit()

        # Insert a recent one
        new_result = ProbeResult(target="new", probe_name="icmp", latencies=[10])
        new_result.compute_stats()
        await storage.store_result(new_result)

        # Prune with 365 day retention
        await storage.prune(365)

        # Old should be gone, new should remain
        old_m = await storage.get_measurements("old")
        new_m = await storage.get_measurements("new")
        assert len(old_m) == 0
        assert len(new_m) == 1
