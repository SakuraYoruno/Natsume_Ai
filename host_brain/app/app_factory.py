from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from host_brain.app.http_hooks import add_request_id_and_audit as _add_request_id_fn
from host_brain.app.http_hooks import global_exception_handler as _global_exception_handler_fn
from host_brain.app.router_registry import register_host_routers, wire_route_providers
from host_brain.app.runtime_metrics import RuntimeMetrics, build_metrics_response
from host_brain.app.schemas import ChatRequest, EdgeEventsRequest, EdgeEventsResponse, MetricsResponse
from host_brain.app.server_config import configure_http_middleware


def create_host_fastapi_app(
    *,
    lifespan: Any,
    metrics: RuntimeMetrics,
    logger: logging.Logger,
    get_ledger: Callable[[], Any],
    get_tool_broker: Callable[[], Any],
    get_tool_catalog: Callable[[], Any],
    get_bond_engine: Callable[[], Any],
    chat_endpoint: Callable[[ChatRequest, Request], Awaitable[Response]],
    edge_events_endpoint: Callable[[EdgeEventsRequest], Awaitable[EdgeEventsResponse]],
    root_endpoint: Callable[[], Awaitable[Response]],
) -> FastAPI:
    wire_route_providers(
        get_ledger=get_ledger,
        get_tool_broker=get_tool_broker,
        get_tool_catalog=get_tool_catalog,
        get_bond_engine=get_bond_engine,
        logger=logger,
    )
    app = FastAPI(
        title="Natsume AI Host Brain",
        description="AI agent orchestration runtime with deterministic execution and audit trails",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    register_host_routers(app)
    configure_http_middleware(app)

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return await _global_exception_handler_fn(request=request, exc=exc, logger=logger)

    @app.middleware("http")
    async def add_request_id_and_audit(request: Request, call_next: Callable) -> Response:
        return await _add_request_id_fn(request=request, call_next=call_next, metrics=metrics, logger=logger)

    app.post("/v1/chat")(chat_endpoint)
    app.post("/v1/edge/events", response_model=EdgeEventsResponse)(edge_events_endpoint)

    @app.get("/metrics", response_model=MetricsResponse)
    async def metrics_endpoint() -> MetricsResponse:
        return build_metrics_response(metrics)

    app.get("/")(root_endpoint)
    return app


def docs_redirect_response() -> Response:
    return Response(status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers={"Location": "/docs"})


__all__ = ["create_host_fastapi_app", "docs_redirect_response"]
