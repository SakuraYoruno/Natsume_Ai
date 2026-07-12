"""ASGI entrypoint for the host main service."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

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
from host_brain.app.platform_security_runtime import (
    platform_security_runtime_lifespan,
)
from host_brain.app.runtime_metrics import RuntimeMetrics
from host_brain.app.schemas import ChatRequest, EdgeEventsRequest, EdgeEventsResponse


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


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    async with runtime_lifespan(get_ledger=get_ledger, logger=logger):
        # ASGI lifespan runs inside the forked worker.  Security stores and
        # owner leases must never be constructed in the import-time parent.
        async with platform_security_runtime_lifespan(
            application,
            get_ledger=get_ledger,
            get_token_service=get_token_service,
            logger=logger,
        ):
            yield


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
    return create_host_fastapi_app(
        lifespan=lifespan,
        metrics=runtime_metrics,
        logger=logger,
        get_ledger=get_ledger,
        get_tool_broker=get_tool_broker,
        get_tool_catalog=get_tool_catalog,
        get_bond_engine=resolve_bond_engine,
        chat_endpoint=chat_endpoint,
        edge_events_endpoint=edge_events_endpoint,
        root_endpoint=root_endpoint,
    )


app = create_app()


__all__ = ["app", "create_app"]
