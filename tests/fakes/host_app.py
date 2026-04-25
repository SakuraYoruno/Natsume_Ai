from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

from host_brain.app.app_factory import create_host_fastapi_app, docs_redirect_response
from host_brain.app.edge_event_routes import handle_edge_events_route
from host_brain.app.runtime_metrics import RuntimeMetrics
from host_brain.app.schemas import ChatRequest, EdgeEventsRequest, EdgeEventsResponse


@asynccontextmanager
async def null_lifespan(_application: FastAPI):
    yield


async def default_chat_endpoint(_request_body: ChatRequest, _request: Request) -> StreamingResponse:
    async def _events():
        yield 'data: {"phase":"COMMIT","decision":"APPROVE","request_id":"req-test-default"}\n\n'

    return StreamingResponse(_events(), media_type="text/event-stream")


async def default_root_endpoint() -> Response:
    return docs_redirect_response()


def build_host_test_app(
    *,
    get_ledger: Callable[[], Any] = lambda: None,
    get_tool_broker: Callable[[], Any] = lambda: None,
    get_tool_catalog: Callable[[], Any] = lambda: None,
    get_bond_engine: Callable[[], Any] = lambda: None,
    chat_endpoint: Callable[[ChatRequest, Request], Awaitable[Response]] = default_chat_endpoint,
    edge_events_endpoint: Callable[[EdgeEventsRequest], Awaitable[EdgeEventsResponse]] | None = None,
    root_endpoint: Callable[[], Awaitable[Response]] = default_root_endpoint,
    metrics: RuntimeMetrics | None = None,
    logger: logging.Logger | None = None,
) -> FastAPI:
    runtime_metrics = metrics or RuntimeMetrics()
    runtime_logger = logger or logging.getLogger("tests.host_app")

    if edge_events_endpoint is None:
        async def edge_events_endpoint(request_body: EdgeEventsRequest) -> EdgeEventsResponse:
            return await handle_edge_events_route(
                request_body=request_body,
                get_ledger=get_ledger,
                logger=runtime_logger,
            )

    return create_host_fastapi_app(
        lifespan=null_lifespan,
        metrics=runtime_metrics,
        logger=runtime_logger,
        get_ledger=get_ledger,
        get_tool_broker=get_tool_broker,
        get_tool_catalog=get_tool_catalog,
        get_bond_engine=get_bond_engine,
        chat_endpoint=chat_endpoint,
        edge_events_endpoint=edge_events_endpoint,
        root_endpoint=root_endpoint,
    )
