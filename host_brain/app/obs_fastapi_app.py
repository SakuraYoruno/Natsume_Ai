"""Dedicated FastAPI application for observability and monitor-facing APIs.

This app is intentionally kept separate from the primary chat/runtime app so
heavy monitor traffic can be routed to an independent process.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse, RedirectResponse

from host_brain.app.web_ui import router as web_ui_router
from host_brain.app.obs_routes import router as obs_router
from host_brain.app.obs_routes_health import router as obs_health_router
from host_brain.app.obs_routes_trace import router as obs_trace_router
from host_brain.app.obs_routes_summary import router as obs_summary_router
from host_brain.app.obs_routes_life_body import router as obs_life_body_router
from host_brain.app.obs_routes_digital_life_soak import router as obs_digital_life_soak_router
from host_brain.app.obs_routes_proactive_autonomy import router as obs_proactive_autonomy_router
from host_brain.app.schemas import ObservabilityRequest, ObservabilityResponse
from host_brain.app.obs_route_ports import (
    ObsRoutePorts,
    require_obs_query_projection_store,
)
from host_brain.observability.routes import build_obs_metrics_response
from host_brain.observability.operator_query_gateway import (
    OperatorQueryGateway,
    OperatorQueryMiddleware,
)
from host_brain.app.observability_runtime.projection_runtime import TRACE_KIND
from host_brain.app.observability_runtime.projection_runtime import projection_meta
from host_brain.app.settings import get_persistence_settings
from host_brain.app.settings.core import get_core_settings
from host_brain.app.obs_application import (
    ObsApplicationContainer,
    ObsApplicationPorts,
    build_default_obs_application_ports,
)
from host_brain.app.obs_projection_jobs import (
    AUTHENTICATED_HOST_READ,
    TYPED_UNAVAILABLE,
)
from host_brain.persistence.effect_reconciliation_query_repository import (
    EffectReconciliationQueryRepository,
)
from host_brain.persistence.terminal_reconciliation_query_repository import (
    TerminalReconciliationQueryRepository,
)
from host_brain.persistence.reconciliation_proof_verifier import (
    Ed25519ReconciliationProofVerifier,
)
from host_brain.app.observability_runtime.health_device_projection import (
    HEALTH_MAX_AGE_MS,
    SERVICE_HEALTH_KEY,
    SERVICE_HEALTH_KIND,
)

_logger = logging.getLogger("Natsume.obs_app")


def _build_effect_reconciliation_read_authority(
    canonical_db_path: str,
) -> EffectReconciliationQueryRepository | None:
    core_settings = get_core_settings()
    if not core_settings.nle_operator_views_enabled:
        return None
    reconciliation_public_keys = core_settings.task_reconciliation_public_keys()
    return EffectReconciliationQueryRepository(
        canonical_db_path,
        reconciliation_proof_verifier=(
            Ed25519ReconciliationProofVerifier(reconciliation_public_keys)
            if reconciliation_public_keys
            else None
        ),
    )


def _build_terminal_reconciliation_read_authority(
    canonical_db_path: str,
) -> TerminalReconciliationQueryRepository | None:
    core_settings = get_core_settings()
    if not core_settings.nle_operator_views_enabled:
        return None
    reconciliation_public_keys = core_settings.task_reconciliation_public_keys()
    return TerminalReconciliationQueryRepository(
        canonical_db_path,
        reconciliation_proof_verifier=(
            Ed25519ReconciliationProofVerifier(reconciliation_public_keys)
            if reconciliation_public_keys
            else None
        ),
    )


def _default_ports_factory() -> ObsApplicationPorts:
    settings = get_persistence_settings()
    return build_default_obs_application_ports(
        canonical_db_path=settings.canonical_db_path,
    )


def _require_obs_container(request: Request) -> ObsApplicationContainer:
    container = getattr(request.app.state, "obs_application_container", None)
    if not isinstance(container, ObsApplicationContainer) or container.closed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OBS_APPLICATION_NOT_READY",
        )
    return container


async def request_trace(
    request_body: ObservabilityRequest,
    request: Request,
) -> ObservabilityResponse:
    """Read one trace through the application-local projection-store port."""

    store = require_obs_query_projection_store(request)
    ts_ms = int(time.time() * 1000)
    request_id = str(request_body.request_id or "").strip()
    if not request_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="request_id is required",
        )
    document = store.get(TRACE_KIND, request_id)
    payload = dict(document.payload) if document is not None else {}
    timeline = payload.get("timeline")
    if not isinstance(timeline, list):
        timeline = [
            dict(row)
            for row in payload.get("nodes", [])
            if isinstance(row, dict)
        ]
    sources = payload.get("sources")
    if not isinstance(sources, dict) or any(
        not isinstance(value, int) for value in sources.values()
    ):
        source_counts: dict[str, int] = {}
        for row in timeline:
            source = str(
                row.get("source")
                or row.get("node_type")
                or row.get("node_id")
                or "trace"
            )
            source_counts[source] = source_counts.get(source, 0) + 1
        sources = source_counts
    return ObservabilityResponse(
        request_id=request_id,
        trace_status=str(
            payload.get("trace_status") or payload.get("status") or "DEGRADED"
        ),
        timeline_total=len(timeline),
        sources=sources,
        timeline=timeline,
        timestamp=ts_ms,
    )


def create_obs_app(
    *,
    ports: ObsApplicationPorts | None = None,
    ports_factory: Callable[[], ObsApplicationPorts] | None = None,
) -> FastAPI:
    """Construct one standalone Obs app from explicit query/projection ports."""

    if ports is not None and ports_factory is not None:
        raise ValueError("provide ports or ports_factory, not both")
    resolve_ports = ports_factory or ((lambda: ports) if ports is not None else _default_ports_factory)
    def clear_application_state(application: FastAPI) -> None:
        application.state.obs_application_container = None
        application.state.obs_route_ports = None
        application.state.life_snapshot_reader = None
        application.state.task_effect_reconciliation_read_authority = None
        application.state.task_terminal_reconciliation_read_authority = None

    def reserve_ports(candidate: ObsApplicationPorts) -> None:
        """Make every mutable application-port bundle single-use.

        A factory may create a fresh bundle for every lifespan.  Reusing the
        same bundle after its ledger has been closed would otherwise appear to
        start successfully and fail later on the first query or projection.
        """

        candidate.claim_lifecycle_owner()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        retained_container = getattr(
            application.state,
            "obs_application_container",
            None,
        )
        retained_route_ports = getattr(application.state, "obs_route_ports", None)
        if retained_container is not None or retained_route_ports is not None:
            if (
                isinstance(retained_container, ObsApplicationContainer)
                and retained_container.closed
            ):
                # A caller successfully retried close through the retained
                # handle.  Drop the stale references before composing a fresh
                # factory-owned object graph.
                clear_application_state(application)
            else:
                # Never overwrite a running app or a failed-close retry
                # handle with another lifespan's object graph.
                raise RuntimeError("OBS_APPLICATION_LIFECYCLE_RETRY_REQUIRED")

        resolved_ports: ObsApplicationPorts | None = None
        container: ObsApplicationContainer | None = None
        resources_closed = False
        try:
            candidate = resolve_ports()
            if not isinstance(candidate, ObsApplicationPorts):
                raise TypeError("Obs ports factory must return ObsApplicationPorts")
            reserve_ports(candidate)
            resolved_ports = candidate
            container = ObsApplicationContainer.compose(resolved_ports)
            route_ports = ObsRoutePorts(
                query_projection_store_provider=(
                    lambda: resolved_ports.query_projection_store
                ),
                life_action_context_provider=(
                    resolved_ports.life_action_context_provider
                ),
                approval_token_validator=resolved_ports.approval_token_validator,
            )
            # Publish the minimum retryable graph before constructing optional
            # readers.  Routes cannot run until lifespan yields, while a
            # startup rollback whose close also fails must retain the exact
            # container and resource bundle for an operator/lifecycle retry.
            application.state.obs_application_container = container
            application.state.obs_route_ports = route_ports
            application.state.life_snapshot_reader = resolved_ports.life_snapshot_reader
            application.state.task_effect_reconciliation_read_authority = None
            application.state.task_terminal_reconciliation_read_authority = None
            canonical_db_path = str(
                getattr(resolved_ports.ledger_query, "canonical_db_path", "") or ""
            ).strip()
            effect_reader = _build_effect_reconciliation_read_authority(
                canonical_db_path
            )
            application.state.task_effect_reconciliation_read_authority = effect_reader
            terminal_reader = _build_terminal_reconciliation_read_authority(
                canonical_db_path
            )
            application.state.task_terminal_reconciliation_read_authority = (
                terminal_reader
            )
            projection_status = container.start_projection_jobs(
                interval_ms=5_000
            )
            _logger.info("operator projection daemon startup: %s", projection_status)
            yield
        finally:
            try:
                if container is not None:
                    stop_status = container.close()
                    resources_closed = True
                    _logger.info(
                        "operator projection daemon shutdown: %s",
                        stop_status,
                    )
                elif resolved_ports is not None:
                    close = resolved_ports.close_owned_resources
                    if close is not None:
                        close()
                    resources_closed = True
                else:
                    resources_closed = True
            finally:
                # A stop timeout means callbacks may still own the SQLite
                # source and leader lease. Keep the exact object graph
                # reachable so a lifecycle/operator retry can close it.
                if resources_closed:
                    clear_application_state(application)

    application = FastAPI(
        title="Natsume AI Observability",
        description="Dedicated observability backend for the monitor UI",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    application.state.obs_application_container = None
    application.state.obs_route_ports = None
    application.state.life_snapshot_reader = None
    application.state.task_effect_reconciliation_read_authority = None
    application.state.task_terminal_reconciliation_read_authority = None

    application.include_router(web_ui_router, prefix="/api")
    application.include_router(obs_router)
    application.include_router(obs_health_router)
    application.include_router(obs_trace_router)
    application.include_router(obs_summary_router)
    application.include_router(obs_life_body_router)
    application.include_router(obs_digital_life_soak_router)
    application.include_router(obs_proactive_autonomy_router)

    # Every query response crosses one server-owned auth/privacy boundary.
    # This credential authority belongs to this app instance, not the module.
    application.state.operator_query_gateway = OperatorQueryGateway.from_environment()
    application.add_middleware(
        OperatorQueryMiddleware,
        gateway_provider=lambda: application.state.operator_query_gateway,
    )

    cors_origins_raw = os.environ.get(
        "Natsume_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000,http://127.0.0.1:8000",
    ).strip()
    if cors_origins_raw:
        cors_origins = [
            origin.strip()
            for origin in cors_origins_raw.split(",")
            if origin.strip()
        ]
        application.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

    @application.exception_handler(Exception)
    async def global_exception_handler(
        _request: Request,
        exc: Exception,
    ) -> JSONResponse:
        _logger.exception("obs app unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "status": "ERROR",
                "detail": "OBS_INTERNAL_ERROR",
                "timestamp": int(time.time() * 1000),
            },
        )

    @application.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        container = _require_obs_container(request)
        document = container.ports.query_projection_store.get(
            SERVICE_HEALTH_KIND,
            SERVICE_HEALTH_KEY,
        )
        missing_jobs = list(container.ports.missing_projection_jobs)
        unavailable_jobs = [
            {
                "name": job.name,
                "reason_code": job.unavailable_reason_code,
            }
            for job in container.ports.projection_jobs
            if job.source_mode == TYPED_UNAVAILABLE
        ]
        partial_jobs = [
            {
                "name": job.name,
                "source_mode": job.source_mode,
            }
            for job in container.ports.projection_jobs
            if str(job.source_mode).upper().endswith("_PARTIAL")
        ]
        authenticated_host_read_jobs = [
            {
                "name": job.name,
                "source_mode": job.source_mode,
            }
            for job in container.ports.projection_jobs
            if job.source_mode == AUTHENTICATED_HOST_READ
        ]
        declared_non_typed_unavailable_jobs = [
            job.name
            for job in container.ports.projection_jobs
            if job.source_mode != TYPED_UNAVAILABLE
        ]
        return {
            "status": (
                "DEGRADED"
                if missing_jobs or unavailable_jobs or partial_jobs
                else "OK"
            ),
            "service": "obs",
            "composition": {
                "projection_coverage": container.ports.projection_coverage,
                "count_semantics": (
                    "producer_registration_and_declared_source_mode_"
                    "not_runtime_success"
                ),
                "registered_jobs": [
                    job.name for job in container.ports.projection_jobs
                ],
                "registered_count": len(container.ports.projection_jobs),
                "declared_non_typed_unavailable_jobs": (
                    declared_non_typed_unavailable_jobs
                ),
                "declared_non_typed_unavailable_count": len(
                    declared_non_typed_unavailable_jobs
                ),
                "typed_unavailable_job_count": len(unavailable_jobs),
                "declared_partial_job_count": len(partial_jobs),
                "authenticated_host_read_jobs": authenticated_host_read_jobs,
                "authenticated_host_read_job_count": len(
                    authenticated_host_read_jobs
                ),
                "missing_jobs": missing_jobs,
                "unavailable_jobs": unavailable_jobs,
                "partial_jobs": partial_jobs,
                "source_modes": {
                    job.name: job.source_mode
                    for job in container.ports.projection_jobs
                },
            },
            "projection": projection_meta(
                SERVICE_HEALTH_KIND,
                SERVICE_HEALTH_KEY,
                document,
                max_age_ms=HEALTH_MAX_AGE_MS,
            ),
            "timestamp": int(time.time() * 1000),
        }

    @application.get("/obs/metrics")
    async def obs_metrics(
        request: Request,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        container = _require_obs_container(request)
        ts_ms = int(time.time() * 1000)
        payload = build_obs_metrics_response(
            collector=container.ports.metrics_reader,
            request_id=request_id,
            ts_ms=ts_ms,
        )
        payload["timestamp"] = ts_ms
        return payload

    application.add_api_route(
        "/observability/trace",
        request_trace,
        methods=["POST"],
        response_model=ObservabilityResponse,
    )

    @application.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs", status_code=307)

    return application


app = create_obs_app()


__all__ = ["app", "create_obs_app", "request_trace"]
