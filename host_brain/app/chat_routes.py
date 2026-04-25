from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from host_brain.app.schemas import ChatRequest
from host_brain.app.sse_response import sse_single_commit
from host_brain.core.ids import new_request_id


async def handle_v1_chat_route(
    *,
    request_body: ChatRequest,
    request: Request,
    metrics: Any,
    logger: logging.Logger,
    evaluate_immune_gate_fn: Callable[[ChatRequest], dict[str, Any]],
    schedule_immune_gate_fn: Callable[..., None],
    handle_chat_fn: Callable[..., Any],
) -> StreamingResponse:
    try:
        from host_brain.app.dependencies import notify_user_interaction  # noqa: PLC0415

        notify_user_interaction()
    except Exception:
        pass
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    request.state.request_id = request_id
    try:
        immune_result = evaluate_immune_gate_fn(request_body)
    except Exception as exc:
        logger.warning("immune gate evaluate degraded: %s", exc)
        immune_result = {"decision": "ALLOW", "reason_codes": ["IMMUNE_EVALUATE_DEGRADED"]}
    request.state.immune_inspection = immune_result
    schedule_immune_gate_fn(
        request_id=request_id,
        request_body=request_body,
        immune_result=immune_result,
    )
    immune_decision = str(immune_result.get("decision") or "ALLOW").strip().upper()
    if immune_decision == "BLOCK":
        logger.warning(
            "immune gate blocked /v1/chat request_id=%s reasons=%s",
            request_id,
            immune_result.get("reason_codes"),
        )
        return sse_single_commit(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "IMMUNE_BLOCKED_HIGH_RISK",
                "narrative": "",
                "request_id": request_id,
                "immune": {
                    "risk_level": immune_result.get("risk_level"),
                    "reason_codes": immune_result.get("reason_codes"),
                    "score": immune_result.get("score"),
                },
            }
        )
    if immune_decision == "REPORT":
        logger.info(
            "immune gate report /v1/chat request_id=%s reasons=%s",
            request_id,
            immune_result.get("reason_codes"),
        )
    return await handle_chat_fn(request_body=request_body, request=request, metrics=metrics)


__all__ = ["handle_v1_chat_route"]
