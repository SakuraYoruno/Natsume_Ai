"""Dedicated FastAPI application for observability and monitor-facing APIs.

This app is intentionally kept separate from the primary chat/runtime app so
heavy monitor traffic can be routed to an independent process.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse, RedirectResponse

from host_brain.app.web_ui import router as web_ui_router
from host_brain.app.obs_routes import router as obs_router, set_ledger_provider as _set_obs_ledger_provider
from host_brain.app.obs_routes_health import router as obs_health_router
from host_brain.app.obs_routes_trace import router as obs_trace_router
from host_brain.app.obs_routes_summary import router as obs_summary_router
from host_brain.app.obs_routes_life_body import router as obs_life_body_router
from host_brain.app.obs_routes_digital_life_soak import router as obs_digital_life_soak_router
from host_brain.app.obs_routes_proactive_autonomy import router as obs_proactive_autonomy_router
from host_brain.composition.process_services import (
    get_ledger as _get_ledger_dep,
    get_obs_metrics_collector,
    get_query_projection_store,
)
from host_brain.app.schemas import ObservabilityRequest, ObservabilityResponse
from host_brain.observability.routes import build_obs_metrics_response
from host_brain.observability.operator_query_gateway import (
    OperatorQueryGateway,
    OperatorQueryMiddleware,
)
from host_brain.app.observability_runtime.projection_runtime import TRACE_KIND
from host_brain.app.observability_runtime.projection_runtime import projection_meta
from host_brain.app.observability_runtime.projection_daemon import (
    register_projection_job,
    start_projection_jobs,
    stop_projection_jobs,
)
from host_brain.app.observability_runtime.remaining_query_projection import (
    refresh_remaining_query_projections,
)
from host_brain.app.observability_runtime.live_source_projection import (
    run_operator_live_state_projection_job,
)
from host_brain.app.settings import get_persistence_settings
from host_brain.app.process_service_wiring import configure_process_service_ports
from host_brain.composition.canon import build_process_canon_container
from host_brain.composition.life import PROCESS_LIFE_PROVIDER, build_process_life_container
from host_brain.app.observability_runtime.health_device_projection import (
    HEALTH_MAX_AGE_MS,
    SERVICE_HEALTH_KEY,
    SERVICE_HEALTH_KIND,
)

_logger = logging.getLogger("Natsume.obs_app")

# The observability server is a standalone process composition root.  Uvicorn
# imports this module directly, without importing the primary Host ASGI app
# that normally binds these application ports.
configure_process_service_ports()


def _get_obs_ledger():
    return _get_ledger_dep()


_set_obs_ledger_provider(_get_obs_ledger)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ledger = _get_ledger_dep()
    settings = get_persistence_settings()
    canon = build_process_canon_container(
        ledger=ledger,
        canonical_db_path=str(getattr(ledger, "canonical_db_path", "") or ""),
        world_root=settings.world_canon_root.resolve(),
    )
    life = build_process_life_container(
        ledger=ledger,
        adopted_at_ms=int(time.time() * 1000),
    )
    PROCESS_LIFE_PROVIDER.install(life)
    _app.state.canon_container = canon
    _app.state.life_container = life
    register_projection_job(
        "remaining-operator-queries",
        lambda: refresh_remaining_query_projections(
            canon_service=canon.application,
        ),
    )
    register_projection_job(
        "operator-live-state",
        lambda: run_operator_live_state_projection_job(
            life_snapshot_reader=life.snapshot_reader,
        ),
    )
    projection_status = start_projection_jobs(interval_ms=5000)
    _logger.info("operator projection daemon startup: %s", projection_status)
    try:
        yield
    finally:
        stop_status = stop_projection_jobs()
        PROCESS_LIFE_PROVIDER.clear(life)
        _app.state.canon_container = None
        _app.state.life_container = None
        _logger.info("operator projection daemon shutdown: %s", stop_status)


app = FastAPI(
    title="Natsume AI Observability",
    description="Dedicated observability backend for the monitor UI",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.include_router(web_ui_router, prefix="/api")
app.include_router(obs_router)
app.include_router(obs_health_router)
app.include_router(obs_trace_router)
app.include_router(obs_summary_router)
app.include_router(obs_life_body_router)
app.include_router(obs_digital_life_soak_router)
app.include_router(obs_proactive_autonomy_router)

# Every query response crosses one server-owned auth/privacy boundary.  Keep
# the gateway inside ``app.state`` so deployments can replace credentials
# during composition without allowing individual route handlers to bypass it.
app.state.operator_query_gateway = OperatorQueryGateway.from_environment()
app.add_middleware(
    OperatorQueryMiddleware,
    gateway_provider=lambda: app.state.operator_query_gateway,
)

_cors_origins_raw = os.environ.get(
    "Natsume_CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000",
).strip()
if _cors_origins_raw:
    _cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)


@app.exception_handler(Exception)
async def global_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    _logger.exception("obs app unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "status": "ERROR",
            # Exception text may contain query parameters, private bodies or
            # credential material.  Keep it in server logs only.
            "detail": "OBS_INTERNAL_ERROR",
            "timestamp": int(time.time() * 1000),
        },
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    document = get_query_projection_store().get(SERVICE_HEALTH_KIND, SERVICE_HEALTH_KEY)
    return {
        "status": "OK",
        "service": "obs",
        "projection": projection_meta(
            SERVICE_HEALTH_KIND,
            SERVICE_HEALTH_KEY,
            document,
            max_age_ms=HEALTH_MAX_AGE_MS,
        ),
        "timestamp": int(time.time() * 1000),
    }


@app.get("/obs/metrics")
async def obs_metrics(request_id: str | None = None) -> dict[str, Any]:
    ts_ms = int(time.time() * 1000)
    payload = build_obs_metrics_response(
        collector=get_obs_metrics_collector(),
        request_id=request_id,
        ts_ms=ts_ms,
    )
    payload["timestamp"] = ts_ms
    return payload


@app.post("/observability/trace", response_model=ObservabilityResponse)
async def request_trace(request_body: ObservabilityRequest) -> ObservabilityResponse:
    ts_ms = int(time.time() * 1000)
    request_id = str(request_body.request_id or "").strip()
    if not request_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="request_id is required",
        )
    document = get_query_projection_store().get(TRACE_KIND, request_id)
    payload = dict(document.payload) if document is not None else {}
    timeline = payload.get("timeline")
    if not isinstance(timeline, list):
        timeline = [dict(row) for row in payload.get("nodes", []) if isinstance(row, dict)]
    sources = payload.get("sources")
    if not isinstance(sources, dict) or any(not isinstance(value, int) for value in sources.values()):
        source_counts: dict[str, int] = {}
        for row in timeline:
            source = str(row.get("source") or row.get("node_type") or row.get("node_id") or "trace")
            source_counts[source] = source_counts.get(source, 0) + 1
        sources = source_counts
    return ObservabilityResponse(
        request_id=request_id,
        trace_status=str(payload.get("trace_status") or payload.get("status") or "DEGRADED"),
        timeline_total=len(timeline),
        sources=sources,
        timeline=timeline,
        timestamp=ts_ms,
    )


@app.get("/")
async def root():
    return RedirectResponse(url="/docs", status_code=307)
