from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from host_brain.app.http_hooks import add_request_id_and_audit as _add_request_id_fn
from host_brain.app.http_hooks import global_exception_handler as _global_exception_handler_fn
from host_brain.input_runtime.contracts_and_normalization import GovernedCommandApplicationProvider
from host_brain.input_runtime.ports import InputRuntimeApplicationPorts
from host_brain.app.pentest_secure_run_routes import PentestSecureRunCompositionProvider
from host_brain.app.router_registry import register_host_routers, wire_route_providers
from host_brain.app.runtime_metrics import RuntimeMetrics, build_metrics_response
from host_brain.app.schemas import ChatRequest, EdgeEventsRequest, EdgeEventsResponse, MetricsResponse
from host_brain.app.server_config import configure_http_middleware
from host_brain.observability.command_http import ObsCommandIngressProvider
from host_brain.skills.signed_intake_http import SignedSkillProviderIntakeProvider


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
    obs_command_ingress_provider: ObsCommandIngressProvider | None = None,
    pentest_secure_run_composition_provider: (
        PentestSecureRunCompositionProvider | None
    ) = None,
    signed_skill_provider_intake_provider: (
        SignedSkillProviderIntakeProvider | None
    ) = None,
    governed_command_application_provider: (
        GovernedCommandApplicationProvider | None
    ) = None,
    internal_agent_ingress_provider: Callable[[], Any | None] | None = None,
    internal_agent_command_service_provider: Callable[[], Any | None] | None = None,
    scheduler_command_ingress_provider: Callable[[], Any | None] | None = None,
    scheduler_command_service_provider: Callable[[], Any | None] | None = None,
    canon_service_provider: Callable[[], Any | None] | None = None,
    input_runtime_ports: InputRuntimeApplicationPorts,
) -> FastAPI:
    wire_route_providers(
        get_ledger=get_ledger,
        get_tool_broker=get_tool_broker,
        get_tool_catalog=get_tool_catalog,
        get_bond_engine=get_bond_engine,
        logger=logger,
        canon_service_provider=canon_service_provider,
    )
    app = FastAPI(
        title="Natsume AI Host Brain",
        description="AI agent orchestration runtime with deterministic execution and audit trails",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    register_host_routers(
        app,
        input_runtime_ports=input_runtime_ports,
        obs_command_ingress_provider=obs_command_ingress_provider,
        pentest_secure_run_composition_provider=(
            pentest_secure_run_composition_provider
        ),
        signed_skill_provider_intake_provider=(
            signed_skill_provider_intake_provider
        ),
        governed_command_application_provider=(
            governed_command_application_provider
        ),
        internal_agent_ingress_provider=internal_agent_ingress_provider,
        internal_agent_command_service_provider=(
            internal_agent_command_service_provider
        ),
        scheduler_command_ingress_provider=scheduler_command_ingress_provider,
        scheduler_command_service_provider=scheduler_command_service_provider,
    )
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
