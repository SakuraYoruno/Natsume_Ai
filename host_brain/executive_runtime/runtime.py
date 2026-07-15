"""Retired Executive execution boundary.

The module keeps the historical streaming helper for isolated rendering tests,
but production Cortex no longer imports or calls this runtime.  All execution
routes use Governed Task Runtime; read routes use deterministic projections.
"""

from ._shared import *  # noqa: F403
from .ports import ExecutiveRuntimeApplicationPorts


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


def _action_arbiter_execution_enabled() -> bool:
    """Retained for isolated legacy helper tests only."""

    return get_orchestration_settings().action_arbiter_enabled


async def _executive_runtime_core_impl(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
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
    """Fail closed for every route after the production cutover."""

    del (
        ports,
        request_body,
        request_id,
        session_id,
        ts_ms,
        emotion_context,
        emotion_snapshot,
        governor_result,
        intent_id,
        metrics,
        person_id,
        sanitized_message,
        bond_summary,
        retrieval_evidence_items,
        retrieval_summary,
        render_target_lang,
        llm_runtime,
        pii_ingress_stats,
    )
    declared_route = str(dict(intent_draft or {}).get("route") or "").strip().lower()
    if declared_route in {"chat", "skill", "delegate", "plan"}:
        raise PermissionError("CANONICAL_ROUTE_REQUIRES_GOVERNED_TASK_RUNTIME")
    if declared_route in {"task_progress", "recent_turn"}:
        raise PermissionError("READ_ROUTE_REQUIRES_DURABLE_PROJECTION")
    raise PermissionError("EXECUTIVE_RUNTIME_RETIRED")
    if False:  # pragma: no cover - preserves the async-generator contract.
        yield ""


__all__ = [
    "_action_arbiter_execution_enabled",
    "_executive_runtime_core_impl",
    "stream_chat_route",
]
