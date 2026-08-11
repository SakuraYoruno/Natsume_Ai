"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any, Callable
from collections.abc import Mapping
from functools import partial
from host_brain.core.runtime_helpers import (  # noqa: F401
    _format_template_text,
    _load_template_mapping,
    _mapping_section,
    _parse_bool_env,
    _sse,
    _airlock_sanitize_input,
    _airlock_sanitize_output,
    _build_reply_preview,
    _coerce_trace_mapping,
    _coerce_trace_score,
    _coerce_trace_bool,
    _normalize_trace_stage_meta,
    _resolve_envelope_confidence,
    _build_trace_output_digest,
    _detect_trace_fallback_used,
    _summarize_motive_state_for_trace,
    _summarize_conversation_interpretation_for_trace,
    _summarize_reply_plan_for_trace,
    _summarize_tactic_ledger_for_trace,
    _summarize_topic_ledger_for_trace,
    _summarize_affect_state_for_trace,
    _summarize_relationship_state_for_trace,
    _summarize_drive_state_for_trace,
    _summarize_emotion_context_for_trace,
    _summarize_persona_resident_memory_for_trace,
)
import asyncio
from host_brain.bond.user_identity import resolve_person_id
from host_brain.core.soft_fail import soft_fail  # OPT-3 Batch B：业务路径统一降级
import time

from host_brain.agent_runtime.contextual_answers import (
    _is_short_assistant_reply_followup,
    _memory_recall_plan_should_bypass_direct_status_fastlane,
)
from host_brain.agent_runtime.fastpaths import (
    _is_low_risk_short_chat_route_reason,
    _needs_selection_followup_retrieval_budget,
    _prefetch_retrieval_persona_inputs,
)
from host_brain.agent_runtime.runtime_texts import (
    _logger,
    _resolve_text_render_target_lang,
)
from host_brain.persona.session_hooks import (
    _resolve_retrieval_timeout_seconds,
    _resolve_selection_followup_retrieval_timeout_seconds,
)
from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.agent_runtime.read_routes import build_read_projection


def _project_governed_terminal_event(
    event: Mapping[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    """Render one durable terminal result as the public Cortex contract.

    Task Runtime deliberately uses ``TERMINAL`` for an unaccepted node: the
    durable receipt remains the authority for recovery, review, or diagnosis.
    The public Cortex SSE protocol completes every route with exactly one
    ``COMMIT`` frame.  Forwarding the internal ``TERMINAL`` phase for Skill,
    Delegate or Plan leaked a Task Runtime detail and left the Host input
    adapter waiting forever for a terminal frame.

    This is a presentation-only projection.  It does not turn a rejected
    TaskGraph into an acceptance, alter its receipt, or invent a narrative.
    """

    projected = dict(event)
    if (
        str(projected.get("phase") or "").strip().upper() != "TERMINAL"
        or not bool(projected.get("done"))
    ):
        return projected
    receipt = projected.get("execution_receipt")
    if not isinstance(receipt, Mapping) or bool(receipt.get("accepted")):
        return projected
    projected.update(
        {
            "phase": "COMMIT",
            "done": True,
            "decision": "REJECT",
            "reason_code": (
                str(projected.get("reason_code") or "").strip()
                or str(receipt.get("reason_code") or "").strip()
                or "GOVERNED_RUNTIME_TERMINAL_REJECTED"
            ),
            "request_id": request_id,
        }
    )
    # A rejected task may legitimately have no visible response.  Keep that
    # fact explicit rather than letting an old client infer a reply from the
    # mere existence of a COMMIT frame.
    projected.setdefault("narrative", "")
    return projected


async def _cortex_orchestrator_sse_generator_impl(
    *,
    application_ports: AgentRuntimeApplicationPorts,
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
    sse_fn: Callable[[dict[str, Any]], str] | None = None,
    airlock_sanitize_input_fn: Callable[[str], tuple[str, bool]] | None = None,
    airlock_sanitize_output_fn: Callable[[str], str] | None = None,
    resolve_retrieval_timeout_seconds_fn: Callable[[], float] | None = None,
    prefetch_retrieval_persona_inputs_fn: Callable[..., Any] | None = None,
    resolve_person_id_fn: Callable[..., str] | None = None,
    governed_command_application: Any | None = None,
    command_authority: Mapping[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    orchestrator_started_ms = int(time.time() * 1000)
    _emit_sse = sse_fn or _sse
    _sanitize_input = airlock_sanitize_input_fn or _airlock_sanitize_input
    _sanitize_output = airlock_sanitize_output_fn or _airlock_sanitize_output
    _resolve_timeout = resolve_retrieval_timeout_seconds_fn or _resolve_retrieval_timeout_seconds
    _prefetch_persona_inputs = prefetch_retrieval_persona_inputs_fn or partial(
        _prefetch_retrieval_persona_inputs,
        application_ports=application_ports,
    )
    _resolve_person = resolve_person_id_fn or resolve_person_id

    user_message = str(request_body.message or "").strip()
    thread_id = getattr(request_body, "thread_id", None)
    person_id = _resolve_person(
        getattr(request_body, "person_id", None),
        session_id=session_id,
        platform=getattr(request_body, "reply_channel", None),
        conversation_id=getattr(request_body, "reply_target", None),
    )
    workspace_id = str(getattr(request_body, "workspace_id", "") or "").strip()
    canonical_route = str((intent_draft or {}).get("route") or "").strip().lower()
    input_source = str(
        getattr(request_body, "input_source", "") or ""
    ).strip().upper()
    canonical_chat_route = canonical_route == "chat"
    # Gateway replies are externally visible effects.  They must be compiled
    # into the immutable TaskGraph so Acceptance owns the durable delivery
    # intent and its ActionPreview authority.  Local/direct companion chat
    # deliberately keeps the low-latency realtime path.
    governed_gateway_chat = canonical_chat_route and input_source == "GATEWAY"
    canonical_realtime_route = canonical_chat_route and not governed_gateway_chat
    canonical_governed_route = (
        canonical_route in {"skill", "delegate", "plan"}
        or governed_gateway_chat
    )
    canonical_read_route = canonical_route in {"task_progress", "recent_turn"}
    realtime_chat_port = getattr(application_ports, "stream_realtime_chat", None)
    governed_supported = bool(
        governed_command_application is not None
        and callable(getattr(governed_command_application, "supports", None))
        and governed_command_application.supports(intent_draft)
    )

    sanitized_message, airlock_passed = _sanitize_input(user_message)
    if not airlock_passed:
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "AIRLOCK_EMPTY_MESSAGE",
                "request_id": request_id,
            }
        )
        return

    _pii_ingress_stats: dict[str, Any] = {}
    with soft_fail("agent_runtime_shared.pii_scrub_ingress", fallback=None):
        from host_brain.airlock.pii_filter import _check_channel, pii_scrub_text  # noqa: PLC0415

        if _check_channel("INGRESS"):
            _pii_result = pii_scrub_text(sanitized_message, context="ingress")
            sanitized_message = _pii_result.scrubbed_text
            if _pii_result.has_pii:
                _pii_ingress_stats = {
                    "detections": _pii_result.total_detections,
                    "rules": sorted({d["rule_id"] for d in _pii_result.detections}),
                }

    yield _emit_sse({"phase": "PRESENCE", "event": "THINKING", "request_id": request_id})

    # The surface Governor is the only authorization authority.  The former
    # chat-lite pass was a second, unrelated decision after the official
    # route grant and could silently override it.
    gov = dict(governor_result or {})
    if str(gov.get("decision") or "").strip().upper() != "APPROVE":
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": gov.get("reason_code", "REJECTED"),
                "request_id": request_id,
            }
        )
        return

    if canonical_realtime_route and not callable(realtime_chat_port):
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "REALTIME_COMPANION_RUNTIME_UNAVAILABLE",
                "request_id": request_id,
            }
        )
        return

    if canonical_governed_route and not governed_supported:
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": (
                    "GOVERNED_RUNTIME_UNAVAILABLE"
                    if governed_command_application is None
                    else "GOVERNED_ROUTE_UNSUPPORTED"
                ),
                "request_id": request_id,
            }
        )
        return

    if (
        not canonical_realtime_route
        and not canonical_governed_route
        and not canonical_read_route
    ):
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "RUNTIME_ROUTE_UNSUPPORTED",
                "request_id": request_id,
            }
        )
        return

    render_target_lang = _resolve_text_render_target_lang(
        request_body=request_body,
        sanitized_message=sanitized_message,
    )

    if canonical_read_route:
        # Read projections do not create a TaskGraph.  They must therefore be
        # strictly side-effect free: visible turns, Memory/Life projections and
        # delivery remain Acceptance-owned effects of governed Tasks only.
        try:
            task_projection = (
                application_ports.task_status_projection()
                if canonical_route == "task_progress"
                else None
            )
            conversation_history = (
                application_ports.conversation_history()
                if canonical_route == "recent_turn"
                else None
            )
            read_result = build_read_projection(
                route=canonical_route,
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                user_message=sanitized_message,
                task_status_projection=task_projection,
                conversation_history=conversation_history,
            )
        except Exception as exc:
            _logger.warning("[%s] read projection unavailable: %s", request_id, exc)
            yield _emit_sse(
                {
                    "phase": "COMMIT",
                    "done": True,
                    "decision": "REJECT",
                    "reason_code": "READ_PROJECTION_UNAVAILABLE",
                    "request_id": request_id,
                }
            )
            return
        if not read_result.available:
            yield _emit_sse(
                {
                    "phase": "COMMIT",
                    "done": True,
                    "decision": "REJECT",
                    "reason_code": read_result.reason_code,
                    "request_id": request_id,
                    "narrative": _sanitize_output(read_result.narrative),
                    "read_projection": dict(read_result.evidence),
                }
            )
            return
        narrative = _sanitize_output(read_result.narrative)
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "reason_code": read_result.reason_code,
                "request_id": request_id,
                "narrative": narrative,
                "read_projection": dict(read_result.evidence),
                "audit_refs": {
                    "request_id": request_id,
                    "path": f"cortex_orchestrator:{canonical_route}_projection",
                    "latency_ms": max(0, int(time.time() * 1000) - ts_ms),
                },
            }
        )
        return

    # All write routes are selected before this point.  Ordinary conversation
    # is enriched and sent to the app-local affective Surface stream; Skill,
    # Delegate and Plan continue into the durable governed runtime below.
    bond_summary: dict[str, Any] = {}

    has_multimodal_input = bool(getattr(request_body, "attachments", None)) or bool(
        getattr(request_body, "multimodal_observations", None)
    )
    assistant_followup_fastlane = (not has_multimodal_input) and _is_short_assistant_reply_followup(sanitized_message)
    route_reason = str(
        (intent_draft or {}).get("route_reason")
        or (intent_draft or {}).get("reason_code")
        or ""
    ).strip()
    route_hint = str((intent_draft or {}).get("route") or "").strip().lower()
    relational_fastlane = False
    relational_topic = ""
    direct_status_fastlane = route_hint in {"task_progress", "recent_turn"}
    if direct_status_fastlane and _memory_recall_plan_should_bypass_direct_status_fastlane(
        sanitized_message,
        task_mode=route_hint,
    ):
        direct_status_fastlane = False
    multimodal_fastlane = False
    if has_multimodal_input:
        _multimodal_observations = dict(getattr(request_body, "multimodal_observations", None) or {})
        _primary_modality = str(_multimodal_observations.get("primary_modality") or "").strip().lower()
        multimodal_fastlane = bool(
            route_reason == "THALAMUS_RULE_MULTIMODAL_CHAT"
            or _primary_modality in {"image", "emoji", "sticker"}
            or sanitized_message in {"[图片消息]", "[表情消息]", "[贴纸消息]", "[动画表情]"}
        )
    if not has_multimodal_input:
        try:
            from host_brain.persona.relational_chat import detect_relational_chat_topic  # noqa: PLC0415

            relational_topic = str(detect_relational_chat_topic(sanitized_message) or "").strip()
        except Exception as relational_detector_exc:
            _logger.debug("[%s] cortex_orchestrator relational detector degraded: %s", request_id, relational_detector_exc)
        relational_fastlane = bool(
            _is_low_risk_short_chat_route_reason(route_reason)
            or relational_topic
            or assistant_followup_fastlane
        )

    retrieval_task: asyncio.Task | None = None
    retrieval_evidence_items: list[dict[str, Any]] = []
    retrieval_summary: dict[str, Any] = {"status": "SKIPPED"}
    bond_evidence: dict[str, Any] | None = None
    retrieval_motive_state: dict[str, Any] = {}
    prefetch_persona_inputs_ms = 0
    retrieval_wait_ms = 0
    prompt_filter_ms = 0
    user_preference_pack_ms = 0
    llm_runtime_resolve_ms = 0
    _hard_state_summary = gov.get("hard_state_summary") if isinstance(gov.get("hard_state_summary"), Mapping) else {}
    retrieval_mood_state = (
        str(
            emotion_snapshot.get("mood_state")
            or emotion_context.get("mood_state")
            or gov.get("mood_state")
            or _hard_state_summary.get("mood_state")
            or ""
        ).strip().lower()
        or None
    )

    prefetch_started_ms = int(time.time() * 1000)
    try:
        if relational_fastlane or direct_status_fastlane:
            bond_summary, bond_evidence, retrieval_motive_state = await asyncio.wait_for(
                _prefetch_persona_inputs(
                    request_id=request_id,
                    session_id=session_id,
                    person_id=person_id,
                    workspace_id=workspace_id,
                    user_message=sanitized_message,
                    emotion_context=emotion_context,
                    mood_state=retrieval_mood_state,
                    governor_decision=gov,
                ),
                timeout=0.35,
            )
        else:
            bond_summary, bond_evidence, retrieval_motive_state = await _prefetch_persona_inputs(
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                workspace_id=workspace_id,
                user_message=sanitized_message,
                emotion_context=emotion_context,
                mood_state=retrieval_mood_state,
                governor_decision=gov,
            )
    except asyncio.TimeoutError:
        bond_summary, bond_evidence, retrieval_motive_state = {}, None, {}
        _logger.info(
            "[%s] cortex_orchestrator relational prefetch skipped timeout_ms=%s topic=%s route_reason=%s",
            request_id,
            350,
            relational_topic or "",
            route_reason or "",
        )
    prefetch_persona_inputs_ms = max(0, int(time.time() * 1000) - prefetch_started_ms)

    try:
        if assistant_followup_fastlane:
            retrieval_summary = {
                "status": "SKIPPED",
                "reason_code": "ASSISTANT_REPLY_FOLLOWUP_RETRIEVAL_SKIPPED",
            }
        elif multimodal_fastlane:
            retrieval_summary = {
                "status": "SKIPPED",
                "reason_code": "MULTIMODAL_FASTLANE_RETRIEVAL_SKIPPED",
            }
        elif direct_status_fastlane:
            retrieval_summary = {
                "status": "SKIPPED",
                "reason_code": f"{route_hint.upper()}_DIRECT_RETRIEVAL_SKIPPED",
            }
        else:
            retrieval_task = asyncio.create_task(
                application_ports.run_retrieval(
                    request_id=request_id,
                    message=sanitized_message,
                    person_id=person_id,
                    session_id=session_id,
                    ts_ms=ts_ms,
                    emotion_tag=str(emotion_context.get("emotion_tag") or "") or None,
                    render_target_lang=render_target_lang,
                    mood_state=retrieval_mood_state,
                    bond_summary=bond_summary or None,
                    motive_state=retrieval_motive_state or None,
                    intent_draft=intent_draft,
                )
            )
    except Exception as retrieval_exc:
        _logger.debug("[%s] cortex_orchestrator retrieval init failed: %s", request_id, retrieval_exc)

    if retrieval_task is not None:
        try:
            retrieval_wait_started_ms = int(time.time() * 1000)
            retrieval_timeout_s = 0.45 if relational_fastlane else _resolve_timeout()
            if not relational_fastlane and _needs_selection_followup_retrieval_budget(sanitized_message):
                retrieval_timeout_s = max(retrieval_timeout_s, _resolve_selection_followup_retrieval_timeout_seconds())
            retrieval_summary, retrieval_evidence_items = await asyncio.wait_for(
                retrieval_task,
                timeout=retrieval_timeout_s,
            )
            retrieval_wait_ms = max(0, int(time.time() * 1000) - retrieval_wait_started_ms)
        except asyncio.TimeoutError:
            retrieval_wait_ms = max(0, int(time.time() * 1000) - retrieval_wait_started_ms)
            retrieval_summary = (
                {"status": "SKIPPED", "reason_code": "RELATIONAL_CHAT_RETRIEVAL_SKIPPED"}
                if relational_fastlane
                else {"status": "DEGRADED", "reason_code": "RETRIEVAL_TIMEOUT"}
            )
            _logger.info(
                "[%s] cortex_orchestrator retrieval skipped timeout_ms=%s topic=%s route_reason=%s",
                request_id,
                int(retrieval_timeout_s * 1000),
                relational_topic or "",
                route_reason or "",
            )
        except Exception as retrieval_exc:
            retrieval_summary = {"status": "DEGRADED", "error": str(retrieval_exc)[:100]}
            retrieval_wait_ms = max(0, int(time.time() * 1000) - retrieval_wait_started_ms)
            _logger.debug("[%s] cortex_orchestrator retrieval failed/timeout: %s", request_id, retrieval_exc)

    if bond_evidence is not None:
        retrieval_evidence_items.append(bond_evidence)
    try:
        prompt_filter_started_ms = int(time.time() * 1000)
        filtered_evidence_items, prompt_filter_meta = application_ports.filter_retrieval_evidence_items_for_prompt(
            user_message=sanitized_message,
            retrieval_summary=retrieval_summary,
            retrieval_evidence_items=retrieval_evidence_items,
            session_id=session_id,
            person_id=person_id,
            ledger=application_ports.audit_ledger(),
        )
        prompt_filter_ms = max(0, int(time.time() * 1000) - prompt_filter_started_ms)
        if prompt_filter_meta.get("applied"):
            retrieval_evidence_items = filtered_evidence_items
            retrieval_summary = dict(retrieval_summary or {})
            retrieval_summary["prompt_filter"] = dict(prompt_filter_meta)
    except Exception as prompt_filter_exc:
        _logger.debug("[%s] cortex_orchestrator prompt retrieval filter failed: %s", request_id, prompt_filter_exc)

    user_preference_pack_started_ms = int(time.time() * 1000)
    try:
        user_preference_pack = application_ports.build_user_preference_pack(
            person_id=person_id,
            query_text=sanitized_message,
            session_id=session_id,
            conversation_id=session_id,
            ledger=application_ports.audit_ledger(),
        )
        if user_preference_pack:
            retrieval_summary = dict(retrieval_summary or {})
            retrieval_summary["user_preference_pack"] = dict(user_preference_pack)
    except Exception as preference_pack_exc:
        _logger.debug("[%s] user preference pack build skipped: %s", request_id, preference_pack_exc)
    finally:
        user_preference_pack_ms = max(
            0,
            int(time.time() * 1000) - user_preference_pack_started_ms,
        )

    # 人格聊天链不再使用 slot 投影。
    # 运行时只保留五路召回原始结果，由 prompt 直接消费检索证据；
    # 最近上下文则继续由 continuity 链单独管理。

    llm_runtime_started_ms = int(time.time() * 1000)
    llm_runtime = await application_ports.resolve_host_llm_runtime_async(
        rollout_key=session_id,
        scene_type="CHAT",
    )
    llm_runtime_resolve_ms = max(0, int(time.time() * 1000) - llm_runtime_started_ms)

    if canonical_realtime_route:
        stream_realtime_chat = realtime_chat_port
        if not callable(stream_realtime_chat):  # defensive; checked above
            raise RuntimeError("REALTIME_COMPANION_RUNTIME_INVARIANT_BROKEN")
        context_ready_ms = int(time.time() * 1000)
        trace_stage_meta = {
            "runtime_path": "realtime_companion",
            "orchestrator_started_ms": orchestrator_started_ms,
            "context_ready_ms": context_ready_ms,
            "context_pipeline_ms": max(0, context_ready_ms - orchestrator_started_ms),
            "prefetch_persona_inputs_ms": prefetch_persona_inputs_ms,
            "retrieval_wait_ms": retrieval_wait_ms,
            "retrieval_runtime_timings_ms": dict(
                retrieval_summary.get("runtime_timings_ms") or {}
            ),
            "prompt_filter_ms": prompt_filter_ms,
            "user_preference_pack_ms": user_preference_pack_ms,
            "llm_runtime_resolve_ms": llm_runtime_resolve_ms,
            "pii_ingress": dict(_pii_ingress_stats),
        }
        async for realtime_event in stream_realtime_chat(
            request_body=request_body,
            route_reason=route_reason,
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
        ):
            yield realtime_event
        return

    if governed_command_application is None:
        raise RuntimeError("GOVERNED_RUNTIME_INVARIANT_BROKEN")
    async for governed_event in governed_command_application.execute_stream(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        governor_result=gov,
        person_id=person_id,
        sanitized_message=sanitized_message,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        llm_runtime=llm_runtime,
        pii_ingress_stats=_pii_ingress_stats,
        motive_state=retrieval_motive_state,
        render_target_lang=render_target_lang,
        command_authority=dict(command_authority or {}),
    ):
        public_event = _project_governed_terminal_event(
            governed_event,
            request_id=request_id,
        )
        yield _emit_sse(public_event)
