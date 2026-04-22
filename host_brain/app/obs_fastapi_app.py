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
from fastapi.responses import JSONResponse, RedirectResponse

from host_brain.app.web_ui import router as web_ui_router
from host_brain.app.obs_routes import router as obs_router, set_ledger_provider as _set_obs_ledger_provider
from host_brain.app.obs_routes_trace import router as obs_trace_router
from host_brain.app.obs_routes_summary import (
    router as obs_summary_router,
    get_digital_life_overview_warm_status,
    get_insights_summary_warm_status,
    start_digital_life_overview_warm_cache,
    start_insights_summary_warm_cache,
    stop_digital_life_overview_warm_cache,
    stop_insights_summary_warm_cache,
)
from host_brain.app.obs_routes_life_body import (
    router as obs_life_body_router,
    get_life_body_warm_status,
    start_life_body_warm_cache,
    stop_life_body_warm_cache,
)
from host_brain.app.obs_routes_sysmode import (
    get_runtime_snapshot_warm_status,
    start_runtime_snapshot_warm_cache,
    stop_runtime_snapshot_warm_cache,
)
from host_brain.app.obs_routes_trace import (
    get_trace_feed_warm_status,
    start_trace_feed_warm_cache,
    stop_trace_feed_warm_cache,
)
from host_brain.app.dependencies import get_ledger as _get_ledger_dep, get_obs_metrics_collector
from host_brain.observability.routes import build_obs_metrics_response

_logger = logging.getLogger("Natsume.obs_app")


def _get_ledger_compat():
    return _get_ledger_dep()


_set_obs_ledger_provider(_get_ledger_compat)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    warm_status = start_runtime_snapshot_warm_cache()
    _logger.info("obs runtime snapshot warm cache startup: %s", warm_status)
    trace_warm_status = start_trace_feed_warm_cache()
    _logger.info("obs trace feed warm cache startup: %s", trace_warm_status)
    insights_warm_status = start_insights_summary_warm_cache()
    _logger.info("obs insights summary warm cache startup: %s", insights_warm_status)
    digital_life_warm_status = start_digital_life_overview_warm_cache()
    _logger.info("obs digital life overview warm cache startup: %s", digital_life_warm_status)
    life_body_warm_status = start_life_body_warm_cache()
    _logger.info("obs life body warm cache startup: %s", life_body_warm_status)
    try:
        yield
    finally:
        life_body_stop_status = stop_life_body_warm_cache()
        _logger.info("obs life body warm cache shutdown: %s", life_body_stop_status)
        digital_life_stop_status = stop_digital_life_overview_warm_cache()
        _logger.info("obs digital life overview warm cache shutdown: %s", digital_life_stop_status)
        insights_stop_status = stop_insights_summary_warm_cache()
        _logger.info("obs insights summary warm cache shutdown: %s", insights_stop_status)
        trace_stop_status = stop_trace_feed_warm_cache()
        _logger.info("obs trace feed warm cache shutdown: %s", trace_stop_status)
        stop_status = stop_runtime_snapshot_warm_cache()
        _logger.info("obs runtime snapshot warm cache shutdown: %s", stop_status)


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
app.include_router(obs_trace_router)
app.include_router(obs_summary_router)
app.include_router(obs_life_body_router)

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
            "detail": str(exc),
            "timestamp": int(time.time() * 1000),
        },
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    ledger = _get_ledger_compat()
    return {
        "status": "OK",
        "service": "obs",
        "ledger_available": ledger is not None,
        "runtime_snapshot_warm_cache": get_runtime_snapshot_warm_status(),
        "trace_feed_warm_cache": get_trace_feed_warm_status(),
        "insights_summary_warm_cache": get_insights_summary_warm_status(),
        "digital_life_overview_warm_cache": get_digital_life_overview_warm_status(),
        "life_body_warm_cache": get_life_body_warm_status(),
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


@app.get("/")
async def root():
    return RedirectResponse(url="/docs", status_code=307)
