from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from host_brain.app.schemas import EdgeEventsRequest, EdgeEventsResponse
from host_brain.app.system_handler import receive_edge_events_response


async def handle_edge_events_route(
    *,
    request_body: EdgeEventsRequest,
    get_ledger: Callable[[], Any],
    logger: logging.Logger,
) -> EdgeEventsResponse:
    bridge_waiters: list[Any] = []
    loop = asyncio.get_running_loop()
    response = await asyncio.to_thread(
        receive_edge_events_response,
        request_body=request_body,
        get_ledger=get_ledger,
        logger=logger,
        inline_waiters=bridge_waiters,
        main_loop=loop,
    )
    if bridge_waiters:
        await asyncio.gather(*bridge_waiters)
    return response


__all__ = ["handle_edge_events_route"]
