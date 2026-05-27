"""PingWatch REST API — FastAPI application and routes."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

from pingwatch.storage import Storage

# Resolve templates directory (next to this package)
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


# --- Pydantic response models ---


class TargetInfo(BaseModel):
    target_name: str
    probe_type: str
    median_ms: float | None = None
    loss_pct: float = 0.0
    jitter_ms: float = 0.0
    timestamp: float | None = None


class TargetsResponse(BaseModel):
    targets: list[TargetInfo]
    count: int


class MeasurementPoint(BaseModel):
    id: int
    target_name: str
    probe_type: str
    timestamp: float
    median_ms: float | None = None
    avg_ms: float | None = None
    min_ms: float | None = None
    max_ms: float | None = None
    loss_pct: float = 0.0
    jitter_ms: float = 0.0
    sent: int = 0
    received: int = 0
    latencies_json: str | None = None
    error: str | None = None


class MeasurementsResponse(BaseModel):
    target: str
    count: int
    data: list[MeasurementPoint]


class RollupPoint(BaseModel):
    id: int
    target_name: str
    probe_type: str
    period_start: float
    median_ms: float | None = None
    avg_ms: float | None = None
    min_ms: float | None = None
    max_ms: float | None = None
    loss_pct: float = 0.0
    jitter_ms: float = 0.0
    sample_count: int = 0


class RollupResponse(BaseModel):
    target: str
    period: str
    count: int
    data: list[RollupPoint]


class TargetSummary(BaseModel):
    target_name: str
    probe_type: str
    sample_count: int
    avg_median: float | None = None
    overall_min: float | None = None
    overall_max: float | None = None
    avg_loss: float = 0.0
    avg_jitter: float = 0.0


class SummaryResponse(BaseModel):
    period_hours: int
    targets: list[TargetSummary]
    count: int


class HealthResponse(BaseModel):
    status: str
    storage: str
    uptime_seconds: float


# AI response models


class BriefingResponse(BaseModel):
    enabled: bool
    period: str
    briefing: str | None = None
    message: str | None = None


class AnalysisResponse(BaseModel):
    enabled: bool
    target: str
    period_hours: int
    analysis: str | None = None
    message: str | None = None


class ScoreResponse(BaseModel):
    enabled: bool
    score: float | None = None
    grade: str | None = None
    target_count: int | None = None
    breakdown: dict[str, float] | None = None
    message: str | None = None


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    enabled: bool
    question: str
    answer: str | None = None
    message: str | None = None


def create_app(storage: Storage, ai_config: dict[str, Any] | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        storage: Connected Storage instance.
        ai_config: AI configuration dict (from PingWatchConfig.ai).

    Returns:
        Configured FastAPI app.
    """
    app = FastAPI(
        title="PingWatch",
        description="Modern Smokeping-like network latency monitor",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    start_time = time.time()

    # Lazy-init briefer
    _briefer = None

    def get_briefer():
        nonlocal _briefer
        if _briefer is None and ai_config:
            from pingwatch.ai.briefer import NetworkBriefer
            _briefer = NetworkBriefer(storage, ai_config)
        return _briefer

    # --- Core API ---

    @app.get("/api/health", response_model=HealthResponse)
    async def health():
        """Service health check."""
        try:
            targets = await storage.get_targets_list()
            db_ok = True
        except Exception:
            db_ok = False
        return HealthResponse(
            status="ok" if db_ok else "degraded",
            storage="connected" if db_ok else "error",
            uptime_seconds=round(time.time() - start_time, 1),
        )

    @app.get("/api/targets", response_model=TargetsResponse)
    async def list_targets():
        """List all targets with latest stats."""
        targets = await storage.get_targets_list()
        return TargetsResponse(
            targets=[TargetInfo(**t) for t in targets],
            count=len(targets),
        )

    @app.get("/api/targets/{name}/measurements", response_model=MeasurementsResponse)
    async def get_measurements(
        name: str,
        since: float | None = Query(None, description="Start timestamp (epoch)"),
        until: float | None = Query(None, description="End timestamp (epoch)"),
        limit: int = Query(1000, ge=1, le=10000, description="Max rows"),
    ):
        """Raw measurements for a target."""
        data = await storage.get_measurements(name, since=since, until=until, limit=limit)
        if not data and not since:
            # Check if target exists at all
            all_targets = await storage.get_targets_list()
            if not any(t["target_name"] == name for t in all_targets):
                raise HTTPException(status_code=404, detail=f"Target '{name}' not found")
        return MeasurementsResponse(
            target=name,
            count=len(data),
            data=[MeasurementPoint(**d) for d in data],
        )

    @app.get("/api/targets/{name}/rollup", response_model=RollupResponse)
    async def get_rollup(
        name: str,
        period: str = Query("5min", pattern=r"^(5min|1hour|1day)$"),
        since: float | None = Query(None, description="Start timestamp (epoch)"),
        until: float | None = Query(None, description="End timestamp (epoch)"),
    ):
        """Aggregated rollup data for a target."""
        try:
            data = await storage.get_rollup(name, period=period, since=since, until=until)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not data and not since:
            all_targets = await storage.get_targets_list()
            if not any(t["target_name"] == name for t in all_targets):
                raise HTTPException(status_code=404, detail=f"Target '{name}' not found")
        return RollupResponse(
            target=name,
            period=period,
            count=len(data),
            data=[RollupPoint(**d) for d in data],
        )

    @app.get("/api/summary", response_model=SummaryResponse)
    async def summary(period_hours: int = Query(1, ge=1, le=720, description="Hours to summarise")):
        """All targets summary for a time period."""
        targets = await storage.get_all_targets_summary(period_hours=period_hours)
        return SummaryResponse(
            period_hours=period_hours,
            targets=[TargetSummary(**t) for t in targets],
            count=len(targets),
        )

    # --- AI API ---

    @app.get("/api/ai/briefing", response_model=BriefingResponse)
    async def ai_briefing(period: str = Query("1h", description="Period: 1h, 6h, 24h, 7d")):
        """Generate a network health briefing using AI."""
        briefer = get_briefer()
        if not briefer or not briefer.enabled:
            return BriefingResponse(enabled=False, period=period, message="AI not configured")

        period_map = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}
        hours = period_map.get(period, 1)
        briefing = await briefer.generate_briefing(hours)
        return BriefingResponse(enabled=True, period=period, briefing=briefing)

    @app.get("/api/ai/analyze/{target}", response_model=AnalysisResponse)
    async def ai_analyze(target: str, period_hours: int = Query(24, ge=1, le=168)):
        """Deep analysis of a specific target using AI."""
        briefer = get_briefer()
        if not briefer or not briefer.enabled:
            return AnalysisResponse(enabled=False, target=target, period_hours=period_hours, message="AI not configured")

        analysis = await briefer.analyze_target(target, period_hours)
        return AnalysisResponse(enabled=True, target=target, period_hours=period_hours, analysis=analysis)

    @app.get("/api/ai/score", response_model=ScoreResponse)
    async def ai_score(period_hours: int = Query(1, ge=1, le=168)):
        """Network health score (0-100). Deterministic, no LLM needed."""
        briefer = get_briefer()
        if not briefer:
            return ScoreResponse(enabled=False, message="AI not configured")

        result = await briefer.compute_network_score(period_hours)
        return ScoreResponse(enabled=True, **result)

    @app.post("/api/ai/ask", response_model=AskResponse)
    async def ai_ask(request: AskRequest):
        """Free-form Q&A about network state."""
        briefer = get_briefer()
        if not briefer or not briefer.enabled:
            return AskResponse(enabled=False, question=request.question, message="AI not configured")

        answer = await briefer.ask(request.question)
        return AskResponse(enabled=True, question=request.question, answer=answer)

    # --- Dashboard ---

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        """Serve the smoke-style dashboard."""
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        template = env.get_template("dashboard.html")
        return HTMLResponse(template.render())

    return app
