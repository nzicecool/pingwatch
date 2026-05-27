"""Storage layer — SQLite backend for time-series measurements."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

from pingwatch.probes import ProbeResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    probe_type TEXT NOT NULL,
    group_name TEXT NOT NULL DEFAULT 'default',
    UNIQUE(name, address)
);

CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_name TEXT NOT NULL,
    probe_type TEXT NOT NULL,
    timestamp REAL NOT NULL,
    median_ms REAL,
    avg_ms REAL,
    min_ms REAL,
    max_ms REAL,
    loss_pct REAL NOT NULL DEFAULT 0,
    jitter_ms REAL NOT NULL DEFAULT 0,
    sent INTEGER NOT NULL DEFAULT 0,
    received INTEGER NOT NULL DEFAULT 0,
    latencies_json TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_measurements_target_ts
    ON measurements(target_name, timestamp);
CREATE INDEX IF NOT EXISTS idx_measurements_ts
    ON measurements(timestamp);

-- Rollup tables for aggregation
CREATE TABLE IF NOT EXISTS measurements_5min (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_name TEXT NOT NULL,
    probe_type TEXT NOT NULL,
    period_start REAL NOT NULL,
    median_ms REAL,
    avg_ms REAL,
    min_ms REAL,
    max_ms REAL,
    loss_pct REAL NOT NULL DEFAULT 0,
    jitter_ms REAL NOT NULL DEFAULT 0,
    sample_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_5min_target_ts
    ON measurements_5min(target_name, period_start);

CREATE TABLE IF NOT EXISTS measurements_1hour (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_name TEXT NOT NULL,
    probe_type TEXT NOT NULL,
    period_start REAL NOT NULL,
    median_ms REAL,
    avg_ms REAL,
    min_ms REAL,
    max_ms REAL,
    loss_pct REAL NOT NULL DEFAULT 0,
    jitter_ms REAL NOT NULL DEFAULT 0,
    sample_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_1hour_target_ts
    ON measurements_1hour(target_name, period_start);

CREATE TABLE IF NOT EXISTS measurements_1day (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_name TEXT NOT NULL,
    probe_type TEXT NOT NULL,
    period_start REAL NOT NULL,
    median_ms REAL,
    avg_ms REAL,
    min_ms REAL,
    max_ms REAL,
    loss_pct REAL NOT NULL DEFAULT 0,
    jitter_ms REAL NOT NULL DEFAULT 0,
    sample_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_1day_target_ts
    ON measurements_1day(target_name, period_start);
"""


class Storage:
    """Async SQLite storage for probe measurements."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open database connection and initialise schema."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        """Close database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage not connected. Call connect() first.")
        return self._db

    async def store_result(self, result: ProbeResult, group_name: str = "default") -> None:
        """Store a single probe result."""
        latencies_json = json.dumps(result.latencies)

        await self.db.execute(
            """
            INSERT INTO measurements
                (target_name, probe_type, timestamp, median_ms, avg_ms,
                 min_ms, max_ms, loss_pct, jitter_ms, sent, received,
                 latencies_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.target,
                result.probe_name,
                time.time(),
                result.median,
                result.avg,
                result.min,
                result.max,
                result.loss_pct,
                result.jitter,
                result.sent,
                result.received,
                latencies_json,
                result.error,
            ),
        )
        await self.db.commit()

    async def store_results(self, results: list[ProbeResult], group_name: str = "default") -> None:
        """Store multiple probe results in a single transaction."""
        now = time.time()
        rows = []
        for r in results:
            rows.append((
                r.target, r.probe_name, now, r.median, r.avg,
                r.min, r.max, r.loss_pct, r.jitter, r.sent,
                r.received, json.dumps(r.latencies), r.error,
            ))

        await self.db.executemany(
            """
            INSERT INTO measurements
                (target_name, probe_type, timestamp, median_ms, avg_ms,
                 min_ms, max_ms, loss_pct, jitter_ms, sent, received,
                 latencies_json, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await self.db.commit()

    async def get_measurements(
        self,
        target: str,
        since: float | None = None,
        until: float | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query measurements for a target."""
        query = "SELECT * FROM measurements WHERE target_name = ?"
        params: list[Any] = [target]

        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        if until:
            query += " AND timestamp <= ?"
            params.append(until)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_latest(self, target: str, count: int = 1) -> list[dict[str, Any]]:
        """Get latest N measurements for a target."""
        cursor = await self.db.execute(
            "SELECT * FROM measurements WHERE target_name = ? ORDER BY timestamp DESC LIMIT ?",
            (target, count),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_all_targets_summary(self, period_hours: int = 1) -> list[dict[str, Any]]:
        """Get summary stats for all targets in the given period."""
        since = time.time() - (period_hours * 3600)
        cursor = await self.db.execute(
            """
            SELECT
                target_name,
                probe_type,
                COUNT(*) as sample_count,
                AVG(median_ms) as avg_median,
                MIN(min_ms) as overall_min,
                MAX(max_ms) as overall_max,
                AVG(loss_pct) as avg_loss,
                AVG(jitter_ms) as avg_jitter
            FROM measurements
            WHERE timestamp >= ?
            GROUP BY target_name, probe_type
            ORDER BY avg_median DESC
            """,
            (since,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def rollup(self) -> None:
        """Aggregate raw measurements into rollup tables."""
        now = time.time()

        # 5-minute rollups
        await self._do_rollup("measurements_5min", 300, now)
        # 1-hour rollups
        await self._do_rollup("measurements_1hour", 3600, now)
        # 1-day rollups
        await self._do_rollup("measurements_1day", 86400, now)

        await self.db.commit()

    async def _do_rollup(self, table: str, period_seconds: int, now: float) -> None:
        """Aggregate raw measurements into a rollup table."""
        # Find the last rollup period
        cursor = await self.db.execute(f"SELECT MAX(period_start) FROM {table}")
        row = await cursor.fetchone()
        last_period = row[0] if row[0] else 0

        if last_period >= now - period_seconds:
            return  # Already rolled up recently

        # Aggregate since last rollup
        await self.db.execute(
            f"""
            INSERT INTO {table} (target_name, probe_type, period_start, median_ms, avg_ms,
                                 min_ms, max_ms, loss_pct, jitter_ms, sample_count)
            SELECT
                target_name,
                probe_type,
                (FLOOR(timestamp / {period_seconds}) * {period_seconds}) as period_start,
                AVG(median_ms),
                AVG(avg_ms),
                MIN(min_ms),
                MAX(max_ms),
                AVG(loss_pct),
                AVG(jitter_ms),
                COUNT(*)
            FROM measurements
            WHERE timestamp > ? AND timestamp <= ?
            GROUP BY target_name, probe_type, period_start
            ON CONFLICT DO NOTHING
            """,
            (last_period, now),
        )

    async def get_rollup(
        self,
        target: str,
        period: str = "5min",
        since: float | None = None,
        until: float | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Query rollup data for a target.

        Args:
            target: Target name.
            period: One of '5min', '1hour', '1day'.
            since: Start timestamp (epoch).
            until: End timestamp (epoch).
            limit: Max rows to return.
        """
        table_map = {
            "5min": "measurements_5min",
            "1hour": "measurements_1hour",
            "1day": "measurements_1day",
        }
        table = table_map.get(period)
        if not table:
            raise ValueError(f"Invalid period '{period}'. Use 5min, 1hour, or 1day.")

        query = f"SELECT * FROM {table} WHERE target_name = ?"
        params: list[Any] = [target]

        if since:
            query += " AND period_start >= ?"
            params.append(since)
        if until:
            query += " AND period_start <= ?"
            params.append(until)

        query += " ORDER BY period_start ASC LIMIT ?"
        params.append(limit)

        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_targets_list(self) -> list[dict[str, Any]]:
        """Get distinct target names with probe types and latest stats."""
        cursor = await self.db.execute(
            """
            SELECT
                m.target_name,
                m.probe_type,
                m.median_ms,
                m.loss_pct,
                m.jitter_ms,
                m.timestamp
            FROM measurements m
            INNER JOIN (
                SELECT target_name, MAX(timestamp) as max_ts
                FROM measurements
                GROUP BY target_name
            ) latest ON m.target_name = latest.target_name AND m.timestamp = latest.max_ts
            ORDER BY m.target_name
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_target_detail(self, target: str, period_hours: int = 24) -> dict[str, Any] | None:
        """Get detailed stats for a single target over a period."""
        since = time.time() - (period_hours * 3600)

        # Get summary
        cursor = await self.db.execute(
            """
            SELECT
                target_name,
                probe_type,
                COUNT(*) as sample_count,
                AVG(median_ms) as avg_median,
                MIN(min_ms) as overall_min,
                MAX(max_ms) as overall_max,
                AVG(loss_pct) as avg_loss,
                AVG(jitter_ms) as avg_jitter
            FROM measurements
            WHERE target_name = ? AND timestamp >= ?
            GROUP BY target_name, probe_type
            """,
            (target, since),
        )
        row = await cursor.fetchone()
        if not row:
            return None

        detail = dict(row)

        # Get recent measurements for trend
        cursor = await self.db.execute(
            """
            SELECT timestamp, median_ms, loss_pct, jitter_ms
            FROM measurements
            WHERE target_name = ? AND timestamp >= ?
            ORDER BY timestamp ASC
            LIMIT 100
            """,
            (target, since),
        )
        detail["measurements"] = [dict(r) for r in await cursor.fetchall()]

        return detail

    async def get_anomalies(self, period_hours: int = 1) -> list[dict[str, Any]]:
        """Find measurements that are > 2x stddev from the 7-day baseline."""
        since = time.time() - (period_hours * 3600)
        baseline_since = time.time() - (7 * 86400)

        # Compute baselines from last 7 days
        cursor = await self.db.execute(
            """
            SELECT
                target_name,
                AVG(median_ms) as baseline_median,
                AVG(loss_pct) as baseline_loss,
                AVG(jitter_ms) as baseline_jitter
            FROM measurements
            WHERE timestamp >= ?
            GROUP BY target_name
            """,
            (baseline_since,),
        )
        baselines = {r["target_name"]: dict(r) for r in await cursor.fetchall()}

        if not baselines:
            return []

        # Find recent measurements exceeding 2x baseline (simplified stddev)
        anomalies = []
        cursor = await self.db.execute(
            """
            SELECT target_name, probe_type, timestamp, median_ms, loss_pct, jitter_ms
            FROM measurements
            WHERE timestamp >= ?
            ORDER BY timestamp DESC
            LIMIT 500
            """,
            (since,),
        )
        recent = await cursor.fetchall()

        for row in recent:
            r = dict(row)
            baseline = baselines.get(r["target_name"])
            if not baseline:
                continue

            # Check if median is > 2x baseline
            bl_median = baseline.get("baseline_median") or 0
            if bl_median > 0 and r.get("median_ms") and r["median_ms"] > bl_median * 2:
                anomalies.append({
                    **r,
                    "baseline_median": round(bl_median, 2),
                    "type": "high_latency",
                })

            # Check if loss is > 2x baseline
            bl_loss = baseline.get("baseline_loss") or 0
            if r.get("loss_pct", 0) > max(bl_loss * 2, 10):  # Also flag > 10% loss
                anomalies.append({
                    **r,
                    "baseline_loss": round(bl_loss, 2),
                    "type": "high_loss",
                })

        return anomalies[:50]  # Cap results

    async def get_top_latency(self, period_hours: int = 1, limit: int = 10) -> list[dict[str, Any]]:
        """Get worst-performing targets by median latency."""
        since = time.time() - (period_hours * 3600)
        cursor = await self.db.execute(
            """
            SELECT
                target_name,
                probe_type,
                AVG(median_ms) as avg_median,
                MAX(max_ms) as peak,
                AVG(loss_pct) as avg_loss,
                AVG(jitter_ms) as avg_jitter,
                COUNT(*) as samples
            FROM measurements
            WHERE timestamp >= ?
            GROUP BY target_name, probe_type
            ORDER BY avg_median DESC
            LIMIT ?
            """,
            (since, limit),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_top_loss(self, period_hours: int = 1, limit: int = 10) -> list[dict[str, Any]]:
        """Get worst-performing targets by packet loss."""
        since = time.time() - (period_hours * 3600)
        cursor = await self.db.execute(
            """
            SELECT
                target_name,
                probe_type,
                AVG(median_ms) as avg_median,
                AVG(loss_pct) as avg_loss,
                MAX(loss_pct) as peak_loss,
                COUNT(*) as samples
            FROM measurements
            WHERE timestamp >= ?
            GROUP BY target_name, probe_type
            ORDER BY avg_loss DESC
            LIMIT ?
            """,
            (since, limit),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def prune(self, retention_days: int = 365) -> None:
        """Remove old measurements beyond retention period."""
        cutoff = time.time() - (retention_days * 86400)
        await self.db.execute("DELETE FROM measurements WHERE timestamp < ?", (cutoff,))
        await self.db.execute("DELETE FROM measurements_5min WHERE period_start < ?", (cutoff,))
        await self.db.execute("DELETE FROM measurements_1hour WHERE period_start < ?", (cutoff,))
        await self.db.execute("DELETE FROM measurements_1day WHERE period_start < ?", (cutoff,))
        await self.db.commit()
