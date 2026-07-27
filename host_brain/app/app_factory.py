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
from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.host_obs_read_routes import HostObsReadPorts
from host_brain.app.health_routes import DegradationStateProvider
from host_brain.app.startup import collect_dependency_probes
from host_brain.app.pentest_secure_run_routes import PentestSecureRunCompositionProvider
from host_brain.app.router_registry import register_host_routers
from host_brain.app.runtime_metrics import RuntimeMetrics, build_metrics_response
from host_brain.app.schemas import ChatRequest, EdgeEventsRequest, EdgeEventsResponse, MetricsResponse
from host_brain.app.server_config import configure_http_middleware
from host_brain.observability.command_http import ObsCommandIngressProvider
from host_brain.skills.signed_intake_http import SignedSkillProviderIntakeProvider


def _read_agent_workspace_cleanup_health(application: FastAPI) -> dict[str, Any]:
    container = getattr(application.state, "agent_workspace_container", None)
    shutdown_error = getattr(
        application.state,
        "agent_workspace_shutdown_error",
        None,
    )
    retry_required = bool(
        isinstance(shutdown_error, dict)
        and shutdown_error.get("retry_required")
    )
    if container is None:
        return {
            "status": "RETRY_REQUIRED" if retry_required else "NOT_CONFIGURED",
            "configured": retry_required,
            "running": False,
            "reason_code": (
                str(shutdown_error.get("reason_code") or "").strip()
                if isinstance(shutdown_error, dict)
                else None
            ),
            "shutdown_retry_required": retry_required,
        }
    worker = getattr(container, "cleanup_worker", None)
    snapshot_reader = getattr(worker, "health_snapshot", None)
    if not callable(snapshot_reader):
        return {
            "status": "UNAVAILABLE",
            "configured": True,
            "running": False,
            "reason_code": "AGENT_WORKSPACE_CLEANUP_HEALTH_UNAVAILABLE",
            "shutdown_retry_required": retry_required,
        }
    try:
        raw = dict(snapshot_reader())
    except Exception:
        return {
            "status": "UNAVAILABLE",
            "configured": True,
            "running": False,
            "reason_code": "AGENT_WORKSPACE_CLEANUP_HEALTH_READ_FAILED",
            "shutdown_retry_required": retry_required,
        }
    last_report = raw.get("last_report")
    last_failure = raw.get("last_failure")
    return {
        "status": "RETRY_REQUIRED" if retry_required else raw.get("status"),
        "configured": True,
        "running": bool(raw.get("running", False)),
        "consecutive_failures": raw.get("consecutive_failures", 0),
        "last_run_ts_ms": raw.get("last_run_ts_ms"),
        "last_success_ts_ms": raw.get("last_success_ts_ms"),
        "last_failure": (
            {
                "ts_ms": last_failure.get("ts_ms"),
                "error_type": last_failure.get("error_type"),
            }
            if isinstance(last_failure, dict)
            else None
        ),
        "last_report_count": (
            len(last_report)
            if isinstance(last_report, (list, tuple))
            else 0
        ),
        "guardian_projection_status": (
            str(raw.get("guardian_projection_status") or "UNBOUND")
            .strip()
            .upper()
        ),
        "guardian_projection_error_type": raw.get(
            "guardian_projection_error_type",
        ),
        "reason_code": (
            str(shutdown_error.get("reason_code") or "").strip()
            if retry_required and isinstance(shutdown_error, dict)
            else None
        ),
        "shutdown_retry_required": retry_required,
    }


def create_host_fastapi_app(
    *,
    lifespan: Any,
    metrics: RuntimeMetrics,
    logger: logging.Logger,
    get_ledger: Callable[[], Any],
    get_tool_broker: Callable[[], Any],
    get_tool_catalog: Callable[[], Any],
    get_bond_engine: Callable[[], Any],
    get_bond_writer: Callable[[], Any] | None = None,
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
    scheduler_admin_runtime_provider: Callable[[], Any | None] | None = None,
    host_obs_read_ports: HostObsReadPorts | None = None,
    degradation_state_provider: DegradationStateProvider | None = None,
    degradation_capability_port_provider: Callable[[], Any | None] | None = None,
    canon_service_provider: Callable[[], Any | None] | None = None,
    route_ports: HostRoutePorts | None = None,
    obs_route_ports: ObsRoutePorts,
    input_runtime_ports: InputRuntimeApplicationPorts,
) -> FastAPI:
    effective_route_ports = route_ports or HostRoutePorts(
        ledger_provider=get_ledger,
        canon_service_provider=canon_service_provider,
        tool_broker_provider=get_tool_broker,
        tool_catalog_provider=get_tool_catalog,
        bond_engine_provider=get_bond_engine,
        bond_writer_provider=get_bond_writer,
    )
    app = FastAPI(
        title="Natsume AI Host Brain",
        description="AI agent orchestration runtime with deterministic execution and audit trails",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    if not isinstance(obs_route_ports, ObsRoutePorts):
        raise TypeError("obs_route_ports must be ObsRoutePorts")
    workspace_cleanup_health_provider = (
        lambda: _read_agent_workspace_cleanup_health(app)
    )
    app.state.obs_route_ports = obs_route_ports
    app.state.health_ledger_provider = get_ledger
    app.state.health_dependency_probe_provider = collect_dependency_probes
    app.state.health_degradation_state_provider = degradation_state_provider
    app.state.health_workspace_cleanup_provider = (
        workspace_cleanup_health_provider
    )
    register_host_routers(
        app,
        input_runtime_ports=input_runtime_ports,
        route_ports=effective_route_ports,
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
        scheduler_admin_runtime_provider=scheduler_admin_runtime_provider,
        host_obs_read_ports=host_obs_read_ports,
        degradation_state_provider=degradation_state_provider,
        health_ledger_provider=get_ledger,
        health_dependency_probe_provider=collect_dependency_probes,
        workspace_cleanup_health_provider=workspace_cleanup_health_provider,
        degradation_capability_port_provider=(
            degradation_capability_port_provider
        ),
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
