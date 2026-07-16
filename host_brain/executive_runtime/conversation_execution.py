"""Executive runtime responsibility extracted from the HTTP application adapter."""

from ._shared import *  # noqa: F403
from .ports import ExecutiveRuntimeApplicationPorts

def _build_chat_input(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    request_body,
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
    sub_agent_context,
    governor_result: Mapping[str, Any] | None = None,
):
    resolved_llm_runtime = dict(llm_runtime or {}) if isinstance(llm_runtime, dict) else llm_runtime
    if isinstance(resolved_llm_runtime, dict) and resolved_llm_runtime:
        agent_role = _normalize_text(resolved_llm_runtime.get("agent_role"), lower=True)
        if agent_role != "surface_reply":
            resolved_llm_runtime = ports.resolve_surface_reply_llm_runtime(
                base_runtime=resolved_llm_runtime,
            )

    chat_trace_stage_meta = dict(trace_stage_meta or {})
    chat_trace_stage_meta.setdefault("input_source", str(getattr(request_body, "input_source", "") or "").strip())
    chat_trace_stage_meta.setdefault("reply_channel", str(getattr(request_body, "reply_channel", "") or "").strip())

    governor = dict(governor_result or {})
    delivery_authorization: dict[str, str] = {}
    if (
        request_id
        and str(governor.get("decision") or "").strip().upper() == "APPROVE"
    ):
        from host_brain.orchestration.governor_grant import governor_grant_ref  # noqa: PLC0415

        authority_principal_id = str(
            governor.get("principal_id")
            or getattr(request_body, "principal_id", "")
            or person_id
            or ""
        ).strip()
        if authority_principal_id:
            delivery_authorization = {
                "request_id": request_id,
                "governor_grant_ref": governor_grant_ref(request_id, governor),
                "authority_kind": "surface_governor",
                "authority_principal_id": authority_principal_id,
            }
            chat_trace_stage_meta["delivery_authorization"] = dict(
                delivery_authorization
            )
            chat_trace_stage_meta["governor_grant_request_id"] = request_id
            chat_trace_stage_meta["governor_grant_ref"] = delivery_authorization[
                "governor_grant_ref"
            ]

    intent_args = intent_draft.get("args") if isinstance(intent_draft, dict) else None
    scene_awareness = intent_draft.get("scene_awareness") if isinstance(intent_draft, dict) else None
    if not isinstance(scene_awareness, dict) and isinstance(intent_args, dict):
        scene_awareness = intent_args.get("scene_awareness")

    return ports.surface_reply_input_factory(
        request_id=request_id,
        session_id=session_id,
        user_message=sanitized_message,
        person_id=person_id,
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
        ts_ms=ts_ms,
        intent_draft=dict(intent_draft or {}),
        trace_stage_meta=chat_trace_stage_meta,
        governor_result=governor,
        delivery_authorization=delivery_authorization,
        llm_runtime=resolved_llm_runtime,
        render_policy=dict(getattr(request_body, "render_policy", {}) or {}),
        surface_task_control=dict(getattr(request_body, "surface_task_control", {}) or {}),
        scene_awareness=dict(scene_awareness or {}) if isinstance(scene_awareness, dict) else {},
        attachments=list(getattr(request_body, "attachments", []) or []),
        multimodal_observations=dict(getattr(request_body, "multimodal_observations", {}) or {}),
        attachment_summary=str(getattr(request_body, "attachment_summary", "") or "").strip(),
    )


def _build_sub_agent_context(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    agent_id: str,
    task_text: str,
    outcome: _ExecutionOutcome,
):
    return ports.surface_sub_agent_context_factory(
        agent_id=agent_id,
        task=task_text,
        result_text=outcome.result_text,
        success=outcome.success,
        latency_ms=outcome.latency_ms,
        error=outcome.error_text,
        payload=dict(outcome.payload or {}),
    )


def _build_plan_chat_outcome(
    *,
    plan: SkillDagPlan,
    outcome: _ExecutionOutcome,
    plan_meta: dict[str, Any],
) -> _ExecutionOutcome:
    payload = dict(outcome.payload or {})
    plan_trace = list(plan_meta.get("plan_trace") or payload.get("plan_trace") or [])
    trace_by_node_id = {
        str(item.get("node_id") or "").strip(): dict(item or {})
        for item in plan_trace
        if isinstance(item, dict) and str(item.get("node_id") or "").strip()
    }
    steps: list[dict[str, Any]] = []
    success_count = 0
    for node in list(plan.nodes or []):
        trace = trace_by_node_id.get(str(node.node_id or "").strip(), {})
        step_success = bool(trace.get("success"))
        if step_success:
            success_count += 1
        steps.append(
            {
                "step_index": int(node.step_index or 0),
                "target_id": str(node.target_id or "").strip(),
                "description": str(node.description or "").strip(),
                "task_description": str(node.task_description or "").strip(),
                "success": step_success,
                "result_text": str(trace.get("result_text") or "").strip(),
                "error": str(trace.get("error_text") or "").strip(),
            }
        )
    payload.update(
        {
            "plan": plan.as_dict(),
            "plan_trace": plan_trace,
            "steps": steps,
            "total_steps": len(steps),
            "success_count": success_count,
            "parallel_enabled": bool(plan_meta.get("parallel_enabled")),
        }
    )
    return _ExecutionOutcome(
        route_type=outcome.route_type,
        target_id=outcome.target_id,
        skill_family=outcome.skill_family,
        success=outcome.success,
        result_text=outcome.result_text,
        error_text=outcome.error_text,
        latency_ms=outcome.latency_ms,
        payload=payload,
    )


async def _stream_chat_with_context(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    request_body: Any,
    route_reason: str,
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
    sub_agent_context,
    runtime_capability_payload: dict[str, Any] | None,
    governor_result: Mapping[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
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
        governor_result=governor_result,
    )
    async for event in stream_chat_route(
        route="chat",
        request_id=request_id,
        route_reason=route_reason,
        skill_family=str(trace_stage_meta.get("skill_family") or "social"),
        runtime_capability_handoff_extra=build_runtime_capability_handoff_extra(runtime_capability_payload),
        record_dispatch_handoff_fn=lambda **kwargs: record_executive_handoff(
            ports=ports,
            request_id=request_id,
            session_id=session_id,
            **kwargs,
        ),
        surface_reply_sse_generator_fn=ports.surface_reply_sse_generator,
        chat_input=chat_input,
    ):
        yield event


# --- Helpers moved from executive_runtime.py (Phase 4) ---


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
    ports: ExecutiveRuntimeApplicationPorts,
    request_id: str,
    session_id: str,
    handoff_route: str,
    handoff_target: str | None,
    handoff_reason: str | None,
    extra: dict[str, Any] | None = None,
) -> None:
    try:
        coordinator = ports.get_tribe_handoff_coordinator()
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
        ports.emit_tribe_event(
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
    ports: ExecutiveRuntimeApplicationPorts,
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
            ports.get_capability_forge_service().record_runtime_result(
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
        ports.get_capability_gap_telemetry().record_execution(
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
