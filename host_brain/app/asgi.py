"""ASGI entrypoint for the host main service."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from host_brain.app.app_factory import create_host_fastapi_app, docs_redirect_response
from host_brain.app.chat_handler import handle_chat
from host_brain.app.chat_immune_gate import evaluate_immune_gate_for_chat, schedule_chat_immune_gate_event
from host_brain.app.chat_routes import handle_v1_chat_route
from host_brain.app.dependencies import (
    get_ledger,
    get_skill_registry,
    get_token_service,
    get_tool_broker,
    get_tool_catalog,
)
from host_brain.app.edge_event_routes import handle_edge_events_route
from host_brain.app.http_hooks import runtime_lifespan
from host_brain.app.runtime_metrics import RuntimeMetrics
from host_brain.app.schemas import ChatRequest, EdgeEventsRequest, EdgeEventsResponse
from host_brain.app.governed_task_adapter import (
    GovernedHostRuntime,
    build_governed_host_runtime,
)
from host_brain.app.settings import get_core_settings
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.composition.security_services import (
    ProcessSecurityServiceContainer,
    SecurityServiceContainerCloseError,
    build_process_security_service_container,
)
from host_brain.composition.task_runtime import (
    ProcessTaskRuntimeContainer,
    TaskRuntimeContainerCloseError,
    build_process_task_runtime_container,
)


logger = logging.getLogger("Natsume.asgi")
runtime_metrics = RuntimeMetrics()


def resolve_bond_engine():
    from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

    return get_social_brain_agent().resolve_stage_engine()


def schedule_immune_gate_audit(
    *,
    request_id: str,
    request_body: ChatRequest,
    immune_result: dict,
) -> None:
    schedule_chat_immune_gate_event(
        request_id=request_id,
        request_body=request_body,
        immune_result=immune_result,
        get_ledger=get_ledger,
        logger=logger,
    )


async def edge_events_endpoint(request_body: EdgeEventsRequest) -> EdgeEventsResponse:
    return await handle_edge_events_route(
        request_body=request_body,
        get_ledger=get_ledger,
        logger=logger,
    )


async def root_endpoint() -> Response:
    return docs_redirect_response()


def create_app() -> FastAPI:
    # This slot belongs to one FastAPI application factory invocation.  Route
    # providers close over it explicitly; there is no module-global service
    # locator and no app-state lookup inside the API registry.
    security_container: ProcessSecurityServiceContainer | None = None
    task_runtime_container: ProcessTaskRuntimeContainer | None = None
    governed_host_runtime: GovernedHostRuntime | None = None

    def task_runtime_container_provider() -> ProcessTaskRuntimeContainer | None:
        return task_runtime_container

    def governed_command_application_provider() -> GovernedHostRuntime | None:
        return governed_host_runtime

    async def handle_chat_with_runtime(
        request_body: ChatRequest,
        request: Request,
        metrics: RuntimeMetrics,
    ) -> StreamingResponse:
        return await handle_chat(
            request_body,
            request,
            metrics,
            governed_command_application=governed_command_application_provider(),
        )

    async def chat_endpoint_with_runtime(
        request_body: ChatRequest,
        request: Request,
    ) -> StreamingResponse:
        return await handle_v1_chat_route(
            request_body=request_body,
            request=request,
            metrics=runtime_metrics,
            logger=logger,
            evaluate_immune_gate_fn=evaluate_immune_gate_for_chat,
            schedule_immune_gate_fn=schedule_immune_gate_audit,
            handle_chat_fn=handle_chat_with_runtime,
        )

    def obs_command_ingress_provider():
        current = security_container
        composition = (
            current.obs_command_composition if current is not None else None
        )
        if composition is None or not composition.enabled:
            return None
        return composition.ingress

    def pentest_secure_run_composition_provider():
        current = security_container
        return (
            current.pentest_secure_run_composition
            if current is not None
            else None
        )

    def signed_skill_provider_intake_provider():
        current = security_container
        composition = (
            current.signed_skill_provider_intake_composition
            if current is not None
            else None
        )
        if composition is None or not composition.enabled:
            return None
        return composition.ingress

    @asynccontextmanager
    async def app_lifespan(application: FastAPI):
        nonlocal security_container, task_runtime_container, governed_host_runtime
        async with runtime_lifespan(get_ledger=get_ledger, logger=logger):
            security: ProcessSecurityServiceContainer | None = None
            task_runtime: ProcessTaskRuntimeContainer | None = None
            try:
                ledger = get_ledger()
                security = build_process_security_service_container(
                    ledger=ledger,
                    canonical_db_path=str(
                        getattr(ledger, "canonical_db_path", "") or ""
                    ),
                    token_service=get_token_service(),
                )
            except Exception:
                logger.error(
                    "HOST_SECURITY_SERVICE_CONTAINER_CONSTRUCTION_FAILED",
                    exc_info=True,
                )
            security_container = security
            application.state.security_service_container = security
            try:
                # This is the fail-closed durable startup gate.  The canonical
                # migrations and process-owned stores must be ready before the
                # ASGI lifespan yields and routes become available.
                ledger = get_ledger()
                task_runtime = build_process_task_runtime_container(
                    ledger=ledger,
                    canonical_db_path=str(
                        getattr(ledger, "canonical_db_path", "") or ""
                    ),
                )
                runtime_settings = get_core_settings()
                canonical_owner = (
                    runtime_settings.task_runtime_canonical_owner
                    or runtime_settings.task_runtime_shadow_owner
                    or "host-task-runtime"
                )
                governed_runtime = build_governed_host_runtime(
                    task_runtime_container=task_runtime,
                    ledger=ledger,
                    skill_registry=get_skill_registry(),
                    token_service=get_token_service(),
                    runtime_router=CanonicalRuntimeRouter(
                        mode=runtime_settings.task_runtime_mode,
                        canary_percent=(
                            runtime_settings.task_runtime_canary_percent
                        ),
                        kill_switch=runtime_settings.task_runtime_kill_switch,
                        canonical_owner=canonical_owner,
                        # The process composition is the canonical worker
                        # owner; request payloads cannot self-confirm it.
                        confirmed_owner=canonical_owner,
                    ),
                    owner_id=canonical_owner,
                )
                task_runtime.bind_runtime(governed_runtime)
                await task_runtime.activate_runtime()
                task_runtime_container = task_runtime
                governed_host_runtime = governed_runtime
                application.state.task_runtime_container = task_runtime
                application.state.governed_host_runtime = governed_runtime
                yield
            finally:
                governed_host_runtime = None
                task_runtime_container = None
                application.state.governed_host_runtime = None
                application.state.task_runtime_container = None
                try:
                    if task_runtime is not None:
                        try:
                            await task_runtime.aclose()
                        except TaskRuntimeContainerCloseError as exc:
                            logger.error(
                                "HOST_TASK_RUNTIME_CONTAINER_CLOSE_FAILED:%s",
                                ",".join(
                                    failure.component
                                    for failure in exc.failures
                                ),
                            )
                finally:
                    # Security was constructed first, so it is always released
                    # after the task runtime, including partial startup failure.
                    security_container = None
                    application.state.security_service_container = None
                    if security is not None:
                        try:
                            await security.aclose()
                        except SecurityServiceContainerCloseError as exc:
                            logger.error(
                                "HOST_SECURITY_SERVICE_CONTAINER_CLOSE_FAILED:%s",
                                ",".join(
                                    failure.service.value
                                    for failure in exc.failures
                                ),
                            )

    application = create_host_fastapi_app(
        lifespan=app_lifespan,
        metrics=runtime_metrics,
        logger=logger,
        get_ledger=get_ledger,
        get_tool_broker=get_tool_broker,
        get_tool_catalog=get_tool_catalog,
        get_bond_engine=resolve_bond_engine,
        chat_endpoint=chat_endpoint_with_runtime,
        edge_events_endpoint=edge_events_endpoint,
        root_endpoint=root_endpoint,
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
    )
    application.state.security_service_container = None
    application.state.task_runtime_container = None
    application.state.governed_host_runtime = None
    application.state.task_runtime_container_provider = (
        task_runtime_container_provider
    )
    return application


app = create_app()


__all__ = ["app", "create_app"]
