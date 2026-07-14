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


async def _execute_plan_node(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    node: SkillDagNode,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str | None,
    governor_result: dict[str, Any],
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    render_target_lang: str,
    node_results: dict[str, _ExecutionOutcome],
) -> _ExecutionOutcome:
    dependency_context_parts: list[str] = []
    for dep_id in list(node.dependencies or ()):
        previous = node_results.get(dep_id)
        if previous and previous.result_text:
            dependency_context_parts.append(f"[{dep_id}] {previous.result_text}")
    task_text = _normalize_text(node.task_description or node.description)
    if dependency_context_parts:
        task_text = f"{task_text}\n\nDependency context:\n" + "\n".join(dependency_context_parts)

    if node.route == "skill":
        plan_intent_draft = dict(intent_draft or {})
        plan_intent_draft["args"] = _merge_plan_node_args(
            base_intent_draft=plan_intent_draft,
            node=node,
            task_text=task_text,
        )
        node_governor_result = dict(governor_result or {})
        node_intent_draft: dict[str, Any] | None = None
        node_grants = node_governor_result.pop("node_grants", None)
        if isinstance(node_grants, Mapping):
            candidate = node_grants.get(node.node_id) or node_grants.get(node.target_id)
            if isinstance(candidate, Mapping):
                node_governor_result = dict(candidate)
                raw_node_intent = node_governor_result.pop("_node_intent_draft", None)
                if isinstance(raw_node_intent, Mapping):
                    node_intent_draft = dict(raw_node_intent)
        if node_intent_draft is not None:
            plan_intent_draft = node_intent_draft
        return await asyncio.to_thread(
            _execute_skill,
            ports=ports,
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            intent_id=(
                _normalize_text(node_intent_draft.get("id"))
                if node_intent_draft is not None
                else intent_id
            ),
            skill_id=node.target_id,
            task_text=task_text,
            user_message=task_text,
            governor_result=node_governor_result,
            intent_draft=plan_intent_draft,
            emotion_context=emotion_context,
            render_target_lang=render_target_lang,
        )
    return await _dispatch_to_sub_agent(
        ports=ports,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        target_id=node.target_id or "intent_agent",
        task_text=task_text,
        user_message=task_text,
        context={
            "skill_family": node.skill_family or "executive",
            "plan_node_id": node.node_id,
            "route_reason": node.route_reason,
        },
    )


async def _execute_skill_dag(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    plan: SkillDagPlan,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str | None,
    governor_result: dict[str, Any],
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    render_target_lang: str,
) -> tuple[_ExecutionOutcome, dict[str, Any]]:
    """Execute only the approved upstream canonical plan."""

    return await _execute_explicit_skill_dag(
        ports=ports,
        plan=plan,
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        intent_id=intent_id,
        governor_result=governor_result,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        render_target_lang=render_target_lang,
    )


async def _execute_explicit_skill_dag(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    plan: SkillDagPlan,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str | None,
    governor_result: dict[str, Any],
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    render_target_lang: str,
) -> tuple[_ExecutionOutcome, dict[str, Any]]:
    node_results: dict[str, _ExecutionOutcome] = {}
    plan_trace: list[dict[str, Any]] = []
    critical_failure = False

    for batch in list(plan.execution_batches or []):
        if critical_failure:
            _logger.warning(
                "[%s] _execute_explicit_skill_dag: critical node failed, stop remaining batches",
                request_id,
            )
            break

        batch_nodes = [node for node in plan.nodes if node.node_id in batch]
        coros = [
            _execute_plan_node(
                ports=ports,
                node=node,
                request_body=request_body,
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                intent_id=intent_id,
                governor_result=governor_result,
                intent_draft=intent_draft,
                emotion_context=emotion_context,
                render_target_lang=render_target_lang,
                node_results=node_results,
            )
            for node in batch_nodes
        ]
        batch_results = await asyncio.gather(*coros)
        for node, outcome in zip(batch_nodes, batch_results):
            node_results[node.node_id] = outcome
            plan_trace.append(
                {
                    "node_id": node.node_id,
                    "route": node.route,
                    "target_id": node.target_id,
                    "success": outcome.success,
                    "latency_ms": outcome.latency_ms,
                    "result_text": outcome.result_text,
                    "error_text": outcome.error_text,
                }
            )
            if not outcome.success:
                has_dependents = any(
                    node.node_id in (n.dependencies or ())
                    for n in plan.nodes
                    if n.node_id != node.node_id
                )
                if has_dependents:
                    critical_failure = True
                    _logger.warning(
                        "[%s] critical explicit-plan node failed: %s",
                        request_id,
                        node.node_id,
                    )

    ordered_results = [node_results[node.node_id] for node in plan.nodes if node.node_id in node_results]
    success = bool(ordered_results) and all(item.success for item in ordered_results) and not critical_failure
    summary_lines: list[str] = []
    for node in plan.nodes:
        outcome = node_results.get(node.node_id)
        if outcome is None:
            continue
        body = outcome.result_text or outcome.error_text or "no output"
        summary_lines.append(f"[{node.step_index}] {node.target_id or node.description}: {body}")

    return (
        _ExecutionOutcome(
            route_type="plan",
            target_id=plan.plan_id,
            skill_family="executive",
            success=success,
            result_text="\n".join(summary_lines).strip(),
            error_text="" if success else "PLAN_NODE_FAILED",
            latency_ms=sum(int(item.latency_ms or 0) for item in ordered_results),
            payload={
                "plan": plan.as_dict(),
                "plan_trace": plan_trace,
            },
        ),
        {
            "plan_id": plan.plan_id,
            "plan_trace": plan_trace,
            "parallel_enabled": any(len(batch) > 1 for batch in list(plan.execution_batches or [])),
            "engine": "canonical_skill_dag",
        },
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
