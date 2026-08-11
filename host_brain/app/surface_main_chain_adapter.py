"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any
from collections.abc import AsyncGenerator, Mapping, Sequence
from host_brain.input_runtime.reply_envelope import ReplyEnvelope
from host_brain.digital_life.companion_voice import japanese_companion_voice_enabled
from host_brain.persona.surface_main_chain import (
    SurfaceMainChain,
    SurfaceMainChainInput,
    SurfaceMainChainResult,
    compile_surface_main_chain_input,
)
import asyncio
import hashlib
from host_brain.persona.surface_runtime import evaluate_surface_model_response_for_retry
import json
import os
import time

from host_brain.surface_runtime.delivery_audit import (
    _delegated_agents_from_sub_agent_context,
    _merge_delegated_agent_trace,
    _public_result_trace,
    _submit_surface_affective_turn_planned,
    _submit_surface_sentence_delivery_intent,
    _submit_surface_stream_cancelled,
    _submit_surface_foreground_completed,
    _surface_model_audit_fields,
    _surface_timings_ms,
)
from host_brain.app.sentence_splitter import SentenceSplitter
from host_brain.persona.isolated_surface_harness import (
    build_isolated_surface_messages,
    build_native_stream_surface_messages,
)
from host_brain.surface_runtime.fact_compiler import (
    _coerce_int,
    _safe_mapping,
    _sanitize_account_login_reply,
)
from host_brain.surface_runtime.input_compiler import (
    build_surface_main_chain_input_from_surface_reply_input,
)
from host_brain.surface_runtime.memory_grounding import (
    _apply_surface_sub_agent_result_guard,
    _sanitize_main_chain_meta_reply,
    _surface_memory_packet_for_output_guard,
    _surface_perception_evidence_gate_text,
)
from host_brain.surface_runtime.quality_guards import (
    _apply_surface_text_quality_guard,
    _memory_packet_payload_for_guard,
    _raw_memory_packet_payload_from_agent_input,
)
from host_brain.surface_runtime.affective_streaming import (
    affective_turn_plan_from_trace,
    gate_stream_sentence,
    should_use_native_sentence_stream,
)
from host_brain.surface_runtime.subagent_context import (
    _surface_sub_agent_id,
    _surface_sub_agent_result_text,
)
from host_brain.surface_runtime.ports import (
    SurfaceRuntimePorts,
)


from host_brain.app.surface_main_chain_support import (
    _final_stream_usage_is_attested,
    _governed_model_budget_failure_reason,
    _governed_model_budget_guard,
    _governed_task_delivery_authority,
    _has_governed_model_budget,
    _native_stream_failure_reason,
    _record_surface_memory_guard_packet,
    _sse,
    _surface_active_knowledge_prompt_evidence,
    _surface_expression_prompt_evidence,
    _surface_failure_fallback_text,
    _surface_main_chain_response_format,
    _surface_model_deadline_ms,
    _surface_model_request_profile,
)
from host_brain.app.surface_affective_native_stream import (
    _buffered_affective_candidate_count,
    _buffered_affective_candidate_messages,
    _expression_delivery_sse,
    _native_affective_sentence_stream,
    _native_affective_stream_preparation,
    _select_buffered_affective_candidate,
)
from host_brain.app.surface_affective_delivery import (
    _canonical_affective_deferred_delivery,
    _canonical_affective_deterministic_delivery,
    _gated_deterministic_affective_sentences,
)


async def run_surface_main_chain_for_surface_reply_input(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: dict[str, Any] | None = None,
) -> SurfaceMainChainResult:
    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    governed_model_budget = _governed_model_budget_guard(trace_stage_meta)
    surface_input = build_surface_main_chain_input_from_surface_reply_input(
        agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    compiled_request = compile_surface_main_chain_input(surface_input).request
    affective_turn_plan = affective_turn_plan_from_trace(trace_stage_meta)
    canonical_companion_act = (
        str(
            _safe_mapping(affective_turn_plan.get("companion_act")).get("kind")
            or ""
        )
        .strip()
        .upper()
    )
    canonical_repair_context = (
        canonical_companion_act == "REPAIR"
    )
    utterance_frame = _safe_mapping(affective_turn_plan.get("utterance_frame"))
    canonical_contained_hurt_repair_context = bool(
        canonical_repair_context
        and (
            str(utterance_frame.get("self_stance") or "").upper()
            == "OWN_REPAIR_WITH_CONTAINED_HURT"
            or str(utterance_frame.get("response_move") or "").upper()
            == "ACKNOWLEDGE_IMPACT_WITH_HONEST_HURT"
        )
    )
    canonical_mixed_context = canonical_companion_act == "HOLD_COMPLEXITY"
    canonical_give_space_context = canonical_companion_act in {
        "GIVE_SPACE",
        "SILENCE",
    }
    canonical_autonomy_context = canonical_companion_act == "RESPECT_AUTONOMY"
    canonical_ambiguous_context = bool(
        canonical_companion_act == "COMFORT"
        and str(getattr(compiled_request.control, "task_mode", "") or "")
        .strip()
        .lower()
        == "ambiguous_affect"
    )
    canonical_jealousy_context = canonical_companion_act == "CONTAINED_JEALOUSY"
    _record_surface_memory_guard_packet(
        agent_input=agent_input,
        surface_input=surface_input,
        trace_stage_meta=trace_stage_meta,
    )
    affective_candidate_count = _buffered_affective_candidate_count(trace_stage_meta)
    affective_candidate_degraded = False

    async def _generate(messages: list[dict[str, str]]) -> str:
        from host_brain.model_runtime.contracts import (  # noqa: PLC0415
            ModelAttemptRequest,
            PromptRef,
            RetryDirective,
        )
        from host_brain.model_runtime.live_prompts import (  # noqa: PLC0415
            SURFACE_MAIN_JSON_PROMPT_ID,
            SURFACE_MAIN_TEXT_PROMPT_ID,
        )
        from host_brain.model_runtime.trace import model_attempt_result_trace  # noqa: PLC0415

        prompt_id = (
            SURFACE_MAIN_JSON_PROMPT_ID
            if _surface_main_chain_response_format() is not None
            else SURFACE_MAIN_TEXT_PROMPT_ID
        )

        def _retry_evaluator(response: Any, attempt: Any) -> Any:
            nonlocal affective_candidate_degraded
            if affective_candidate_count is not None:
                evaluation_trace: dict[str, Any] = {}
                allow_single_degradation = int(
                    getattr(attempt, "attempt_index", 1) or 1
                ) >= 2
                try:
                    _select_buffered_affective_candidate(
                        raw_output=str(response.content or ""),
                        compiled_request=compiled_request,
                        expected_candidate_count=affective_candidate_count,
                        trace_stage_meta=evaluation_trace,
                        allow_single_degradation=allow_single_degradation,
                    )
                except RuntimeError as exc:
                    retry_reason = str(exc).split(":", 1)[0]
                    repaired_messages = _buffered_affective_candidate_messages(
                        attempt.prompt.messages,
                        candidate_count=affective_candidate_count,
                        retry_reason=retry_reason,
                        canonical_repair_context=canonical_repair_context,
                        canonical_contained_hurt_repair_context=(
                            canonical_contained_hurt_repair_context
                        ),
                        canonical_mixed_context=canonical_mixed_context,
                        canonical_give_space_context=canonical_give_space_context,
                        canonical_autonomy_context=canonical_autonomy_context,
                        canonical_ambiguous_context=canonical_ambiguous_context,
                        canonical_jealousy_context=canonical_jealousy_context,
                    )
                    return RetryDirective(
                        retry=True,
                        reason=retry_reason,
                        rendered_prompt=attempt.prompt.with_messages(repaired_messages),
                        reuse_target=True,
                        metadata={
                            "expected_candidate_count": affective_candidate_count,
                        },
                    )
                selection = _safe_mapping(
                    evaluation_trace.get("affective_candidate_selection")
                )
                affective_candidate_degraded = selection.get(
                    "selection_status"
                ) in {
                    "DEGRADED_SINGLE_AFTER_RETRY",
                    "DEGRADED_LOCAL_REPAIR_AFTER_RETRY",
                    "DEGRADED_LOW_QUALITY_AFTER_RETRY",
                }
                if (
                    not bool(selection.get("selected_passed"))
                    and int(getattr(attempt, "attempt_index", 1) or 1) < 2
                ):
                    retry_reason = "AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR"
                    repaired_messages = _buffered_affective_candidate_messages(
                        attempt.prompt.messages,
                        candidate_count=affective_candidate_count,
                        retry_reason=retry_reason,
                        canonical_repair_context=canonical_repair_context,
                        canonical_contained_hurt_repair_context=(
                            canonical_contained_hurt_repair_context
                        ),
                        canonical_mixed_context=canonical_mixed_context,
                        canonical_give_space_context=canonical_give_space_context,
                        canonical_autonomy_context=canonical_autonomy_context,
                        canonical_ambiguous_context=canonical_ambiguous_context,
                        canonical_jealousy_context=canonical_jealousy_context,
                    )
                    return RetryDirective(
                        retry=True,
                        reason=retry_reason,
                        rendered_prompt=attempt.prompt.with_messages(repaired_messages),
                        reuse_target=True,
                        metadata={
                            "expected_candidate_count": affective_candidate_count,
                            "selected_reason_codes": list(
                                selection.get("selected_reason_codes") or []
                            )[:16],
                        },
                    )
                return RetryDirective.accept()
            evaluation = evaluate_surface_model_response_for_retry(
                compiled_request,
                raw_output=str(response.content or ""),
                current_messages=attempt.prompt.messages,
                # Missing Japanese speech must earn one repair attempt, but it
                # never blocks the visible Chinese reply after that attempt.
                # The delivery layer then degrades voice explicitly instead of
                # feeding Chinese text into a Japanese acoustic model.
                allow_voice_degradation=int(attempt.attempt_index) > 1,
            )
            if not evaluation.retry:
                return RetryDirective.accept()
            return RetryDirective(
                retry=True,
                reason=evaluation.reason,
                rendered_prompt=attempt.prompt.with_messages(
                    _buffered_affective_candidate_messages(
                        evaluation.messages,
                        candidate_count=affective_candidate_count,
                    )
                ),
                reuse_target=True,
                metadata={"violation_codes": list(evaluation.violation_codes)},
            )

        generate_started = time.perf_counter()
        model_deadline_ms = _surface_model_deadline_ms(governed_model_budget)
        if governed_model_budget is not None and model_deadline_ms <= 0:
            raise RuntimeError("MODEL_BUDGET_DEADLINE_EXHAUSTED")
        model_scene_type, model_cognitive_tier, model_route_lane = (
            _surface_model_request_profile(trace_stage_meta)
        )
        if isinstance(trace_stage_meta, dict):
            trace_stage_meta["surface_model_route"] = {
                "scene_type": model_scene_type,
                "cognitive_tier": model_cognitive_tier,
                "route_lane": model_route_lane,
            }
        rendered_messages = _buffered_affective_candidate_messages(
            (
                build_native_stream_surface_messages(compiled_request)
                if affective_candidate_count is not None
                else messages
            ),
            candidate_count=affective_candidate_count,
            canonical_repair_context=canonical_repair_context,
            canonical_contained_hurt_repair_context=(
                canonical_contained_hurt_repair_context
            ),
            canonical_mixed_context=canonical_mixed_context,
            canonical_give_space_context=canonical_give_space_context,
            canonical_autonomy_context=canonical_autonomy_context,
            canonical_ambiguous_context=canonical_ambiguous_context,
            canonical_jealousy_context=canonical_jealousy_context,
        )
        expression_prompt_evidence = _surface_expression_prompt_evidence(
            messages=rendered_messages,
            style_anchors=tuple(
                getattr(compiled_request.control, "style_anchor", ()) or ()
            ),
        )
        active_knowledge_prompt_evidence = (
            _surface_active_knowledge_prompt_evidence(
                messages=rendered_messages,
                memory_packet=surface_input.memory_packet,
            )
        )
        result = await ports.model_attempt_service().complete(
            ModelAttemptRequest(
                request_id=request_id or "surface-main-chain",
                scene_type=model_scene_type,
                cognitive_tier=model_cognitive_tier,
                prompt=PromptRef(
                    prompt_id,
                    {"messages": rendered_messages},
                ),
                deadline_ms=model_deadline_ms,
                foreground=True,
                retry_evaluator=_retry_evaluator,
                previsibility_transport_retries=(
                    1 if model_route_lane == "realtime_companion" else 0
                ),
                metadata={
                    "chain": "surface_main",
                    "route_lane": model_route_lane,
                    **expression_prompt_evidence,
                    **active_knowledge_prompt_evidence,
                    **(
                        {"model_node_id": governed_model_budget.node_id}
                        if governed_model_budget is not None
                        else {}
                    ),
                },
                model_budget_guard=governed_model_budget,
            )
        )
        if isinstance(trace_stage_meta, dict):
            attempt_trace = model_attempt_result_trace(result)
            attempt_trace["generate_elapsed_ms"] = int(
                (time.perf_counter() - generate_started) * 1000
            )
            attempt_trace["message_count"] = len(messages)
            if affective_candidate_count is not None:
                attempt_trace["affective_candidate_count"] = affective_candidate_count
            trace_stage_meta["surface_model_attempt"] = attempt_trace
        if not result.success:
            raise RuntimeError(
                f"{result.error_code or 'SURFACE_MODEL_ATTEMPT_FAILED'}: "
                f"{result.error_message or 'model attempt failed'}"
            )
        content = str(result.content or "").strip()
        if not content:
            raise RuntimeError("SURFACE_LLM_EMPTY_RESPONSE")
        if affective_candidate_count is not None:
            return _select_buffered_affective_candidate(
                raw_output=content,
                compiled_request=compiled_request,
                expected_candidate_count=affective_candidate_count,
                trace_stage_meta=trace_stage_meta,
                allow_single_degradation=affective_candidate_degraded,
            )
        return content

    return await SurfaceMainChain(
        _generate,
        model_retries_owned_externally=True,
    ).run(surface_input)


async def surface_main_chain_sse_generator(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    started_ms = int(time.time() * 1000)
    trace = {
        **_safe_mapping(getattr(agent_input, "trace_stage_meta", None)),
        **dict(trace_stage_meta or {}),
    }
    delegated_agents = _delegated_agents_from_sub_agent_context(agent_input)
    _merge_delegated_agent_trace(trace, delegated_agents)
    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    canonical_affective_context = bool(
        _safe_mapping(getattr(agent_input, "emotion_context", None)).get(
            "_affective_life_internal"
        )
    )
    evidence_gate_text = _surface_perception_evidence_gate_text(agent_input)
    if evidence_gate_text:
        # A rule-based perception boundary is still Natsume speaking.  It
        # therefore always receives the canonical plan.  Direct Host delivery
        # stages it locally; a governed worker instead returns one deferred
        # foreground effect, which becomes the sole writer after Acceptance.
        if canonical_affective_context:
            try:
                surface_input = build_surface_main_chain_input_from_surface_reply_input(
                    agent_input,
                    ports=ports,
                    trace_stage_meta=trace,
                )
                _record_surface_memory_guard_packet(
                    agent_input=agent_input,
                    surface_input=surface_input,
                    trace_stage_meta=trace,
                )
                plan = affective_turn_plan_from_trace(trace)
                if not plan:
                    raise RuntimeError("AFFECTIVE_TURN_PLAN_REQUIRED_FOR_RULE_REPLY")
            except Exception as exc:
                yield _sse(
                    {
                        "phase": "COMMIT",
                        "done": True,
                        "decision": "NO_REPLY",
                        "reason_code": str(exc).split(":", 1)[0][:160]
                        or "AFFECTIVE_TURN_PLAN_REQUIRED_FOR_RULE_REPLY",
                        "narrative": "",
                        "request_id": request_id,
                        "audit_refs": {
                            "request_id": request_id,
                            "path": "surface_reply:canonical_affective_deterministic",
                            "durability": "fail_closed_before_stream",
                            "trace_stage_meta": {
                                **trace,
                                "surface_main_chain_used": True,
                                "reply_source": "surface_evidence_gate_prepare_reject",
                            },
                        },
                    }
                )
                return
            trace["surface_evidence_gate"] = {
                "status": "OK",
                "reason_code": "SURFACE_PERCEPTION_EVIDENCE_REQUIRED",
                "has_multimodal_payload": False,
            }
            if bool(getattr(agent_input, "defer_effects_to_task_runtime", False)):
                async for event in _canonical_affective_deferred_delivery(
                    agent_input,
                    ports=ports,
                    trace=trace,
                    plan=plan,
                    text=evidence_gate_text,
                    started_ms=started_ms,
                    reply_source="surface_evidence_gate",
                    reason_code="SURFACE_PERCEPTION_EVIDENCE_REQUIRED",
                    degraded=False,
                    audit={"evidence_gate": True},
                    raw_extra={"evidence_gate": True},
                ):
                    yield event
                return
            if _has_governed_model_budget(trace):
                yield _sse(
                    {
                        "phase": "COMMIT",
                        "done": True,
                        "decision": "NO_REPLY",
                        "reason_code": "GOVERNED_AFFECTIVE_DEFER_REQUIRED",
                        "narrative": "",
                        "request_id": request_id,
                        "audit_refs": {
                            "request_id": request_id,
                            "path": "surface_reply:governed_affective_deterministic_buffered",
                            "durability": "fail_closed_before_stream",
                            "trace_stage_meta": {
                                **trace,
                                "surface_main_chain_used": True,
                                "reply_source": "surface_evidence_gate_defer_reject",
                            },
                        },
                    }
                )
                return
            async for event in _canonical_affective_deterministic_delivery(
                agent_input,
                ports=ports,
                trace=trace,
                plan=plan,
                text=evidence_gate_text,
                started_ms=started_ms,
                reply_source="surface_evidence_gate",
                reason_code="SURFACE_PERCEPTION_EVIDENCE_REQUIRED",
                degraded=False,
                audit={"evidence_gate": True},
                raw_extra={"evidence_gate": True},
            ):
                yield event
            return
        total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
        envelope = ReplyEnvelope(
            kind="final_reply",
            text=evidence_gate_text,
            response_form="text",
            source="surface_evidence_gate",
            sendable=True,
            persistable=True,
            cacheable=False,
            degraded=False,
            reason_code="SURFACE_PERCEPTION_EVIDENCE_REQUIRED",
            audit={"request_id": request_id},
            raw_extra={"evidence_gate": True},
        )
        yield _sse({"phase": "RENDER", "chunk": evidence_gate_text})
        yield _sse({"phase": "SENTENCE", "sentence": evidence_gate_text, "sentence_index": 0})
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "reason_code": envelope.reason_code,
                "narrative": evidence_gate_text,
                "request_id": request_id,
                "reply_envelope": envelope.as_dict(),
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:surface_main_chain",
                    "latency_ms": total_latency_ms,
                    "first_token_ms": 0,
                    "llm_latency_ms": 0,
                    "model_id": "",
                    "provider_name": "rule",
                    "response_form": "text",
                    "timings_ms": _surface_timings_ms(
                        total_latency_ms=total_latency_ms,
                        llm_latency_ms=0,
                    ),
                    "reply_envelope": envelope.as_dict(),
                    "trace_stage_meta": {
                        **trace,
                        "surface_main_chain_used": True,
                        "reply_source": "surface_evidence_gate",
                        "surface_evidence_gate": {
                            "status": "OK",
                            "reason_code": "SURFACE_PERCEPTION_EVIDENCE_REQUIRED",
                            "has_multimodal_payload": False,
                        },
                    },
                },
            }
        )
        return

    # DL-04 only changes calls that already carry the authenticated Affect
    # Fusion hand-off.  Legacy inputs deliberately stay on the compatibility
    # path until DL-06 removes their remaining parallel emotion writers.
    if canonical_affective_context:
        try:
            prepared_stream = _native_affective_stream_preparation(
                agent_input,
                ports=ports,
                trace_stage_meta=trace,
            )
        except Exception as exc:
            yield _sse(
                {
                    "phase": "COMMIT",
                    "done": True,
                    "decision": "REJECT",
                    "reason_code": str(exc).split(":", 1)[0][:160] or "AFFECTIVE_TURN_PREPARE_FAILED",
                    "narrative": "",
                    "request_id": request_id,
                    "audit_refs": {
                        "request_id": request_id,
                        "path": "surface_reply:native_affective_stream",
                        "trace_stage_meta": {
                            **trace,
                            "surface_main_chain_used": True,
                            "reply_source": "affective_turn_prepare_reject",
                        },
                    },
                }
            )
            return
        if prepared_stream is not None:
            surface_input, compiled_request, plan = prepared_stream
            async for event in _native_affective_sentence_stream(
                agent_input,
                ports=ports,
                trace=trace,
                surface_input=surface_input,
                compiled_request=compiled_request,
                plan=plan,
                started_ms=started_ms,
            ):
                yield event
            return
    try:
        result = await run_surface_main_chain_for_surface_reply_input(
            agent_input,
            ports=ports,
            trace_stage_meta=trace,
        )
    except Exception as exc:
        total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
        if _has_governed_model_budget(trace):
            reason_code = _governed_model_budget_failure_reason(exc)
            yield _sse(
                {
                    "phase": "COMMIT",
                    "done": True,
                    "decision": "REJECT",
                    "reason_code": reason_code,
                    "narrative": "模型调用未满足当前节点约束，当前节点已停止。",
                    "request_id": request_id,
                    "audit_refs": {
                        "request_id": request_id,
                        "path": "surface_reply:surface_main_chain",
                        "latency_ms": total_latency_ms,
                        "reason_code": reason_code,
                        "timings_ms": _surface_timings_ms(
                            total_latency_ms=total_latency_ms,
                            llm_latency_ms=0,
                        ),
                        "trace_stage_meta": {
                            **trace,
                            "surface_main_chain_used": True,
                            "reply_source": "surface_main_chain_budget_reject",
                        },
                    },
                }
            )
            return
        fallback_text = _surface_failure_fallback_text(agent_input, exc=exc)
        if fallback_text:
            if canonical_affective_context:
                plan = affective_turn_plan_from_trace(trace)
                if not plan:
                    yield _sse(
                        {
                            "phase": "COMMIT",
                            "done": True,
                            "decision": "NO_REPLY",
                            "reason_code": "AFFECTIVE_TURN_PLAN_REQUIRED_FOR_FALLBACK",
                            "narrative": "",
                            "request_id": request_id,
                            "audit_refs": {
                                "request_id": request_id,
                                "path": "surface_reply:canonical_affective_deterministic",
                                "durability": "fail_closed_before_stream",
                                "trace_stage_meta": {
                                    **trace,
                                    "surface_main_chain_used": True,
                                    "reply_source": "surface_main_chain_fallback_reject",
                                },
                            },
                        }
                    )
                    return
                async for event in _canonical_affective_deterministic_delivery(
                    agent_input,
                    ports=ports,
                    trace=trace,
                    plan=plan,
                    text=fallback_text,
                    started_ms=started_ms,
                    reply_source="surface_main_chain_affective_fallback",
                    reason_code="SURFACE_MAIN_CHAIN_FALLBACK",
                    degraded=True,
                    audit={"request_id": request_id, "error": type(exc).__name__},
                    raw_extra={
                        "fallback": True,
                        "degraded": True,
                        "reason_code": "SURFACE_MAIN_CHAIN_FAILED",
                    },
                ):
                    yield event
                return
            envelope = ReplyEnvelope(
                kind="final_reply",
                text=fallback_text,
                response_form="text",
                source="surface_main_chain_fallback",
                sendable=True,
                persistable=False,
                cacheable=False,
                degraded=False,
                reason_code="SURFACE_MAIN_CHAIN_FALLBACK",
                audit={"request_id": request_id, "error": type(exc).__name__},
                raw_extra={
                    "fallback": True,
                    "degraded": True,
                    "reason_code": "SURFACE_MAIN_CHAIN_FAILED",
                },
            )
            yield _sse({"phase": "RENDER", "chunk": fallback_text})
            yield _sse({"phase": "SENTENCE", "sentence": fallback_text, "sentence_index": 0})
            yield _sse(
                {
                    "phase": "COMMIT",
                    "done": True,
                    "decision": "APPROVE",
                    "reason_code": envelope.reason_code,
                    "narrative": fallback_text,
                    "request_id": request_id,
                    "reply_envelope": envelope.as_dict(),
                    "audit_refs": {
                        "request_id": request_id,
                        "path": "surface_reply:surface_main_chain",
                        "latency_ms": total_latency_ms,
                        "reason_code": "SURFACE_MAIN_CHAIN_FAILED",
                        "timings_ms": _surface_timings_ms(
                            total_latency_ms=total_latency_ms,
                            llm_latency_ms=0,
                        ),
                        "trace_stage_meta": {
                            **trace,
                            "surface_main_chain_used": True,
                            "reply_source": "surface_main_chain_fallback",
                            "surface_renderer": {
                                "status": "DEGRADED",
                                "invoked": True,
                                "affected_output": True,
                                "source": "surface_main_chain_fallback",
                                "reason_code": "SURFACE_MAIN_CHAIN_FAILED",
                            },
                        },
                    },
                }
            )
            return
        envelope = ReplyEnvelope.no_reply(
            reason_code="SURFACE_MAIN_CHAIN_FAILED",
            source="surface_main_chain",
            audit={"request_id": request_id, "error": type(exc).__name__},
        )
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": envelope.reason_code,
                "narrative": "",
                "request_id": request_id,
                "reply_envelope": envelope.as_dict(),
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:surface_main_chain",
                    "latency_ms": total_latency_ms,
                    "reason_code": envelope.reason_code,
                    "timings_ms": _surface_timings_ms(
                        total_latency_ms=total_latency_ms,
                        llm_latency_ms=0,
                    ),
                    "trace_stage_meta": {**trace, "surface_main_chain_used": True},
                },
            }
        )
        return

    raw_narrative = str(result.selected_text or "").strip()
    narrative = raw_narrative
    narrative, account_guard_applied = _sanitize_account_login_reply(narrative, agent_input=agent_input)
    narrative = _sanitize_main_chain_meta_reply(narrative, agent_input=agent_input)
    narrative, quality_guard = _apply_surface_text_quality_guard(
        narrative,
        agent_input=agent_input,
    )
    # A reply node owns exactly the primary Surface model attempt frozen into
    # its TaskGraph budget.  The former optional Spark "judge" made a second,
    # unbound model call after the candidate had already been produced.  It is
    # retired instead of being skipped only for governed inputs: otherwise a
    # future non-governed caller could silently recreate a second model
    # authority outside Attempt/Budget/Acceptance.  Retire the route globally
    # rather than merely skipping it for governed inputs.
    narrative, sub_agent_result_guard = _apply_surface_sub_agent_result_guard(
        narrative,
        agent_input=agent_input,
    )
    if account_guard_applied:
        trace["surface_account_login_guard"] = {
            "applied": True,
            "reason_code": "ACCOUNT_CREDENTIAL_REQUEST_REWRITTEN",
        }
    if quality_guard.get("applied"):
        trace["surface_text_quality_guard"] = quality_guard
    if sub_agent_result_guard.get("applied"):
        trace["surface_sub_agent_result_guard"] = sub_agent_result_guard
    total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
    llm_latency_ms = int(result.runtime.latency_ms or 0)
    timings_ms = _surface_timings_ms(
        total_latency_ms=total_latency_ms,
        llm_latency_ms=llm_latency_ms,
    )
    model_audit = _surface_model_audit_fields(agent_input=agent_input, trace_stage_meta=trace)
    envelope = ReplyEnvelope.final_reply(
        text=narrative,
        response_form="text",
        source="surface_main_chain",
        audit={
            "request_id": request_id,
            "observation_codes": list(result.observation_codes),
            "natsume_emotion_tag": result.natsume_emotion_tag,
            "user_emotion_tag": result.user_emotion_tag,
            "vts_expression_id": result.vts_expression_id,
            "emotion_confidence": float(result.runtime.emotion_confidence or 0.0),
        },
    )
    acceptance_effect_intents: list[dict[str, Any]] = []
    if envelope.persistable:
        try:
            foreground_receipt = await asyncio.to_thread(
                _submit_surface_foreground_completed,
                life_command_service=ports.get_life_command_service(),
                life_snapshot_reader=ports.get_life_snapshot_reader(),
                relationship_lifecycle_service=(
                    ports.get_relationship_lifecycle_service()
                ),
                agent_input=agent_input,
                result=result,
                envelope=envelope,
                narrative=narrative,
                trace_stage_meta=trace,
                timings_ms=timings_ms,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=llm_latency_ms,
            )
        except Exception as exc:
            trace["life_causality"] = {
                "status": "FAILED",
                "reason_code": "LIFE_FOREGROUND_COMMIT_FAILED",
                "error_type": type(exc).__name__,
            }
            failure_envelope = ReplyEnvelope.no_reply(
                reason_code="LIFE_FOREGROUND_COMMIT_FAILED",
                source="surface_main_chain",
                audit={
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                    "durability": "fail_closed_before_render",
                },
            )
            yield _sse(
                {
                    "phase": "COMMIT",
                    "done": True,
                    "decision": "NO_REPLY",
                    "reason_code": failure_envelope.reason_code,
                    "narrative": "",
                    "request_id": request_id,
                    "reply_envelope": failure_envelope.as_dict(),
                    "audit_refs": {
                        "request_id": request_id,
                        "path": "surface_reply:surface_main_chain",
                        "durability": "fail_closed_before_render",
                        "trace_stage_meta": {
                            **trace,
                            "surface_main_chain_used": True,
                            "reply_source": "surface_main_chain",
                        },
                    },
                }
            )
            return
        trace["life_causality"] = {
            "status": str(foreground_receipt.get("status") or "UNKNOWN"),
            "reason_code": foreground_receipt.get("reason_code"),
            "command_id": foreground_receipt.get("command_id"),
            "event_id": foreground_receipt.get("event_id"),
            "state_version": foreground_receipt.get("state_version"),
            "outbox_count": foreground_receipt.get("outbox_count"),
            "replayed": bool(foreground_receipt.get("replayed")),
        }
        raw_effect_intents = foreground_receipt.get("effect_intents")
        if isinstance(raw_effect_intents, list):
            acceptance_effect_intents = [
                dict(item)
                for item in raw_effect_intents
                if isinstance(item, Mapping)
            ]
    else:
        trace["life_causality"] = {
            "status": "SKIPPED",
            "reason_code": "REPLY_NOT_PERSISTABLE",
        }
    if narrative:
        delivery_event = _expression_delivery_sse(foreground_receipt)
        if delivery_event is not None:
            yield _sse(delivery_event)
        yield _sse({"phase": "RENDER", "chunk": narrative})
        yield _sse({"phase": "SENTENCE", "sentence": narrative, "sentence_index": 0})
    yield _sse(
        {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE" if envelope.sendable else "NO_REPLY",
            "reason_code": envelope.reason_code or None,
            "narrative": narrative if envelope.sendable else "",
            "request_id": request_id,
            "reply_envelope": envelope.as_dict(),
            "acceptance_effect_intents": acceptance_effect_intents,
            "audit_refs": {
                "request_id": request_id,
                "path": "surface_reply:surface_main_chain",
                "latency_ms": total_latency_ms,
                "first_token_ms": llm_latency_ms if narrative else None,
                "llm_latency_ms": llm_latency_ms,
                "model_id": model_audit["model_id"],
                "provider_name": model_audit["provider_name"],
                "response_form": "text",
                "tts_text_ja": str(getattr(result, "tts_text_ja", "") or ""),
                "timings_ms": timings_ms,
                "reply_envelope": envelope.as_dict(),
                "surface_main_chain": _public_result_trace(result),
                "trace_stage_meta": {
                    **trace,
                    "surface_main_chain_used": True,
                    "reply_source": "surface_main_chain",
                    "surface_renderer": {
                        "status": "OK",
                        "invoked": True,
                        "affected_output": bool(narrative),
                        "source": "surface_main_chain",
                        "reason_code": "",
                    },
                },
            },
        }
    )
