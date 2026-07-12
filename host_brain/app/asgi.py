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
    get_token_service,
    get_tool_broker,
    get_tool_catalog,
)
from host_brain.app.edge_event_routes import handle_edge_events_route
from host_brain.app.http_hooks import runtime_lifespan
from host_brain.app.runtime_metrics import RuntimeMetrics
from host_brain.app.schemas import ChatRequest, EdgeEventsRequest, EdgeEventsResponse
from host_brain.composition.security_services import (
    ProcessSecurityServiceContainer,
    SecurityServiceContainerCloseError,
    build_process_security_service_container,
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


async def chat_endpoint(request_body: ChatRequest, request: Request) -> StreamingResponse:
    return await handle_v1_chat_route(
        request_body=request_body,
        request=request,
        metrics=runtime_metrics,
        logger=logger,
        evaluate_immune_gate_fn=evaluate_immune_gate_for_chat,
        schedule_immune_gate_fn=schedule_immune_gate_audit,
        handle_chat_fn=handle_chat,
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
        nonlocal security_container
        async with runtime_lifespan(get_ledger=get_ledger, logger=logger):
            container: ProcessSecurityServiceContainer | None = None
            try:
                ledger = get_ledger()
                container = build_process_security_service_container(
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
            security_container = container
            application.state.security_service_container = container
            try:
                yield
            finally:
                security_container = None
                application.state.security_service_container = None
                if container is not None:
                    try:
                        await container.aclose()
                    except SecurityServiceContainerCloseError as exc:
                        logger.error(
                            "HOST_SECURITY_SERVICE_CONTAINER_CLOSE_FAILED:%s",
                            ",".join(
                                failure.service.value
                                for failure in exc.failures
                            ),
                        )

    return create_host_fastapi_app(
        lifespan=app_lifespan,
        metrics=runtime_metrics,
        logger=logger,
        get_ledger=get_ledger,
        get_tool_broker=get_tool_broker,
        get_tool_catalog=get_tool_catalog,
        get_bond_engine=resolve_bond_engine,
        chat_endpoint=chat_endpoint,
        edge_events_endpoint=edge_events_endpoint,
        root_endpoint=root_endpoint,
        obs_command_ingress_provider=obs_command_ingress_provider,
        pentest_secure_run_composition_provider=(
            pentest_secure_run_composition_provider
        ),
        signed_skill_provider_intake_provider=(
            signed_skill_provider_intake_provider
        ),
    )


app = create_app()


__all__ = ["app", "create_app"]
