"""Authority-minimal production ports for already-routed host work.

The governed Task Runtime calls this module only after admission has frozen a
node's route and target.  The boundary intentionally exposes no router,
planner, replan service, task registry, or delivery authority.  It can render
one chat node or dispatch one delegate node; choosing *which* operation to run
belongs to the caller's durable TaskGraph.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
import time
from typing import Any, Protocol

from host_brain.app.governed_delegate_dispatch import (
    GovernedDelegateDispatchError,
    GovernedDelegateInvocation,
    GovernedDelegateOutcome,
)
from host_brain.surface_runtime.ports import SurfaceRuntimePorts
from host_brain.task_runtime.governed_workers import FrozenNodeInput


class SurfaceReplyStream(Protocol):
    def __call__(self, value: Any) -> AsyncIterator[str]: ...


@dataclass(frozen=True, slots=True)
class GovernedRouteExecutionPorts:
    """Small immutable object graph for frozen chat/delegate execution."""

    surface_reply_input_factory: Callable[..., Any]
    resolve_surface_reply_llm_runtime: Callable[..., Any]
    surface_reply_sse_generator: SurfaceReplyStream
    governed_delegate_dispatcher: Any


@dataclass(frozen=True, slots=True)
class RealtimeCompanionExecutionPorts:
    """Minimal app-local ports for an ordinary conversational turn."""

    surface_reply_input_factory: Callable[..., Any]
    resolve_surface_reply_llm_runtime: Callable[..., Any]
    surface_reply_sse_generator: SurfaceReplyStream


@dataclass(frozen=True, slots=True)
class RouteExecutionOutcome:
    """Authority-free result returned by a single low-level route call."""

    route_type: str
    target_id: str
    skill_family: str
    success: bool
    result_text: str
    error_text: str
    latency_ms: int
    payload: dict[str, Any]


class RouteExecutionBoundaryError(RuntimeError):
    """Raised when authority is smuggled into a frozen execution port."""


def build_governed_route_execution_ports(
    *,
    governed_delegate_dispatcher: Any,
    surface_runtime_ports: SurfaceRuntimePorts | None = None,
) -> GovernedRouteExecutionPorts:
    """Compose only collaborators needed to execute an already-frozen node.

    The low-level builder has no hidden process-service fallback.  Production
    Host composition must inject the lifespan-owned Surface ports.  Focused
    non-Chat tests may build a registry without them, but a real Chat node then
    fails closed before the legacy Surface path can be reached.
    """

    if not callable(getattr(governed_delegate_dispatcher, "dispatch", None)):
        raise RouteExecutionBoundaryError(
            "governed_delegate_dispatcher.dispatch is required"
        )

    from host_brain.app.surface_reply_preprocessor import (  # noqa: PLC0415
        SurfaceReplyInput,
    )
    if surface_runtime_ports is None:

        def resolve_surface_reply_llm_runtime(**_kwargs: Any) -> dict[str, Any]:
            raise RouteExecutionBoundaryError(
                "governed Chat requires lifespan-owned SurfaceRuntimePorts"
            )

        async def surface_reply_sse_generator(_value: Any) -> AsyncIterator[str]:
            if False:  # pragma: no cover - makes the fail-closed path an async iterator.
                yield ""
            raise RouteExecutionBoundaryError(
                "governed Chat requires lifespan-owned SurfaceRuntimePorts"
            )

    else:
        from host_brain.app.surface_reply_runtime import (  # noqa: PLC0415
            surface_reply_sse_generator as _surface_reply_sse_generator,
        )

        resolve_surface_reply_llm_runtime = (
            surface_runtime_ports.resolve_surface_reply_llm_runtime
        )

        def surface_reply_sse_generator(value: Any) -> AsyncIterator[str]:
            return _surface_reply_sse_generator(
                value,
                ports=surface_runtime_ports,
            )

    return GovernedRouteExecutionPorts(
        surface_reply_input_factory=SurfaceReplyInput,
        resolve_surface_reply_llm_runtime=resolve_surface_reply_llm_runtime,
        surface_reply_sse_generator=surface_reply_sse_generator,
        governed_delegate_dispatcher=governed_delegate_dispatcher,
    )


def build_realtime_companion_execution_ports(
    *,
    surface_runtime_ports: SurfaceRuntimePorts,
) -> RealtimeCompanionExecutionPorts:
    """Bind the existing Surface stream without task-execution authority."""

    if not isinstance(surface_runtime_ports, SurfaceRuntimePorts):
        raise RouteExecutionBoundaryError(
            "realtime companion chat requires lifespan-owned SurfaceRuntimePorts"
        )
    from host_brain.app.surface_reply_preprocessor import (  # noqa: PLC0415
        SurfaceReplyInput,
    )
    from host_brain.app.surface_reply_runtime import (  # noqa: PLC0415
        surface_reply_sse_generator as _surface_reply_sse_generator,
    )

    def surface_reply_sse_generator(value: Any) -> AsyncIterator[str]:
        return _surface_reply_sse_generator(value, ports=surface_runtime_ports)

    def resolve_realtime_surface_reply_llm_runtime(
        *,
        base_runtime: dict,
    ) -> dict[str, Any]:
        runtime = dict(base_runtime or {})
        runtime["runtime_path"] = "realtime_companion"
        return surface_runtime_ports.resolve_surface_reply_llm_runtime(
            base_runtime=runtime,
        )

    return RealtimeCompanionExecutionPorts(
        surface_reply_input_factory=SurfaceReplyInput,
        resolve_surface_reply_llm_runtime=resolve_realtime_surface_reply_llm_runtime,
        surface_reply_sse_generator=surface_reply_sse_generator,
    )


async def stream_realtime_chat_with_context(
    *,
    ports: RealtimeCompanionExecutionPorts,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    sanitized_message: str,
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    bond_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    ts_ms: int,
    intent_draft: dict[str, Any],
    trace_stage_meta: dict[str, Any],
) -> AsyncIterator[str]:
    """Stream one ordinary companion turn through canonical affective Life.

    This boundary deliberately has no TaskGraph, grant, budget, review or
    external tool port.  A router must choose ``skill``, ``delegate`` or
    ``plan`` before those capabilities can be used.
    """

    async for event in _stream_chat_with_context(
        ports=ports,
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        sanitized_message=sanitized_message,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        trace_stage_meta=trace_stage_meta,
        sub_agent_context=None,
    ):
        yield event


async def stream_governed_chat_with_context(
    *,
    ports: GovernedRouteExecutionPorts,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    sanitized_message: str,
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    bond_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    ts_ms: int,
    intent_draft: dict[str, Any],
    trace_stage_meta: dict[str, Any],
    sub_agent_context: Any,
    runtime_capability_payload: dict[str, Any] | None,
    governor_result: Mapping[str, Any] | None = None,
) -> AsyncIterator[str]:
    """Render one admitted chat node without route or delivery authority."""

    if dict(governor_result or {}):
        raise RouteExecutionBoundaryError(
            "governed chat port does not accept governor or delivery authority"
        )
    if dict(runtime_capability_payload or {}):
        raise RouteExecutionBoundaryError(
            "governed chat port cannot synthesize or activate runtime capabilities"
        )
    async for event in _stream_chat_with_context(
        ports=ports,
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        sanitized_message=sanitized_message,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        trace_stage_meta=trace_stage_meta,
        sub_agent_context=sub_agent_context,
    ):
        yield event


async def _stream_chat_with_context(
    *,
    ports: Any,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    sanitized_message: str,
    emotion_context: Mapping[str, Any],
    emotion_snapshot: Mapping[str, Any],
    bond_summary: Mapping[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: Mapping[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    ts_ms: int,
    intent_draft: Mapping[str, Any],
    trace_stage_meta: Mapping[str, Any],
    sub_agent_context: Any,
) -> AsyncIterator[str]:
    chat_input = _build_chat_input(
        ports=ports,
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        sanitized_message=sanitized_message,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        trace_stage_meta=trace_stage_meta,
        sub_agent_context=sub_agent_context,
    )
    async for event in ports.surface_reply_sse_generator(chat_input):
        yield event


def _build_chat_input(
    *,
    ports: Any,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    sanitized_message: str,
    emotion_context: Mapping[str, Any],
    emotion_snapshot: Mapping[str, Any],
    bond_summary: Mapping[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: Mapping[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    ts_ms: int,
    intent_draft: Mapping[str, Any],
    trace_stage_meta: Mapping[str, Any],
    sub_agent_context: Any,
) -> Any:
    resolved_llm_runtime = (
        dict(llm_runtime or {}) if isinstance(llm_runtime, dict) else llm_runtime
    )
    if isinstance(resolved_llm_runtime, dict) and resolved_llm_runtime:
        agent_role = _text(resolved_llm_runtime.get("agent_role"), lower=True)
        if agent_role != "surface_reply":
            resolved_llm_runtime = ports.resolve_surface_reply_llm_runtime(
                base_runtime=resolved_llm_runtime,
            )

    chat_trace_stage_meta = dict(trace_stage_meta or {})
    chat_trace_stage_meta.setdefault(
        "input_source",
        str(getattr(request_body, "input_source", "") or "").strip(),
    )
    chat_trace_stage_meta.setdefault(
        "reply_channel",
        str(getattr(request_body, "reply_channel", "") or "").strip(),
    )

    args = intent_draft.get("args") if isinstance(intent_draft, Mapping) else None
    scene_awareness = (
        intent_draft.get("scene_awareness") if isinstance(intent_draft, Mapping) else None
    )
    if not isinstance(scene_awareness, Mapping) and isinstance(args, Mapping):
        scene_awareness = args.get("scene_awareness")

    return ports.surface_reply_input_factory(
        request_id=request_id,
        session_id=session_id,
        user_message=sanitized_message,
        person_id=person_id,
        workspace_id=str(getattr(request_body, "workspace_id", "") or "").strip(),
        thread_id=(
            str(getattr(request_body, "thread_id", "") or "").strip() or None
        ),
        emotion_context=dict(emotion_context or {}),
        emotion_snapshot=dict(emotion_snapshot or {}),
        bond_summary=dict(bond_summary or {}),
        retrieval_evidence_items=list(retrieval_evidence_items or []),
        retrieval_summary=dict(retrieval_summary or {}),
        sub_agent_context=sub_agent_context,
        render_target_lang=render_target_lang,
        response_form=str(getattr(request_body, "response_form", "") or "").strip(),
        reply_channel=getattr(request_body, "reply_channel", None),
        reply_target=getattr(request_body, "reply_target", None),
        suppress_edge_tts=getattr(request_body, "suppress_edge_tts", None),
        suppress_edge_vts=getattr(request_body, "suppress_edge_vts", None),
        defer_effects_to_task_runtime=bool(
            getattr(request_body, "defer_effects_to_task_runtime", False)
        ),
        governed_delivery_authority=getattr(
            request_body,
            "governed_delivery_authority",
            None,
        ),
        ts_ms=ts_ms,
        intent_draft=dict(intent_draft or {}),
        trace_stage_meta=chat_trace_stage_meta,
        governor_result={},
        delivery_authorization={},
        llm_runtime=resolved_llm_runtime,
        render_policy=dict(getattr(request_body, "render_policy", {}) or {}),
        surface_task_control=dict(
            getattr(request_body, "surface_task_control", {}) or {}
        ),
        scene_awareness=(
            dict(scene_awareness or {}) if isinstance(scene_awareness, Mapping) else {}
        ),
        attachments=list(getattr(request_body, "attachments", []) or []),
        multimodal_observations=dict(
            getattr(request_body, "multimodal_observations", {}) or {}
        ),
        attachment_summary=str(
            getattr(request_body, "attachment_summary", "") or ""
        ).strip(),
    )


async def dispatch_governed_delegate(
    *,
    ports: GovernedRouteExecutionPorts | Any,
    frozen: FrozenNodeInput,
    request_id: str,
    session_id: str,
    person_id: str,
    target_id: str,
    task_text: str,
    user_message: str,
    context: dict[str, Any] | None = None,
) -> RouteExecutionOutcome:
    """Dispatch one frozen delegate node through the typed inner boundary."""

    target = _required_text(target_id, field_name="target_id")
    task = _required_text(task_text, field_name="task_text")
    if not isinstance(frozen, FrozenNodeInput):
        raise RouteExecutionBoundaryError("delegate dispatch requires FrozenNodeInput")
    if (
        str(frozen.request_id or "").strip() != _required_text(request_id, field_name="request_id")
        or str(frozen.session_id or "").strip() != _required_text(session_id, field_name="session_id")
        or str(frozen.principal_id or "").strip() != _required_text(person_id, field_name="person_id")
        or str(frozen.target_id or "").strip() != target
        or str(frozen.objective or "").strip() != task
    ):
        raise RouteExecutionBoundaryError(
            "delegate call arguments drifted from the frozen TaskNode"
        )
    safe_context = dict(context or {})
    forbidden = {
        "route",
        "route_type",
        "target_id",
        "planner",
        "planning",
        "replan",
        "recovery_route",
    }
    leaked = sorted(key for key in safe_context if str(key).strip().lower() in forbidden)
    if leaked:
        raise RouteExecutionBoundaryError(
            f"delegate context contains route/planning authority: {', '.join(leaked)}"
        )

    started = time.perf_counter()
    try:
        invocation = GovernedDelegateInvocation.from_frozen(
            frozen=frozen,
            user_message=str(user_message or "").strip(),
            context=safe_context,
        )
        response = ports.governed_delegate_dispatcher.dispatch(invocation)
        if hasattr(response, "__await__"):
            response = await response
    except GovernedDelegateDispatchError as exc:
        raise RouteExecutionBoundaryError(str(exc)) from exc
    measured_latency_ms = int((time.perf_counter() - started) * 1000)
    if not isinstance(response, GovernedDelegateOutcome):
        raise RouteExecutionBoundaryError(
            "governed delegate dispatcher returned an invalid outcome"
        )
    response_agent_id = _text(response.target_id)
    if response_agent_id != target:
        raise RouteExecutionBoundaryError(
            "governed delegate outcome target differs from the frozen delegate target"
        )
    return RouteExecutionOutcome(
        route_type="delegate",
        target_id=target,
        skill_family=_text(safe_context.get("skill_family")) or "general",
        success=bool(response.success),
        result_text=_text(response.result_text),
        error_text=_text(response.error_text),
        latency_ms=measured_latency_ms,
        payload={
            "audit_refs": dict(response.audit_refs or {}),
            "agent_id": response_agent_id or target,
        },
    )


def _required_text(value: Any, *, field_name: str) -> str:
    text = _text(value)
    if not text:
        raise RouteExecutionBoundaryError(f"{field_name} is required")
    return text


def _text(value: Any, *, lower: bool = False) -> str:
    text = str(value or "").strip()
    return text.lower() if lower else text


__all__ = [
    "GovernedRouteExecutionPorts",
    "RealtimeCompanionExecutionPorts",
    "RouteExecutionBoundaryError",
    "RouteExecutionOutcome",
    "build_governed_route_execution_ports",
    "build_realtime_companion_execution_ports",
    "dispatch_governed_delegate",
    "stream_governed_chat_with_context",
    "stream_realtime_chat_with_context",
]
