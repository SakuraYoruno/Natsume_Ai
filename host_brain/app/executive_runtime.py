"""Executive runtime implementation entrypoint.

This module hosts the canonical execution implementation for executive routing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, AsyncGenerator

_logger = logging.getLogger("Natsume.executive_runtime")


def _extract_result_code(error_text: str | None) -> str | None:
    text = str(error_text or "").strip()
    if not text:
        return None
    match = re.match(r"([A-Z0-9_]+)\s*:", text)
    if match:
        return match.group(1).strip()
    if text.isupper() and " " not in text:
        return text
    return None


def record_executive_handoff(
    *,
    request_id: str,
    session_id: str,
    handoff_route: str,
    handoff_target: str | None,
    handoff_reason: str | None,
    extra: dict[str, Any] | None = None,
) -> None:
    try:
        from host_brain.tribe.handoff import get_tribe_handoff_coordinator  # noqa: PLC0415

        coordinator = get_tribe_handoff_coordinator()
        coordinator.record_dispatch_handoff(
            request_id=request_id,
            session_id=session_id,
            route=handoff_route,
            target_id=handoff_target,
            reason=handoff_reason,
            source_family="cortex",
            extra=extra,
        )
    except Exception as exc:
        _logger.debug("[%s] executive handoff record skipped: %s", request_id, exc)

    try:
        from host_brain.tribe.events import emit_tribe_event  # noqa: PLC0415

        emit_tribe_event(
            event_type="INTERACTION_RECORDED",
            source_agent="executive_router",
            payload={
                "request_id": str(request_id or ""),
                "session_id": str(session_id or ""),
                "route": str(handoff_route or ""),
                "target_id": str(handoff_target or ""),
                "reason": str(handoff_reason or ""),
            },
        )
    except Exception as exc:
        _logger.debug("[%s] executive INTERACTION_RECORDED tribe event skipped: %s", request_id, exc)


def build_runtime_capability_handoff_extra(runtime_capability_payload: dict[str, Any] | None) -> dict[str, Any]:
    if runtime_capability_payload is None:
        return {}
    runtime_spec = dict(runtime_capability_payload.get("runtime_capability") or {})
    forge_spec = dict(runtime_capability_payload.get("capability_forge") or {})
    gap_info = dict(runtime_capability_payload.get("capability_gap") or {})
    return {
        "runtime_capability_id": runtime_spec.get("runtime_capability_id"),
        "capability_gap_reason": gap_info.get("reason_code"),
        "capability_forge_proposal_id": forge_spec.get("proposal_id"),
        "capability_forge_stage": forge_spec.get("stage"),
    }


def record_runtime_capability_execution(
    *,
    request_id: str,
    runtime_capability_payload: dict[str, Any] | None,
    success: bool,
    latency_ms: int | float | None,
    error_text: str | None,
    executor_route: str,
    executor_target: str,
    actor: str = "executive_runtime",
) -> None:
    if runtime_capability_payload is None:
        return

    forge_spec = dict(runtime_capability_payload.get("capability_forge") or {})
    forge_proposal_id = str(forge_spec.get("proposal_id") or "").strip()
    if forge_proposal_id:
        try:
            from host_brain.app.dependencies import get_capability_forge_service  # noqa: PLC0415

            get_capability_forge_service().record_runtime_result(
                proposal_id=forge_proposal_id,
                success=bool(success),
                latency_ms=int(latency_ms or 0),
                error_code=_extract_result_code(error_text),
                actor=str(actor or "executive_runtime"),
            )
        except Exception as exc:
            _logger.debug("[%s] capability forge runtime result skipped: %s", request_id, exc)

    runtime_spec = dict(runtime_capability_payload.get("runtime_capability") or {})
    runtime_capability_id = str(runtime_spec.get("runtime_capability_id") or "").strip()
    if not runtime_capability_id:
        return
    try:
        from host_brain.app.dependencies import get_capability_gap_telemetry  # noqa: PLC0415

        get_capability_gap_telemetry().record_execution(
            runtime_capability_id=runtime_capability_id,
            request_id=request_id,
            success=bool(success),
            executor_route=executor_route,
            executor_target=executor_target,
            latency_ms=int(latency_ms or 0),
            error_code=_extract_result_code(error_text),
        )
    except Exception as exc:
        _logger.debug("[%s] capability gap telemetry(record_execution) skipped: %s", request_id, exc)


async def try_handle_recent_turn_direct_route(
    *,
    route: str,
    request_id: str,
    session_id: str,
    person_id: str,
    sanitized_message: str,
    render_target_lang: str,
    route_reason: str,
    ts_ms: int,
    emotion_context: dict[str, Any],
    bond_summary: dict[str, Any],
    governor_result: dict[str, Any],
    pii_ingress_stats: dict[str, Any] | None,
    sse_fn,
    record_dispatch_handoff_fn,
) -> list[str] | None:
    if not _legacy_direct_text_routes_enabled():
        return None
    if str(route or "").strip().lower() != "recent_turn":
        return None

    from host_brain.app import agent_runtime_shared as shared  # noqa: PLC0415

    answer = shared._maybe_build_recent_turn_answer(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        render_target_lang=render_target_lang,
    )
    if not answer:
        return None

    narrative = shared._airlock_sanitize_output(str(answer).strip())
    total_latency_ms = max(0, int(time.time() * 1000) - int(ts_ms or 0))
    record_dispatch_handoff_fn(
        handoff_route="recent_turn",
        handoff_target=None,
        handoff_reason=route_reason,
        extra={"skill_family": "conversation_memory"},
    )
    asyncio.create_task(
        shared._async_post_process(
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            user_message=sanitized_message,
            narrative=narrative,
            emotion_context=emotion_context,
            bond_summary=bond_summary,
            ts_ms=ts_ms,
            render_target_lang=render_target_lang,
            total_latency_ms=total_latency_ms,
            llm_latency_ms=0,
            first_token_ms=None,
            provider_name="",
            model_id="",
            buffer_stream=False,
            delegated_agents=[],
            retrieval_summary={"status": "SKIPPED", "reason_code": "RECENT_TURN_DIRECT"},
            gov_decision=governor_result,
            pii_ingress_stats=pii_ingress_stats,
            pii_egress_stats={},
            trace_stage_meta={
                "executive_router_used": True,
                "surface_reply_used": False,
                "executive_runtime_mode": "recent_turn_direct",
            },
        )
    )
    return [
        sse_fn(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "executive_runtime:recent_turn",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
    ]


async def try_handle_task_progress_direct_route(
    *,
    route: str,
    request_id: str,
    session_id: str,
    person_id: str,
    sanitized_message: str,
    route_reason: str,
    ts_ms: int,
    emotion_context: dict[str, Any],
    bond_summary: dict[str, Any],
    governor_result: dict[str, Any],
    pii_ingress_stats: dict[str, Any] | None,
    render_target_lang: str,
    fallback_no_summary_text: str,
    build_task_progress_sub_context_fn,
    sse_fn,
    record_dispatch_handoff_fn,
) -> list[str] | None:
    if not _legacy_direct_text_routes_enabled():
        return None
    if str(route or "").strip().lower() != "task_progress":
        return None

    from host_brain.app import agent_runtime_shared as shared  # noqa: PLC0415

    sub_context = build_task_progress_sub_context_fn(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
    )
    result_text = str(getattr(sub_context, "result_text", "") or "").strip()
    if not result_text:
        result_text = str(fallback_no_summary_text or "").strip()
    if not result_text:
        return None

    narrative = shared._airlock_sanitize_output(result_text)
    total_latency_ms = max(0, int(time.time() * 1000) - int(ts_ms or 0))
    record_dispatch_handoff_fn(
        handoff_route="task_progress",
        handoff_target=None,
        handoff_reason=route_reason,
        extra={"skill_family": "runtime_task"},
    )
    asyncio.create_task(
        shared._async_post_process(
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            user_message=sanitized_message,
            narrative=narrative,
            emotion_context=emotion_context,
            bond_summary=bond_summary,
            ts_ms=ts_ms,
            render_target_lang=render_target_lang,
            total_latency_ms=total_latency_ms,
            llm_latency_ms=0,
            first_token_ms=None,
            provider_name="",
            model_id="",
            buffer_stream=False,
            delegated_agents=[],
            retrieval_summary={"status": "SKIPPED", "reason_code": "TASK_PROGRESS_DIRECT"},
            gov_decision=governor_result,
            pii_ingress_stats=pii_ingress_stats,
            pii_egress_stats={},
            trace_stage_meta={
                "executive_router_used": True,
                "surface_reply_used": False,
                "executive_runtime_mode": "task_progress_direct",
            },
        )
    )
    return [
        sse_fn(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "executive_runtime:task_progress",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
    ]


async def stream_chat_route(
    *,
    route: str,
    request_id: str,
    route_reason: str,
    skill_family: str,
    runtime_capability_handoff_extra: dict[str, Any],
    record_dispatch_handoff_fn,
    surface_reply_sse_generator_fn,
    chat_input,
):
    if str(route or "").strip().lower() != "chat":
        return

    chat_route_started_ms = int(time.time() * 1000)
    _logger.info("[%s] ExecutiveRuntime -> chat (reason=%s)", request_id, route_reason)
    record_dispatch_handoff_fn(
        handoff_route="chat",
        handoff_target=None,
        handoff_reason=route_reason,
        extra={"skill_family": skill_family, **dict(runtime_capability_handoff_extra or {})},
    )
    first_event_logged = False
    first_render_logged = False
    async for event in surface_reply_sse_generator_fn(chat_input):
        if not first_event_logged:
            first_event_logged = True
            _logger.info(
                "[%s] ExecutiveRuntime chat_first_event_ms=%s",
                request_id,
                max(0, int(time.time() * 1000) - chat_route_started_ms),
            )
        if not first_render_logged and event.startswith("data: "):
            try:
                payload = json.loads(event[6:].strip())
            except Exception:
                payload = {}
            if str(payload.get("phase") or "").strip().upper() == "RENDER":
                first_render_logged = True
                _logger.info(
                    "[%s] ExecutiveRuntime chat_first_render_ms=%s",
                    request_id,
                    max(0, int(time.time() * 1000) - chat_route_started_ms),
                )
        yield event


async def executive_runtime_sse_generator(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    governor_result: dict[str, Any],
    intent_id: str | None,
    metrics: Any,
    person_id: str,
    sanitized_message: str,
    bond_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    pii_ingress_stats: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    async for event in _executive_runtime_core_sse_generator_impl(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        governor_result=governor_result,
        intent_id=intent_id,
        metrics=metrics,
        person_id=person_id,
        sanitized_message=sanitized_message,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        pii_ingress_stats=pii_ingress_stats,
    ):
        yield event


async def _executive_runtime_core_sse_generator_impl(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    governor_result: dict[str, Any],
    intent_id: str | None,
    metrics: Any,
    person_id: str,
    sanitized_message: str,
    bond_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    pii_ingress_stats: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    from host_brain.app.executive_runtime_core import _executive_runtime_core_impl  # noqa: PLC0415

    async for event in _executive_runtime_core_impl(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        governor_result=governor_result,
        intent_id=intent_id,
        metrics=metrics,
        person_id=person_id,
        sanitized_message=sanitized_message,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        pii_ingress_stats=pii_ingress_stats,
    ):
        yield event


def _legacy_direct_text_routes_enabled() -> bool:
    raw = str(os.environ.get("HOST_LEGACY_DIRECT_TEXT_ROUTES_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


__all__ = [
    "executive_runtime_sse_generator",
    "_executive_runtime_core_sse_generator_impl",
    "record_executive_handoff",
    "build_runtime_capability_handoff_extra",
    "record_runtime_capability_execution",
    "try_handle_recent_turn_direct_route",
    "try_handle_task_progress_direct_route",
    "stream_chat_route",
]
