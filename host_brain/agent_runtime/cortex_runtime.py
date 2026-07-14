"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any, Callable, Sequence
from collections.abc import Mapping
from functools import partial
from host_brain.core.runtime_helpers import (  # noqa: F401
    _format_template_text,
    _load_template_mapping,
    _mapping_section,
    _mapping_text,
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
from host_brain.core.orchestration_settings import get_orchestration_settings
from host_brain.bond.user_identity import resolve_person_id
from host_brain.core.soft_fail import soft_fail  # OPT-3 Batch B：业务路径统一降级
import time

from host_brain.agent_runtime.contextual_answers import (
    _is_short_assistant_reply_followup,
    _load_continuity_turns,
    _maybe_build_contextual_memory_answer,
    _maybe_build_contextual_session_answer,
    _maybe_build_contextual_style_answer,
    _memory_recall_plan_should_bypass_direct_status_fastlane,
)
from host_brain.agent_runtime.fastpaths import (
    _is_low_risk_short_chat_route_reason,
    _maybe_build_previous_meaning_answer,
    _needs_selection_followup_retrieval_budget,
    _prefetch_retrieval_persona_inputs,
)
from host_brain.agent_runtime.postprocess import (
    _async_post_process,
)
from host_brain.agent_runtime.runtime_texts import (
    _load_runtime_text_value,
    _load_runtime_texts_section,
    _logger,
    _resolve_text_render_target_lang,
)
from host_brain.persona.session_hooks import (
    _resolve_retrieval_timeout_seconds,
    _resolve_selection_followup_retrieval_timeout_seconds,
)
from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts


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
    maybe_build_contextual_memory_answer_fn: Callable[..., str | None] | None = None,
    maybe_build_contextual_style_answer_fn: Callable[..., str | None] | None = None,
    maybe_build_contextual_session_answer_fn: Callable[..., str | None] | None = None,
    maybe_build_previous_meaning_answer_fn: Callable[..., str | None] | None = None,
    async_post_process_fn: Callable[..., Any] | None = None,
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
    _contextual_memory_answer = maybe_build_contextual_memory_answer_fn or partial(
        _maybe_build_contextual_memory_answer,
        load_continuity_turns=partial(
            _load_continuity_turns,
            application_ports=application_ports,
        ),
    )
    _contextual_style_answer = maybe_build_contextual_style_answer_fn or _maybe_build_contextual_style_answer
    _contextual_session_answer = maybe_build_contextual_session_answer_fn or _maybe_build_contextual_session_answer
    _previous_meaning_answer = maybe_build_previous_meaning_answer_fn or _maybe_build_previous_meaning_answer
    _post_process = async_post_process_fn or partial(
        _async_post_process,
        application_ports=application_ports,
    )
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

    # Record user turn for conversation history (moved from cortex_orchestrator)
    try:
        application_ports.record_user_turn(
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            content=str(getattr(request_body, "message", "") or ""),
            ts_ms=int(time.time() * 1000),
            lang=str(getattr(request_body, "render_target_lang", None) or "zh").strip().lower() or "zh",
            metadata={
                "input_source": str(getattr(request_body, "input_source", None) or "").strip(),
                "reply_channel": str(getattr(request_body, "reply_channel", None) or "").strip(),
                "platform": str(getattr(request_body, "reply_channel", None) or "").strip(),
                "reply_target": str(getattr(request_body, "reply_target", None) or getattr(request_body, "conversation_id", None) or "").strip(),
                "conversation_id": str(getattr(request_body, "reply_target", None) or getattr(request_body, "conversation_id", None) or "").strip(),
                "from_id": str(getattr(request_body, "from_id", None) or "").strip(),
                "msg_id": str(getattr(request_body, "msg_id", None) or "").strip(),
                "reply_to_msg_id": str(getattr(request_body, "reply_to_msg_id", None) or "").strip(),
                "attachments": list(getattr(request_body, "attachments", []) or []),
                "multimodal_observations": dict(getattr(request_body, "multimodal_observations", {}) or {}),
                "attachment_summary": str(getattr(request_body, "attachment_summary", "") or "").strip(),
            },
        )
    except Exception:
        pass

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

    render_target_lang = _resolve_text_render_target_lang(
        request_body=request_body,
        sanitized_message=sanitized_message,
    )

    if (
        governed_command_application is not None
        and callable(getattr(governed_command_application, "supports", None))
        and governed_command_application.supports(intent_draft)
    ):
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
            render_target_lang=render_target_lang,
            command_authority=dict(command_authority or {}),
        ):
            yield _emit_sse(dict(governed_event))
        return

    bond_summary: dict[str, Any] = {}

    with soft_fail("agent_runtime_shared.cortex_conversation_started", fallback=None):
        from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

        get_cortex_agent().emit_conversation_started(
            user_id=str(person_id or ""),
            session_id=str(session_id or ""),
            request_id=str(request_id or ""),
        )

    contextual_session_answer = _contextual_session_answer(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        render_target_lang=render_target_lang,
    )
    if contextual_session_answer:
        narrative = _sanitize_output(str(contextual_session_answer).strip())
        total_latency_ms = int(time.time() * 1000) - ts_ms
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "cortex_orchestrator:contextual_session_direct",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
        asyncio.create_task(
            _post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=sanitized_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary={},
                ts_ms=ts_ms,
                render_target_lang=render_target_lang,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=0,
                first_token_ms=None,
                provider_name="",
                model_id="",
                buffer_stream=False,
                delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "CONTEXTUAL_SESSION_DIRECT"},
                gov_decision=governor_result,
                pii_ingress_stats=_pii_ingress_stats,
                pii_egress_stats={},
                trace_stage_meta={
                    "cortex_orchestrator_used": True,
                    "executive_router_used": False,
                    "surface_reply_used": False,
                    "cortex_orchestrator_mode": "contextual_session_direct",
                    "reply_source": "contextual_session_direct",
                },
            )
        )
        return

    contextual_style_answer = _contextual_style_answer(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        render_target_lang=render_target_lang,
    )
    if contextual_style_answer:
        narrative = _sanitize_output(str(contextual_style_answer).strip())
        total_latency_ms = int(time.time() * 1000) - ts_ms
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "cortex_orchestrator:contextual_style_direct",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
        asyncio.create_task(
            _post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=sanitized_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary={},
                ts_ms=ts_ms,
                render_target_lang=render_target_lang,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=0,
                first_token_ms=None,
                provider_name="",
                model_id="",
                buffer_stream=False,
                delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "CONTEXTUAL_STYLE_DIRECT"},
                gov_decision=governor_result,
                pii_ingress_stats=_pii_ingress_stats,
                pii_egress_stats={},
                trace_stage_meta={
                    "cortex_orchestrator_used": True,
                    "executive_router_used": False,
                    "surface_reply_used": False,
                    "cortex_orchestrator_mode": "contextual_style_direct",
                    "reply_source": "contextual_style_direct",
                },
            )
        )
        return

    clarification_direct_answer = (
        _previous_meaning_answer(
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            user_message=sanitized_message,
            render_target_lang=render_target_lang,
        )
        if get_orchestration_settings().clarification_direct_enabled
        else None
    )
    if clarification_direct_answer:
        narrative = _sanitize_output(str(clarification_direct_answer).strip())
        total_latency_ms = int(time.time() * 1000) - ts_ms
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "cortex_orchestrator:clarification_direct",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
        asyncio.create_task(
            _post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=sanitized_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary={},
                ts_ms=ts_ms,
                render_target_lang=render_target_lang,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=0,
                first_token_ms=None,
                provider_name="",
                model_id="",
                buffer_stream=False,
                delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "CLARIFICATION_DIRECT"},
                gov_decision=governor_result,
                pii_ingress_stats=_pii_ingress_stats,
                pii_egress_stats={},
                trace_stage_meta={
                    "cortex_orchestrator_used": True,
                    "executive_router_used": False,
                    "surface_reply_used": False,
                    "cortex_orchestrator_mode": "clarification_direct",
                },
            )
        )
        return

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

    try:
        runtime_task_status_templates = _load_runtime_texts_section(
            "runtime_task_status",
            render_target_lang=render_target_lang,
        )
        runtime_task_registry = application_ports.runtime_task_registry()
        if runtime_task_registry.get_by_request_id(request_id) is None:
            runtime_task_registry.create_task(
                task_kind="chat_request",
                target_id="cortex_orchestrator",
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                user_message=sanitized_message,
                detail=_mapping_text(
                    runtime_task_status_templates,
                    "pre_dispatch_detail",
                ),
                progress_message=_mapping_text(
                    runtime_task_status_templates,
                    "pre_dispatch_progress",
                ),
                meta={"stage": "cortex_pre_dispatch"},
            )
    except Exception as task_exc:
        _logger.debug("[%s] cortex_orchestrator runtime task init failed: %s", request_id, task_exc)

    retrieval_task: asyncio.Task | None = None
    retrieval_evidence_items: list[dict[str, Any]] = []
    retrieval_summary: dict[str, Any] = {"status": "SKIPPED"}
    bond_evidence: dict[str, Any] | None = None
    retrieval_motive_state: dict[str, Any] = {}
    prefetch_persona_inputs_ms = 0
    retrieval_wait_ms = 0
    prompt_filter_ms = 0
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

    # 人格聊天链不再使用 slot 投影。
    # 运行时只保留五路召回原始结果，由 prompt 直接消费检索证据；
    # 最近上下文则继续由 continuity 链单独管理。

    llm_runtime_started_ms = int(time.time() * 1000)
    llm_runtime = await application_ports.resolve_host_llm_runtime_async(
        rollout_key=session_id,
        scene_type="CHAT",
    )
    llm_runtime_resolve_ms = max(0, int(time.time() * 1000) - llm_runtime_started_ms)

    if llm_runtime is not None and not llm_runtime.get("available"):
        fallback_narrative = _load_runtime_text_value(
            "llm_unavailable_fallback",
            render_target_lang=render_target_lang,
        )
        total_latency_ms = int(time.time() * 1000) - ts_ms
        try:
            asyncio.create_task(
                _post_process(
                    request_id=request_id,
                    person_id=person_id,
                    session_id=session_id,
                    user_message=sanitized_message,
                    narrative=fallback_narrative,
                    emotion_context=emotion_context,
                    bond_summary=bond_summary,
                    ts_ms=ts_ms,
                    render_target_lang=render_target_lang,
                    total_latency_ms=total_latency_ms,
                    llm_latency_ms=0,
                    first_token_ms=None,
                    provider_name=str(llm_runtime.get("provider_name") or "fallback"),
                    model_id=str(llm_runtime.get("model_id") or "llm-unavailable-fallback"),
                    buffer_stream=False,
                    delegated_agents=[],
                    retrieval_summary=(retrieval_summary or {"status": "SKIPPED", "reason_code": "LLM_UNAVAILABLE"}),
                    gov_decision=governor_result,
                    pii_ingress_stats=_pii_ingress_stats,
                    pii_egress_stats={},
                    retrieval_evidence_items=list(retrieval_evidence_items or []),
                    trace_stage_meta={
                        "cortex_orchestrator_used": True,
                        "executive_router_used": False,
                        "surface_reply_used": False,
                        "cortex_orchestrator_mode": "llm_unavailable",
                    },
                )
            )
        except Exception as post_process_exc:
            _logger.debug("[%s] cortex_orchestrator llm unavailable post_process failed: %s", request_id, post_process_exc)
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "LLM_UNAVAILABLE",
                "request_id": request_id,
                "narrative": fallback_narrative,
            }
        )
        return

    executive_router_sse_generator = application_ports.executive_router_sse_generator

    _logger.info(
        "[%s] cortex_orchestrator stage_summary prefetch_ms=%s retrieval_wait_ms=%s prompt_filter_ms=%s "
        "llm_runtime_resolve_ms=%s request_to_executive_ms=%s",
        request_id,
        prefetch_persona_inputs_ms,
        retrieval_wait_ms,
        prompt_filter_ms,
        llm_runtime_resolve_ms,
        max(0, int(time.time() * 1000) - orchestrator_started_ms),
    )

    async for event in executive_router_sse_generator(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        governor_result=gov,
        intent_id=intent_id,
        metrics=metrics,
        person_id=person_id,
        sanitized_message=sanitized_message,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        pii_ingress_stats=_pii_ingress_stats,
    ):
        yield event
