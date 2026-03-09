"""HTTP lifecycle, middleware, and global exception hooks."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from host_brain.app.main import build_startup_report, emit_runtime_bootstrap_logs
from host_brain.app.schemas import ErrorResponse
from host_brain.core.ids import new_request_id


@asynccontextmanager
async def runtime_lifespan(
    *,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> AsyncGenerator[None, None]:
    logger.info("Natsume AI Host Brain starting up ...")
    ledger = get_ledger()
    try:
        report = build_startup_report(
            runtime_statuses={},
            dependency_probes={},
            request_id=new_request_id(),
            ledger=ledger,
            ts_ms=int(time.time() * 1000),
        )
        emit_runtime_bootstrap_logs(startup_report=report)
        logger.info("Startup probe completed", extra={"status": report.get("status", "UNKNOWN")})
    except Exception as exc:
        logger.warning("Startup probe degraded", extra={"error": str(exc)})
    yield
    logger.info("Natsume AI Host Brain shutting down ...")


async def global_exception_handler(
    *,
    request: Request,
    exc: Exception,
    logger: Any,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    if isinstance(exc, HTTPException):
        status_code = exc.status_code
        reason_code = f"HTTP_{status_code}"
        detail = exc.detail
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        reason_code = "INTERNAL_SERVER_ERROR"
        detail = str(exc) or "Internal server error"
    logger.error(
        "HTTP request failed",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "status_code": status_code,
            "reason_code": reason_code,
            "detail": detail,
        },
    )
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=exc.__class__.__name__,
            reason_code=reason_code,
            detail=detail,
            request_id=request_id,
            timestamp=int(time.time() * 1000),
        ).dict(),
    )


async def add_request_id_and_audit(
    *,
    request: Request,
    call_next: Callable,
    metrics: Any,
    logger: Any,
) -> Response:
    request_id = new_request_id()
    request.state.request_id = request_id
    start_time = time.time()
    logger.info(
        "HTTP request started",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "client": request.client.host if request.client else "unknown",
        },
    )
    try:
        metrics.inc_request()
        response = await call_next(request)
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "HTTP request completed",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
    except Exception as exc:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error(
            "HTTP request failed with exception",
            extra={
                "request_id": request_id,
                "path": request.url.path,
                "method": request.method,
                "duration_ms": duration_ms,
                "exception": str(exc),
            },
        )
        raise
