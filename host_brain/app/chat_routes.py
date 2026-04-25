from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from host_brain.app.schemas import ChatRequest
from host_brain.app.sse_response import sse_single_commit
from host_brain.core.ids import new_request_id


_CHAT_HELP_MARKERS = (
    "帮",
    "请",
    "修",
    "查",
    "做",
    "实现",
    "分析",
    "plan",
    "fix",
    "implement",
    "help",
    "?",
    "？",
)


def build_chat_bio_ops_preflight_trace(request_body: ChatRequest) -> dict[str, Any]:
    message = str(getattr(request_body, "message", "") or "")
    emotion_context = dict(getattr(request_body, "emotion_context", None) or {})
    emotion_tag = str(getattr(request_body, "emotion_tag", "") or "").strip()
    if emotion_tag and "emotion_tag" not in emotion_context:
        emotion_context["emotion_tag"] = emotion_tag
    if getattr(request_body, "emotion_intensity", None) is not None and "arousal" not in emotion_context:
        emotion_context["arousal"] = getattr(request_body, "emotion_intensity", None)
    explicit_help_request = any(marker in message.lower() for marker in _CHAT_HELP_MARKERS)
    try:
        from host_brain.bio_ops.runtime_trace import build_bio_ops_runtime_trace  # noqa: PLC0415

        return build_bio_ops_runtime_trace(
            message=message,
            route_type="chat",
            target_id="",
            intent_draft=dict(getattr(request_body, "intent_draft", None) or {}),
            emotion_context=emotion_context,
            runtime_signals={
                "current_hour": int(time.localtime().tm_hour),
                "tts_status": "suppressed" if bool(getattr(request_body, "suppress_edge_tts", False)) else "ok",
                "user_stress": str(emotion_context.get("stress_level") or ""),
                "explicit_help_request": explicit_help_request,
            },
        )
    except Exception as exc:
        return {
            "source": "bio_ops.runtime_trace",
            "proposal_only": True,
            "status": "DEGRADED",
            "reason_code": "BIO_OPS_CHAT_PREFLIGHT_TRACE_FAILED",
            "detail": str(exc)[:200],
        }


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
    bio_ops_trace = build_chat_bio_ops_preflight_trace(request_body)
    request.state.bio_ops_runtime_trace = bio_ops_trace
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
                "bio_ops_runtime_trace": bio_ops_trace,
            }
        )
    if immune_decision == "REPORT":
        logger.info(
            "immune gate report /v1/chat request_id=%s reasons=%s",
            request_id,
            immune_result.get("reason_codes"),
        )
    return await handle_chat_fn(request_body=request_body, request=request, metrics=metrics)


__all__ = ["build_chat_bio_ops_preflight_trace", "handle_v1_chat_route"]
