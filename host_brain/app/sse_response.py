from __future__ import annotations

import json
from typing import Any

from fastapi import Response
from fastapi.responses import StreamingResponse


def sse_single_commit(payload: dict[str, Any]) -> StreamingResponse:
    async def _single_event():
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _single_event(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def sse_immediate_events(events: list[dict[str, Any]]) -> Response:
    body = "".join(
        f"data: {json.dumps(dict(event or {}), ensure_ascii=False)}\n\n"
        for event in list(events or [])
    )
    return Response(
        content=body,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


__all__ = ["sse_immediate_events", "sse_single_commit"]
