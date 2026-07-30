"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any, Callable, Sequence
from collections.abc import Mapping
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
from host_brain.core.orchestration_settings import get_orchestration_settings
from host_brain.core.soft_fail import soft_fail  # OPT-3 Batch B：业务路径统一降级
import time

from host_brain.agent_runtime.runtime_texts import (
    _logger,
    _run_in_post_process_executor,
)
from host_brain.persona.session_hooks import (
    _infer_emotion_tag_for_tts,
    _resolve_persona_session_emotion,
    _run_persona_session_end_hooks,
    _run_surface_persona_session_end_sync,
)
from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _require_durable_assistant_turn_result(value: Any) -> None:
    result = dict(value or {}) if isinstance(value, Mapping) else {}
    status = str(result.get("status") or "").strip().upper()
    reason_code = str(result.get("reason_code") or "").strip().upper()
    if status == "OK":
        return
    if status == "SKIPPED" and reason_code in {
        "DUPLICATE_CONVERSATION_TURN",
        "DUPLICATE_COMMAND",
    }:
        return
    raise RuntimeError(
        (reason_code or "CONVERSATION_ASSISTANT_TURN_NOT_DURABLE")
        + ": accepted assistant turn was not durably recorded"
    )


def _require_durable_life_memory_result(
    value: Mapping[str, Any],
    *,
    request_id: str,
) -> None:
    result = dict(value or {})
    status = str(result.get("status") or "").strip().upper()
    receipt_request_id = str(result.get("request_id") or "").strip()
    if receipt_request_id != str(request_id or "").strip():
        raise RuntimeError(
            "LIFE_MEMORY_RECEIPT_BINDING_MISMATCH: durable Memory receipt "
            "does not belong to the accepted request"
        )
    # SKIPPED is a durable policy decision, not a missing write.  COMMITTED
    # includes both a new admission and an idempotent replay of that receipt.
    if status in {"COMMITTED", "SKIPPED"}:
        return
    raise RuntimeError(
        str(result.get("reason_code") or "LIFE_MEMORY_NOT_DURABLE")
        + ": accepted chat Memory admission has no durable receipt"
    )


async def _async_post_process(
    *,
    application_ports: AgentRuntimeApplicationPorts,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    narrative: str,
    emotion_context: dict[str, Any],
    bond_summary: dict[str, Any] | None,
    ts_ms: int,
    render_target_lang: str,
    total_latency_ms: int = 0,
    llm_latency_ms: int = 0,
    first_token_ms: int | None = None,
    provider_name: str = "",
    model_id: str = "",
    buffer_stream: bool = False,
    delegated_agents: list[dict[str, Any]] | None = None,
    retrieval_summary: dict[str, Any] | None = None,
    gov_decision: dict[str, Any] | None = None,
    pii_ingress_stats: dict[str, Any] | None = None,
    pii_egress_stats: dict[str, Any] | None = None,
    llm_input_digest: str | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    motive_state: Mapping[str, Any] | None = None,
    reply_plan: Mapping[str, Any] | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    multimodal_observations: dict[str, Any] | None = None,
    attachment_summary: str = "",
    reply_channel: str | None = None,
    reply_target: str | None = None,
) -> None:
    normalized_trace_stage_meta = _normalize_trace_stage_meta(trace_stage_meta)
    voice_outputs_enabled = bool(normalized_trace_stage_meta.get("voice_outputs_enabled"))
    suppress_final_reply = bool(normalized_trace_stage_meta.get("suppress_final_reply"))
    skip_heavy_persona_postprocess = bool(
        normalized_trace_stage_meta.get("skip_heavy_persona_postprocess")
    )
    skip_default_mode_on_conversation_end = bool(
        normalized_trace_stage_meta.get("skip_default_mode_on_conversation_end")
    )
    surface_main_chain_used = bool(normalized_trace_stage_meta.get("surface_main_chain_used"))
    # A Digital Life projection is already running behind a durable outbox.
    # Conversation and LifeMemory writes performed by that projection are
    # therefore part of the durable commit chain: hiding their failure would
    # incorrectly acknowledge the projection and make the loss permanent.
    durable_projection_mode = bool(
        normalized_trace_stage_meta.get("life_projection_mode")
    )

    try:
        from host_brain.tribe.default_mode_agent import mark_recent_foreground_interaction  # noqa: PLC0415

        mark_recent_foreground_interaction(ts_ms=int(time.time() * 1000))
    except Exception:
        pass

    if surface_main_chain_used:
        # Relationship outcomes are committed by the canonical Life
        # acceptance/aftereffect chain.  Calling the historical Surface flow
        # after a reply would recreate an unbounded Bond writer that cannot
        # participate in replay or recovery.
        normalized_trace_stage_meta = {
            **normalized_trace_stage_meta,
            "surface_emotion_bond_flow": {
                "status": "RETIRED_CANONICAL_LIFE",
                "authority": "CANONICAL_LIFE_JOURNAL",
                "reason_code": "LEGACY_SURFACE_BOND_FLOW_RETIRED",
            },
        }

    try:
        await application_ports.persist_post_process_audit(
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            narrative=narrative,
            emotion_context=emotion_context,
            bond_summary=bond_summary,
            ts_ms=ts_ms,
            total_latency_ms=total_latency_ms,
            llm_latency_ms=llm_latency_ms,
            first_token_ms=first_token_ms,
            provider_name=provider_name,
            model_id=model_id,
            buffer_stream=buffer_stream,
            delegated_agents=delegated_agents,
            retrieval_summary=retrieval_summary,
            gov_decision=gov_decision,
            pii_ingress_stats=pii_ingress_stats,
            pii_egress_stats=pii_egress_stats,
            llm_input_digest=llm_input_digest,
            retrieval_evidence_items=retrieval_evidence_items,
            motive_state=motive_state,
            reply_plan=reply_plan,
            trace_stage_meta=normalized_trace_stage_meta,
        )
    except Exception as exc:
        _logger.debug("[%s] cortex_orchestrator post_process audit persist failed: %s", request_id, exc)

    try:
        if suppress_final_reply:
            _logger.debug("[%s] cortex_orchestrator skipped assistant turn: suppressed final reply", request_id)
        else:
            fallback_used = _resolve_persisted_fallback_flag(
                trace_stage_meta=normalized_trace_stage_meta,
                gov_decision=gov_decision,
                narrative=narrative,
                provider_name=provider_name,
                model_id=model_id,
            )
            assistant_turn_result = application_ports.record_assistant_turn(
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                content=narrative,
                ts_ms=int(time.time() * 1000),
                lang=render_target_lang,
                reply_plan=reply_plan,
                metadata={
                    "provider_name": provider_name,
                    "model_id": model_id,
                    "fallback": fallback_used,
                    "message_kind": "final_reply",
                    "reply_source": str(normalized_trace_stage_meta.get("reply_source") or ("fallback" if fallback_used else "full_model")).strip(),
                    "degraded": bool(fallback_used),
                    "reply_envelope": dict(normalized_trace_stage_meta.get("reply_envelope") or {}),
                },
            )
            if durable_projection_mode:
                _require_durable_assistant_turn_result(assistant_turn_result)
            # Conversation Experience is intentionally read-only in the
            # governed Surface path.  The old mutable store accepted reply
            # updates with no Task-bound proposal/activation receipt and even
            # swallowed its own persistence failures.  Accepted turns and
            # LifeMemory remain durable projections here; any new experience
            # evolution must enter the later proposal/activation owner.
    except Exception as exc:
        if durable_projection_mode:
            raise
        _logger.debug("[%s] cortex_orchestrator post_process conversation_turn failed: %s", request_id, exc)

    try:
        from host_brain.airlock.quarantine import classify_ingress  # noqa: PLC0415

        await _run_in_post_process_executor(classify_ingress, narrative)
    except Exception as exc:
        _logger.debug("[%s] cortex_orchestrator post_process airlock classify failed: %s", request_id, exc)

    try:
        if not suppress_final_reply:
            from host_brain.bond.user_identity import resolve_bond_user_id  # noqa: PLC0415
            from host_brain.memory.life_memory.contracts import ChatMemoryCandidate  # noqa: PLC0415

            life_user = user_message
            life_narrative = narrative
            with soft_fail("agent_runtime_shared.pii_scrub_life_memory_write", fallback=None):
                from host_brain.airlock.pii_filter import _check_channel, pii_scrub_if_enabled  # noqa: PLC0415

                if _check_channel("MEMORY"):
                    life_user = pii_scrub_if_enabled(user_message, context="life_memory_write_user")
                    life_narrative = pii_scrub_if_enabled(narrative, context="life_memory_write_ai")
            memory_emotion_tag = str(
                emotion_context.get("surface_user_emotion_tag")
                or emotion_context.get("user_emotion_tag")
                or emotion_context.get("emotion_tag")
                or "NEUTRAL"
            ).strip().upper() or "NEUTRAL"
            memory_valence, memory_arousal = _resolve_persona_session_emotion(
                {**dict(emotion_context or {}), "emotion_tag": memory_emotion_tag}
            )
            memory_reply_plan = dict(reply_plan or {}) if isinstance(reply_plan, Mapping) else {}
            memory_intent_id = str(
                memory_reply_plan.get("intent_id")
                or memory_reply_plan.get("task_mode")
                or memory_reply_plan.get("primary_topic")
                or memory_reply_plan.get("response_move")
                or request_id
            ).strip() or request_id
            memory_candidate = ChatMemoryCandidate(
                request_id=request_id,
                intent_id=memory_intent_id,
                person_id=resolve_bond_user_id(person_id or None, session_id=session_id or None),
                session_id=session_id,
                user_message=life_user,
                assistant_reply=life_narrative,
                ts_ms=ts_ms,
                emotion_tag=memory_emotion_tag,
                emotion_valence=memory_valence,
                emotion_arousal=memory_arousal,
                turn_count=max(1, _safe_int(emotion_context.get("turn_count"), default=1)),
                attachments=tuple(attachments or ()),
                multimodal_observations=dict(multimodal_observations or {}),
                attachment_summary=str(attachment_summary or "").strip(),
                metadata={
                    "trace_stage_meta": dict(normalized_trace_stage_meta or {}),
                    "reply_source": str(normalized_trace_stage_meta.get("reply_source") or ""),
                    "reply_channel": str(reply_channel or "").strip() or None,
                    "reply_target": str(reply_target or "").strip() or None,
                    "source": "surface_main_chain" if surface_main_chain_used else "agent_runtime",
                },
            )
            life_memory_receipt = await _run_in_post_process_executor(
                application_ports.life_memory_service().admit_chat,
                memory_candidate,
            )
            life_memory_write = (
                life_memory_receipt.as_dict()
                if hasattr(life_memory_receipt, "as_dict")
                else dict(life_memory_receipt or {})
            )
            if durable_projection_mode:
                _require_durable_life_memory_result(
                    life_memory_write,
                    request_id=request_id,
                )
            life_memory_write["source"] = (
                "surface_main_chain" if surface_main_chain_used else "agent_runtime"
            )
            normalized_trace_stage_meta = {
                **normalized_trace_stage_meta,
                "life_memory_write": life_memory_write,
            }
            if surface_main_chain_used:
                normalized_trace_stage_meta = {
                    **normalized_trace_stage_meta,
                    # Compatibility trace only; this is the same receipt, not a
                    # second writer invocation.
                    "surface_memory_write": dict(life_memory_write),
                }
    except Exception as exc:
        if durable_projection_mode:
            raise
        _logger.debug("[%s] cortex_orchestrator post_process life memory failed: %s", request_id, exc)

    if bool(normalized_trace_stage_meta.get("life_projection_mode")):
        # AW-04 projection replay owns only durable audit, conversation,
        # Memory and Bond projections above. Persona/Tribe mutations and all
        # VTS/TTS transports below are separate Life intents/outbox messages;
        # replaying them here would recreate parallel writers and duplicate
        # external effects.
        return

    if skip_heavy_persona_postprocess and surface_main_chain_used:
        try:
            surface_session_end = await _run_in_post_process_executor(
                _run_surface_persona_session_end_sync,
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=user_message,
                emotion_context=emotion_context,
            )
            normalized_trace_stage_meta = {
                **normalized_trace_stage_meta,
                "surface_persona_session_end": surface_session_end,
            }
        except Exception as exc:
            _logger.debug("[%s] surface persona session_end failed: %s", request_id, exc)

    if skip_heavy_persona_postprocess:
        _logger.debug(
            "[%s] cortex_orchestrator post_process skip persona session_end",
            request_id,
        )
    else:
        try:
            await _run_persona_session_end_hooks(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=user_message,
                emotion_context=emotion_context,
            )
        except Exception as exc:
            _logger.debug("[%s] cortex_orchestrator post_process persona session_end failed: %s", request_id, exc)

    _logger.debug(
        "[%s] cortex_orchestrator legacy Bond post-process is retired; "
        "canonical Life acceptance owns relationship effects",
        request_id,
    )

    try:
        from host_brain.tribe.cortex_agent import get_cortex_agent as _get_cortex  # noqa: PLC0415

        ec = dict(emotion_context or {})
        event_user_id = str(person_id or "")
        if surface_main_chain_used:
            from host_brain.bond.user_identity import resolve_bond_user_id  # noqa: PLC0415

            event_user_id = resolve_bond_user_id(event_user_id or None, session_id=session_id or None)
        _get_cortex().emit_conversation_ended(
            user_id=event_user_id,
            session_id=str(session_id or ""),
            request_id=str(request_id or ""),
            emotion_tag=str(ec.get("emotion_tag") or "NEUTRAL").strip().upper(),
            emotion_intensity=float(ec.get("emotion_intensity") or 0.5),
            skip_default_mode_inner_activity=skip_default_mode_on_conversation_end,
            summary={
                "user_message": str(user_message or ""),
                "user_message_preview": str(user_message or "")[:64],
                "assistant_reply": str(narrative or "")[:512],
                "ts_ms": int(ts_ms),
            },
        )
    except Exception as exc:
        _logger.debug("[%s] cortex_orchestrator post_process conversation_ended tribe event failed: %s", request_id, exc)

    try:
        await application_ports.drive_stream_vts_emotion(
            request_id=request_id,
            emotion_context=emotion_context,
            ts_ms=ts_ms,
        )
    except Exception as exc:
        _logger.debug("[%s] cortex_orchestrator post_process vts failed: %s", request_id, exc)

    try:
        orchestration_settings = get_orchestration_settings()
        skip_bulk_tts = orchestration_settings.tts_sentence_stream_enabled
        if orchestration_settings.stream_tts_enabled and narrative.strip() and not skip_bulk_tts:
            from host_brain.channels.embodiment import submit_voice_command  # noqa: PLC0415
            base_etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper()
            tts_route = application_ports.resolve_tts_route(
                text=narrative,
                emotion_context={**dict(emotion_context), "emotion_tag": base_etag},
                bond_summary=bond_summary or None,
            )
            etag = str(tts_route.get("emotion_tag") or _infer_emotion_tag_for_tts(narrative, base_etag)).strip()
            intensity_raw = emotion_context.get("emotion_intensity")
            intensity: float | None = None
            if intensity_raw is not None:
                try:
                    intensity = max(0.0, min(1.0, float(intensity_raw)))
                except (TypeError, ValueError):
                    pass
            tts_result = await submit_voice_command(
                action="speak",
                payload={
                    "text": narrative,
                    "session_id": session_id,
                    "speed": float(tts_route.get("speed") or 1.0),
                    "pitch": float(tts_route.get("pitch") or 0.0),
                    "emotion_tag": etag,
                    "emotion_intensity": intensity,
                    "style_note": str(tts_route.get("style_note") or ""),
                    "lang_hint": str(tts_route.get("lang_hint") or render_target_lang),
                },
                request_id=request_id,
                idempotency_key=f"{request_id}:bulk-tts",
            )

            try:
                application_ports.audit_ledger().log_voice_event(
                    {
                        "request_id": request_id,
                        "session_id": session_id,
                        "event_type": "TTS_SPEAK",
                        "direction": "EGRESS",
                        "status": str(getattr(tts_result, "status", "OK")),
                        "latency_ms": int(getattr(tts_result, "latency_ms", 0)),
                        "payload": {
                            "text_len": len(narrative),
                            "emotion_tag": etag,
                            "speed": float(tts_route.get("speed") or 1.0),
                            "pitch": float(tts_route.get("pitch") or 0.0),
                            "lang_hint": str(tts_route.get("lang_hint") or render_target_lang),
                        },
                        "ts_ms": int(time.time() * 1000),
                    }
                )
            except Exception:
                pass
    except Exception as exc:
        _logger.debug("[%s] cortex_orchestrator post_process tts failed: %s", request_id, exc)

    if skip_heavy_persona_postprocess:
        _logger.debug(
            "[%s] cortex_orchestrator post_process skip drift/growth",
            request_id,
        )
    else:
        try:
            from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

            drift_result = await _run_in_post_process_executor(
                get_prefrontal_agent().run_persona_drift_cycle,
                llm_output_text=narrative,
                user_input_text=user_message,
            )
            drift_level = str(dict(drift_result or {}).get("alert_level") or "").strip().upper()
            if drift_level in ("MODERATE", "SEVERE"):
                _logger.info(
                    "[%s] 人格漂移检测: level=%s dims=%s",
                    request_id,
                    drift_level,
                    dict(drift_result or {}).get("drift_dims"),
                )
        except Exception as exc:
            _logger.debug("[%s] cortex_orchestrator post_process drift_detector failed: %s", request_id, exc)

        try:
            from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

            growth_payload = await _run_in_post_process_executor(
                get_prefrontal_agent().run_growth_feedback_cycle,
                session_id=session_id,
                user_message=user_message,
                assistant_reply=narrative,
            )
            if bool(dict(growth_payload or {}).get("applied")):
                _logger.info(
                    "[%s] 人格成长: user=%s rules=%s",
                    request_id,
                    dict(growth_payload or {}).get("user_id"),
                    dict(growth_payload or {}).get("triggered_rules"),
                )
        except Exception as exc:
            _logger.debug("[%s] cortex_orchestrator post_process growth_feedback failed: %s", request_id, exc)


__all__ = [
    "_format_template_text",
    "_load_template_mapping",
    "_load_runtime_texts",
    "_mapping_section",
    "_mapping_text",
    "_parse_bool_env",
    "_sse",
    "_airlock_sanitize_input",
    "_airlock_sanitize_output",
    "_build_reply_preview",
    "_load_continuity_turns",
    "_detect_recent_turn_query_role",
    "_looks_like_active_task_context",
    "_extract_delegate_visible_text",
    "_render_delegated_result_naturally",
    "_resolve_retrieval_timeout_seconds",
    "_normalize_retrieval_summary_for_audit",
    "_surface_memory_debug_trace_for_audit",
    "_coerce_trace_mapping",
    "_coerce_trace_score",
    "_coerce_trace_bool",
    "_summarize_motive_state_for_trace",
    "_summarize_reply_plan_for_trace",
    "_summarize_emotion_context_for_trace",
    "_summarize_persona_resident_memory_for_trace",
    "_normalize_trace_stage_meta",
    "_resolve_envelope_confidence",
    "_build_trace_output_digest",
    "_detect_trace_fallback_used",
    "_build_trace_agent_envelope",
    "_build_trace_stage_node",
    "_persist_post_process_audit",
    "_build_subagent_gateway_from_runtime",
    "_cortex_orchestrator_sse_generator_impl",
    "_maybe_build_task_progress_fastpath",
    "_maybe_build_contextual_memory_answer",
    "_maybe_build_contextual_session_answer",
    "_maybe_build_contextual_style_answer",
    "_maybe_build_recent_turn_answer",
    "_maybe_build_previous_meaning_answer",
    "_extract_delegate_from_llm_output",
    "_extract_visible_text",
    "_build_retrieval_bond_summary",
    "_prefetch_retrieval_persona_inputs",
    "_resolve_persona_session_emotion",
    "_run_persona_session_end_hooks",
    "_infer_emotion_tag_for_tts",
    "_resolve_persisted_fallback_flag",
    "_async_post_process",
]


def _resolve_persisted_fallback_flag(
    *,
    trace_stage_meta: Mapping[str, Any] | None,
    gov_decision: Mapping[str, Any] | None,
    narrative: str,
    provider_name: str,
    model_id: str,
) -> bool:
    normalized_trace_stage_meta = _normalize_trace_stage_meta(trace_stage_meta)
    explicit_reply_fallback = _coerce_trace_bool(normalized_trace_stage_meta.get("reply_used_fallback"))
    if explicit_reply_fallback is not None:
        return bool(explicit_reply_fallback)

    explicit_trace_fallback = _coerce_trace_bool(normalized_trace_stage_meta.get("fallback_used"))
    if explicit_trace_fallback is not None:
        return bool(explicit_trace_fallback)

    return _detect_trace_fallback_used(
        reason_code=str((gov_decision or {}).get("reason_code") or ""),
        degrade_code=str(normalized_trace_stage_meta.get("degrade_code") or ""),
        output_data={"narrative": narrative, "provider_name": provider_name, "model_id": model_id},
    )
