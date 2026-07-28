"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any
from natsume_contracts.life import LifeDeliveryAuthorization, digest_json
from collections.abc import AsyncGenerator, Mapping, Sequence
from host_brain.persona.surface_main_chain import (
    SurfaceMainChain,
    SurfaceMainChainInput,
    SurfaceMainChainResult,
    compile_surface_main_chain_input,
)
import json
from host_brain.digital_life.application_boundary import (
    project_life_director_pack,
    read_life_snapshot,
    submit_life_command,
)
from host_brain.digital_life.affective_expression_delivery import (
    build_expression_delivery_controls,
    build_sentence_expression_delivery,
    expression_delivery_trace,
)
import time

from host_brain.surface_runtime.common import (
    _coerce_int,
    _safe_mapping,
)
from host_brain.surface_runtime.reply_context import (
    _selected_reply_plan,
    _surface_emotion_context,
)
from host_brain.surface_runtime.subagent_context import (
    _surface_sub_agent_context,
    _surface_sub_agent_id,
)
def _public_result_trace(
    result: SurfaceMainChainResult | None,
    *,
    stream_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a public trace without making model emotion fields authoritative."""

    if result is None:
        summary = _safe_mapping(stream_summary)
        return {
            "streaming": True,
            "mode": str(summary.get("mode") or "native_sentence_stream"),
            "sentence_count": max(0, _coerce_int(summary.get("sentence_count"), default=0)),
            "plan_id": str(summary.get("plan_id") or "").strip(),
            "synchronization_id": str(summary.get("synchronization_id") or "").strip(),
            "self_affect_revision": max(
                0,
                _coerce_int(summary.get("self_affect_revision"), default=0),
            ),
            "first_token_ms": (
                max(0, _coerce_int(summary.get("first_token_ms"), default=0))
                if summary.get("first_token_ms") is not None
                else None
            ),
            "provider_usage_status": str(
                summary.get("provider_usage_status")
                or "STREAM_ATTEMPT_AUDITED_NO_FINAL_USAGE"
            ),
        }
    return {
        "selected_text": result.selected_text,
        "natsume_emotion_tag": result.natsume_emotion_tag,
        "user_emotion_tag": result.user_emotion_tag,
        "vts_expression_id": result.vts_expression_id,
        "emotion_confidence": float(result.runtime.emotion_confidence or 0.0),
        "observation_codes": list(result.observation_codes),
        "packet_trace": dict(result.compiled.trace),
        "generation_trace": dict(result.runtime.generation_trace),
        "latency_ms": int(result.runtime.latency_ms or 0),
    }


def _surface_timings_ms(*, total_latency_ms: int, llm_latency_ms: int) -> dict[str, int]:
    return {
        "prompt_build_ms": 0,
        "llm_runtime_resolve_ms": 0,
        "stream_after_first_token_ms": 0,
        "response_intelligence_ms": 0,
        "sanitize_egress_ms": 0,
        "precommit_log_ms": 0,
        "surface_main_chain_ms": max(0, int(total_latency_ms or 0)),
        "llm_latency_ms": max(0, int(llm_latency_ms or 0)),
    }


def _surface_model_audit_fields(*, agent_input: Any, trace_stage_meta: Mapping[str, Any]) -> dict[str, Any]:
    del agent_input
    attempt_trace = _safe_mapping(trace_stage_meta.get("surface_model_attempt"))
    return {
        "model_id": str(attempt_trace.get("final_model") or "").strip(),
        "provider_name": str(attempt_trace.get("final_provider") or "").strip(),
    }


def _delegated_agents_from_sub_agent_context(agent_input: Any) -> list[dict[str, Any]]:
    ctx = _surface_sub_agent_context(agent_input)
    agent_id = _surface_sub_agent_id(agent_input)
    if ctx is None or not agent_id:
        return []
    payload = _safe_mapping(getattr(ctx, "payload", None))
    audit_refs = _safe_mapping(payload.get("audit_refs"))
    return [
        {
            "agent_id": agent_id,
            "task": str(getattr(ctx, "task", "") or "").strip()[:500],
            "success": bool(getattr(ctx, "success", False)),
            "latency_ms": max(0, _coerce_int(getattr(ctx, "latency_ms", 0), default=0)),
            "error": str(getattr(ctx, "error", "") or "").strip()[:500],
            "result_text": str(getattr(ctx, "result_text", "") or "").strip()[:4000],
            "audit_refs": audit_refs,
        }
    ]


def _merge_delegated_agent_trace(trace: dict[str, Any], delegated_agents: Sequence[Mapping[str, Any]]) -> None:
    if not delegated_agents:
        return
    first = dict(delegated_agents[0])
    agent_id = str(first.get("agent_id") or "").strip()
    if not agent_id:
        return
    trace.setdefault("dispatch_route", "delegate")
    trace.setdefault("dispatch_target", agent_id)
    trace.setdefault("dispatch_reason", str(trace.get("skill_route_reason") or trace.get("route_reason") or "").strip())
    trace.setdefault("chat_mode", "sub_agent")
    trace.setdefault("chat_sub_agent_id", agent_id)
    trace["delegated_agent_count"] = len(list(delegated_agents))


def _submit_surface_foreground_completed(
    *,
    life_command_service: Any,
    life_snapshot_reader: Any,
    relationship_lifecycle_service: Any | None = None,
    agent_input: Any,
    result: SurfaceMainChainResult | None,
    envelope: Any,
    narrative: str,
    trace_stage_meta: Mapping[str, Any],
    timings_ms: Mapping[str, int],
    total_latency_ms: int,
    llm_latency_ms: int,
    stream_summary: Mapping[str, Any] | None = None,
    first_token_ms: int | None = None,
    buffer_stream: bool = True,
) -> dict[str, Any]:
    """Commit one governed command for every durable Surface completion.

    The former adapter launched two independent background writers.  Projection
    and delivery work is now represented as intents committed atomically with the
    foreground event; downstream workers own their execution and retry policy.
    """

    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    person_id = str(getattr(agent_input, "person_id", "") or "").strip() or "default"
    workspace_id = str(getattr(agent_input, "workspace_id", "") or "").strip() or "workspace::default"
    session_id = str(getattr(agent_input, "session_id", "") or "").strip()
    user_message = str(getattr(agent_input, "user_message", "") or "")
    reply_channel = str(
        getattr(agent_input, "reply_channel", "")
        or getattr(agent_input, "platform", "")
        or ""
    ).strip()
    reply_target = str(
        getattr(agent_input, "reply_target", "")
        or getattr(agent_input, "conversation_id", "")
        or ""
    ).strip()
    request_ts_ms = _coerce_int(getattr(agent_input, "ts_ms", 0), default=0)
    completed_at_ms = int(time.time() * 1000)
    surface_result_trace = _public_result_trace(
        result,
        stream_summary=stream_summary,
    )
    affective_turn_plan = _affective_turn_plan(trace_stage_meta)
    affective_turn_plan_digest = (
        digest_json(affective_turn_plan) if affective_turn_plan else ""
    )
    emotion_context = _surface_emotion_context(
        agent_input,
        result=result,
        trace_stage_meta=trace_stage_meta,
    )
    bond_summary = _safe_mapping(getattr(agent_input, "bond_summary", None))
    reply_plan = _selected_reply_plan(agent_input, trace_stage_meta)
    life_director = _safe_mapping(dict(trace_stage_meta or {}).get("life_director"))
    model_audit = _surface_model_audit_fields(
        agent_input=agent_input,
        trace_stage_meta=trace_stage_meta,
    )
    governor_result = _safe_mapping(
        getattr(agent_input, "governor_result", None)
    )
    delivery_authorization = _surface_delivery_authorization(
        agent_input,
        trace_stage_meta=trace_stage_meta,
        request_id=request_id,
        governor_result=governor_result,
    )
    projection_base = {
        "request_id": request_id,
        "session_id": session_id,
        "person_id": person_id,
        "workspace_id": workspace_id,
        "user_message": user_message,
        "assistant_reply": str(narrative or ""),
        "completed_at_ms": completed_at_ms,
    }
    delegated_agents = _delegated_agents_from_sub_agent_context(agent_input)
    # The frozen model budget is an execution authority.  The Chat executor
    # verifies it against the durable node budget and separately persists only
    # the measured usage.  Copying the guard into a Life projection would both
    # leak max_*_tokens authority fields and make the projection secret gate
    # fail after the provider call has already completed.
    post_process_trace = {
        **{
            str(key): value
            for key, value in dict(trace_stage_meta or {}).items()
            if str(key) != "governed_model_budget"
        },
        "surface_main_chain_used": True,
        "surface_reply_used": True,
        "reply_source": "surface_main_chain",
        "reply_envelope": envelope.as_dict(),
        "surface_main_chain": surface_result_trace,
        "timings_ms": dict(timings_ms),
        "skip_heavy_persona_postprocess": True,
    }
    _merge_delegated_agent_trace(post_process_trace, delegated_agents)
    post_process_payload = {
        **projection_base,
        # Keep the former _async_post_process input contract together in one
        # projection row.  The projection worker can therefore recover the
        # conversation, emotion/bond and audit effects atomically.
        "narrative": str(narrative or ""),
        "thread_id": str(getattr(agent_input, "thread_id", "") or ""),
        "request_ts_ms": request_ts_ms,
        "ts_ms": request_ts_ms or completed_at_ms,
        "render_target_lang": str(
            getattr(agent_input, "render_target_lang", "zh") or "zh"
        ),
        "emotion_context": emotion_context,
        "bond_summary": bond_summary,
        "reply_plan": reply_plan,
        "reply_envelope": envelope.as_dict(),
        "reply_channel": reply_channel or None,
        "reply_target": reply_target or None,
        "model_audit": model_audit,
        "provider_name": str(model_audit.get("provider_name") or ""),
        "model_id": str(model_audit.get("model_id") or ""),
        "timings_ms": dict(timings_ms),
        "total_latency_ms": max(0, int(total_latency_ms or 0)),
        "llm_latency_ms": max(0, int(llm_latency_ms or 0)),
        "first_token_ms": (
            max(
                0,
                int(
                    first_token_ms
                    if first_token_ms is not None
                    else llm_latency_ms
                )
                or 0,
            )
            if narrative
            else None
        ),
        "buffer_stream": bool(buffer_stream),
        "delegated_agents": delegated_agents,
        "retrieval_summary": _safe_mapping(
            getattr(agent_input, "retrieval_summary", None)
        ),
        "retrieval_evidence_items": [
            dict(item)
            for item in list(
                getattr(agent_input, "retrieval_evidence_items", None) or []
            )
            if isinstance(item, Mapping)
        ],
        "gov_decision": governor_result,
        "pii_ingress_stats": _safe_mapping(
            getattr(agent_input, "pii_ingress_stats", None)
            or dict(trace_stage_meta or {}).get("pii_ingress_stats")
        ),
        "pii_egress_stats": _safe_mapping(
            getattr(agent_input, "pii_egress_stats", None)
        ),
        "llm_input_digest": json.dumps(
            surface_result_trace, ensure_ascii=False
        )[:500],
        "motive_state": _safe_mapping(
            dict(trace_stage_meta or {}).get("motive_state")
            or getattr(agent_input, "motive_state", None)
        ),
        "trace_stage_meta": post_process_trace,
        "attachments": [
            dict(item)
            for item in list(getattr(agent_input, "attachments", None) or [])
            if isinstance(item, Mapping)
        ],
        "multimodal_observations": _safe_mapping(
            getattr(agent_input, "multimodal_observations", None)
        ),
        "attachment_summary": str(
            getattr(agent_input, "attachment_summary", "") or ""
        ).strip(),
        "life_director": life_director,
        "affective_turn_plan_ref": str(affective_turn_plan.get("plan_id") or ""),
        "affective_turn_plan_digest": affective_turn_plan_digest,
    }
    projection_intents = [
        {
            "target": "surface.post_process",
            "payload": post_process_payload,
        }
    ]
    expression_intents: list[dict[str, Any]] = []
    expression_controls: list[dict[str, Any]] = []
    expression_receipts: list[dict[str, Any]] = []
    expression_traces: list[dict[str, Any]] = []
    # The native stream already stages every gated sentence before it becomes
    # visible.  Buffered/governed Chat reaches this function only after Task
    # Acceptance, so stage its one accepted utterance here rather than using
    # the retired direct TTS/VTS side path.
    native_sentence_stream = str(_safe_mapping(stream_summary).get("mode") or "") == "native_sentence_stream"
    buffered_expression_segments: list[str] = []
    if affective_turn_plan and narrative and not native_sentence_stream:
        buffered_expression_segments = _bounded_expression_segments(str(narrative or ""))
        for sentence_index, segment in enumerate(buffered_expression_segments):
            intents, receipts, trace = _expression_sentence_protocol(
                plan=affective_turn_plan,
                sentence=segment,
                sentence_index=sentence_index,
                created_at_ms=completed_at_ms,
            )
            expression_intents.extend(intents)
            expression_receipts.extend(receipts)
            expression_traces.append(trace)
            projection_intents.append(
                _expression_protocol_projection_intent(
                    plan=affective_turn_plan,
                    trace=trace,
                    delivery_kind="INTENT",
                )
            )
    # A complete foreground response releases the paired protocol after its
    # accepted output is recorded.  This remains a Host-staged receipt until
    # a future Edge worker returns a physical channel receipt.
    if affective_turn_plan and narrative:
        sent_sentence_count = (
            len(buffered_expression_segments)
            if buffered_expression_segments
            else max(
                1,
                _coerce_int(
                    _safe_mapping(stream_summary).get("sentence_count"),
                    default=1,
                ),
            )
        )
        controls, receipts, trace = _expression_control_protocol(
            plan=affective_turn_plan,
            action="RELEASE",
            sent_sentence_count=sent_sentence_count,
            created_at_ms=completed_at_ms,
        )
        expression_controls.extend(controls)
        expression_receipts.extend(receipts)
        expression_traces.append(trace)
        projection_intents.append(
            _expression_protocol_projection_intent(
                plan=affective_turn_plan,
                trace=trace,
                delivery_kind="RELEASE",
            )
        )
    deliveries: list[dict[str, Any]] = []
    embodiment_target = _safe_mapping(life_director.get("embodiment_target"))
    # A canonical affective plan owns the expression protocol.  Do not also
    # enqueue the retired life-director VTS/MateEngine delivery here: that
    # would create a second channel authority and could duplicate a future
    # Edge application.  Compatibility-only calls without a frozen plan keep
    # their existing delivery behavior until DL-06 retires the old path.
    if embodiment_target and delivery_authorization is not None and not affective_turn_plan:
        delivery_request_base = request_id or session_id or person_id
        deliveries.extend(
            [
                {
                    "destination": "vts.emotion",
                    "authorization": delivery_authorization.as_dict(),
                    "payload": {
                        "request_id": f"{delivery_request_base}:vts",
                        "person_id": person_id,
                        "emotion_tag": str(embodiment_target.get("emotion_tag") or "NEUTRAL"),
                        "expression": str(embodiment_target.get("expression") or ""),
                        "source_kind": "foreground_completed",
                    },
                },
                {
                    "destination": "mateengine.state",
                    "authorization": delivery_authorization.as_dict(),
                    "payload": {
                        "request_id": f"{delivery_request_base}:mateengine",
                        "person_id": person_id,
                        "state": str(embodiment_target.get("mateengine_state") or "idle"),
                        "emotion_tag": str(embodiment_target.get("emotion_tag") or "NEUTRAL"),
                        "expression": str(embodiment_target.get("expression") or ""),
                        "source_kind": "foreground_completed",
                    },
                },
            ]
        )
    state_patch: dict[str, Any] = {
        "clock": {
            "last_user_seen_ms": completed_at_ms,
            "idle_minutes": 0,
        },
        "continuity": {
            "last_interaction_digest": (user_message or str(narrative or ""))[:160],
        },
    }
    if reply_channel and reply_target:
        state_patch["proactive"] = {
            "last_target": {
                "platform": reply_channel,
                "conversation_id": reply_target,
            }
        }
    payload = {
        **projection_base,
        "state_patch": state_patch,
        "emotion_context": emotion_context,
        "bond_summary": bond_summary,
        "reply_plan": reply_plan,
        "life_director": life_director,
        "model_audit": model_audit,
        "surface_result": surface_result_trace,
        "affective_turn_plan": affective_turn_plan,
        "affective_turn_plan_digest": affective_turn_plan_digest,
        "expression_delivery_intents": expression_intents,
        "expression_delivery_controls": expression_controls,
        "expression_delivery_receipts": expression_receipts,
        "legacy_direct_embodiment_delivery_retired": bool(affective_turn_plan),
        "timings_ms": dict(timings_ms),
        "total_latency_ms": max(0, int(total_latency_ms or 0)),
        "llm_latency_ms": max(0, int(llm_latency_ms or 0)),
        "projection_intents": projection_intents,
        "deliveries": deliveries,
    }
    command_payload = {
        "command_type": "foreground_completed",
        "person_id": person_id,
        "command_id": (
            f"life::foreground_completed::{request_id or session_id}"
            if request_id or session_id
            else ""
        ),
        "correlation_id": request_id or None,
        "payload": payload,
        "created_at_ms": completed_at_ms,
        "metadata": {
            "source": "surface_main_chain",
            "reply_sendable": bool(envelope.sendable),
            "reply_persistable": bool(envelope.persistable),
            "delivery_authorized": delivery_authorization is not None,
            "legacy_direct_embodiment_delivery_retired": bool(affective_turn_plan),
        },
    }
    if bool(getattr(agent_input, "defer_effects_to_task_runtime", False)):
        # A frozen governed worker is speculative until Verification and
        # Acceptance commit.  Return the complete immutable command payload to
        # the Task Runtime; do not touch Life state or any delivery transport.
        return {
            "status": "DEFERRED_TO_TASK_ACCEPTANCE",
            "reason_code": "TASK_ACCEPTANCE_REQUIRED",
            "effect_intents": [
                {
                    "effect_type": "life.foreground_completed",
                    "commit_scope": "NODE",
                    "payload": command_payload,
                }
            ],
        }
    foreground_receipt = submit_life_command(
        service=life_command_service,
        reader=life_snapshot_reader,
        **command_payload,
    )
    if expression_traces:
        expression_status = (
            "HOST_PROTOCOL_STAGED"
            if expression_intents
            else "HOST_RELEASE_STAGED"
            if expression_controls
            else "HOST_PROTOCOL_STAGED"
        )
        foreground_receipt = {
            **dict(foreground_receipt),
            "expression_delivery": {
                "status": expression_status,
                "traces": expression_traces,
            },
        }
    if relationship_lifecycle_service is None:
        return foreground_receipt
    provision = getattr(
        relationship_lifecycle_service,
        "provision_accepted_response",
        None,
    )
    if not callable(provision):
        raise RuntimeError("RELATIONSHIP_LIFECYCLE_SERVICE_NOT_INJECTED")
    episode_receipt = provision(
        workspace_id=workspace_id,
        relationship_subject_id=person_id,
        session_id=session_id or "session::unknown",
        request_id=request_id or str(foreground_receipt.get("command_id") or ""),
        response_receipt=foreground_receipt,
        response_text=str(narrative or ""),
    )
    return {
        **dict(foreground_receipt),
        "relationship_episode": {
            "command_id": episode_receipt.command_id,
            "event_id": episode_receipt.event_id,
            "state_version": episode_receipt.state_version,
            "receipt_ref": f"life-event://{episode_receipt.event_id}",
            "replayed": bool(episode_receipt.replayed),
        },
    }


def _affective_turn_plan(trace_stage_meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the frozen DL-04 plan only when it has the canonical shape."""

    plan = _safe_mapping(_safe_mapping(trace_stage_meta).get("affective_turn_plan"))
    expression = _safe_mapping(plan.get("expression_plan"))
    companion = _safe_mapping(plan.get("companion_act"))
    if (
        plan.get("schema_version") != "affective_turn_plan.v1"
        or not str(plan.get("plan_id") or "").strip()
        or not str(plan.get("self_affect_digest") or "").strip()
        or not str(expression.get("synchronization_id") or "").strip()
        or not str(companion.get("kind") or "").strip()
    ):
        return {}
    return plan


def _surface_stream_scope(agent_input: Any, plan: Mapping[str, Any]) -> tuple[str, str, str, str]:
    """Validate that streaming persistence cannot cross an affective scope."""

    person_id = str(getattr(agent_input, "person_id", "") or "").strip() or "default"
    workspace_id = str(getattr(agent_input, "workspace_id", "") or "").strip() or "workspace::default"
    session_id = str(getattr(agent_input, "session_id", "") or "").strip()
    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    if (
        str(plan.get("workspace_id") or "").strip() != workspace_id
        or str(plan.get("relationship_subject_id") or "").strip() != person_id
        or str(plan.get("session_id") or "").strip() != session_id
        or (request_id and str(plan.get("request_id") or "").strip() != request_id)
    ):
        raise RuntimeError("AFFECTIVE_TURN_STREAM_SCOPE_MISMATCH")
    return person_id, workspace_id, session_id, request_id


def _expression_protocol_projection_intent(
    *,
    plan: Mapping[str, Any],
    trace: Mapping[str, Any],
    delivery_kind: str,
) -> dict[str, Any]:
    """Build a projection acknowledgement without exposing channel content.

    The canonical command retains a text sentence only where DL-04 already
    needs it for exact visible-delivery recovery.  The downstream projection
    gets delivery identity and channel state, never the text or raw media.
    """

    return {
        "target": "surface.expression_delivery_protocol",
        "payload": {
            "plan_id": str(plan.get("plan_id") or ""),
            "synchronization_id": str(trace.get("synchronization_id") or ""),
            "self_affect_revision": max(
                0,
                _coerce_int(trace.get("self_affect_revision"), default=0),
            ),
            "delivery_kind": str(delivery_kind or "").upper(),
            "delivery_trace": dict(trace),
        },
    }


def _expression_sentence_protocol(
    *,
    plan: Mapping[str, Any],
    sentence: str,
    sentence_index: int,
    created_at_ms: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Compile one immutable sentence to paired Host-staged channels."""

    try:
        intents, receipts = build_sentence_expression_delivery(
            affective_turn_plan=plan,
            sentence=sentence,
            sentence_index=sentence_index,
            created_at_ms=created_at_ms,
        )
    except ValueError as exc:
        raise RuntimeError(f"AFFECTIVE_EXPRESSION_PROTOCOL_INVALID:{exc}") from exc
    trace = expression_delivery_trace(intents, receipts)
    return (
        [intent.as_dict() for intent in intents],
        [receipt.as_dict() for receipt in receipts],
        trace,
    )


def _bounded_expression_segments(narrative: str, *, maximum: int = 2_000) -> list[str]:
    """Split a buffered accepted reply without silently dropping its tail.

    Native streaming already yields stable sentences.  A governed/buffered
    reply becomes visible only after Acceptance, so it may be compiled here.
    The Voice protocol retains its per-intent bound; a long reply is split at
    a nearby natural boundary, falling back to a bounded Unicode slice.
    """

    remaining = str(narrative or "").strip()
    if not remaining:
        return []
    maximum = max(64, int(maximum))
    segments: list[str] = []
    separators = "。！？!?；;，,、\n"
    while remaining:
        if len(remaining) <= maximum:
            segments.append(remaining)
            break
        window = remaining[:maximum]
        boundary = max(window.rfind(marker) for marker in separators)
        cut = boundary + 1 if boundary >= maximum // 2 else maximum
        segment = remaining[:cut].strip()
        if not segment:  # defensive only; a non-empty source always advances
            segment = remaining[:maximum]
            cut = len(segment)
        segments.append(segment)
        remaining = remaining[cut:].lstrip()
    return segments


def _expression_control_protocol(
    *,
    plan: Mapping[str, Any],
    action: str,
    sent_sentence_count: int,
    created_at_ms: int,
    reason_code: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Compile cancel/release controls without resurrecting an Edge client."""

    try:
        intents, receipts = build_expression_delivery_controls(
            affective_turn_plan=plan,
            action=action,
            sent_sentence_count=sent_sentence_count,
            created_at_ms=created_at_ms,
            reason_code=reason_code,
        )
    except ValueError as exc:
        raise RuntimeError(f"AFFECTIVE_EXPRESSION_CONTROL_INVALID:{exc}") from exc
    trace = expression_delivery_trace(intents, receipts)
    return (
        [intent.as_dict() for intent in intents],
        [receipt.as_dict() for receipt in receipts],
        trace,
    )


def _submit_surface_affective_turn_planned(
    *,
    life_command_service: Any,
    life_snapshot_reader: Any,
    agent_input: Any,
    trace_stage_meta: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist one immutable plan before the first visible stream sentence."""

    plan = _affective_turn_plan(trace_stage_meta)
    if not plan:
        raise RuntimeError("AFFECTIVE_TURN_PLAN_REQUIRED_FOR_STREAM")
    person_id, workspace_id, session_id, request_id = _surface_stream_scope(agent_input, plan)
    plan_id = str(plan["plan_id"])
    expression = _safe_mapping(plan.get("expression_plan"))
    payload = {
        "affective_turn_plan": plan,
        "affective_turn_plan_digest": digest_json(plan),
        "workspace_id": workspace_id,
        "session_id": session_id,
        "relationship_subject_id": person_id,
        "projection_intents": [
            {
                "target": "surface.affective_turn_plan",
                "payload": {
                    "plan_id": plan_id,
                    "synchronization_id": str(expression.get("synchronization_id") or ""),
                    "self_affect_revision": max(
                        0,
                        _coerce_int(plan.get("self_affect_revision"), default=0),
                    ),
                    "plan_digest": digest_json(plan),
                },
            }
        ],
    }
    return submit_life_command(
        service=life_command_service,
        reader=life_snapshot_reader,
        command_type="affective_turn_planned",
        person_id=person_id,
        command_id=f"life::affective_turn_planned::{plan_id}",
        correlation_id=request_id or None,
        payload=payload,
        created_at_ms=max(0, _coerce_int(plan.get("created_at_ms"), default=0)),
        metadata={
            "source": "surface_native_sentence_stream",
            "workspace_id": workspace_id,
            "session_id": session_id,
        },
    )


def _submit_surface_sentence_delivery_intent(
    *,
    life_command_service: Any,
    life_snapshot_reader: Any,
    agent_input: Any,
    trace_stage_meta: Mapping[str, Any],
    sentence: str,
    sentence_index: int,
) -> dict[str, Any]:
    """Durably stage one already-gated sentence before its SSE emission."""

    plan = _affective_turn_plan(trace_stage_meta)
    if not plan:
        raise RuntimeError("AFFECTIVE_TURN_PLAN_REQUIRED_FOR_SENTENCE")
    text = str(sentence or "").strip()
    if not text:
        raise ValueError("surface stream sentence is required")
    if len(text) > 2_000:
        raise ValueError("surface stream sentence exceeds 2000 characters")
    index = max(0, int(sentence_index))
    person_id, workspace_id, session_id, request_id = _surface_stream_scope(agent_input, plan)
    plan_id = str(plan["plan_id"])
    expression = _safe_mapping(plan.get("expression_plan"))
    sentence_digest = digest_json({"sentence": text})
    created_at_ms = int(time.time() * 1000)
    expression_intents, expression_receipts, expression_trace = _expression_sentence_protocol(
        plan=plan,
        sentence=text,
        sentence_index=index,
        created_at_ms=created_at_ms,
    )
    payload = {
        "affective_turn_plan_ref": plan_id,
        "synchronization_id": str(expression.get("synchronization_id") or ""),
        "workspace_id": workspace_id,
        "session_id": session_id,
        "relationship_subject_id": person_id,
        "sentence_index": index,
        # The sentence is user-visible delivery content, not an affect
        # observation or raw media artifact.  It is kept for exact replay and
        # idempotency; projection payloads carry the same immutable content.
        "sentence": text,
        "sentence_digest": sentence_digest,
        "affective_turn_plan_digest": digest_json(plan),
        "self_affect_revision": max(
            0,
            _coerce_int(plan.get("self_affect_revision"), default=0),
        ),
        "expression_delivery_intents": expression_intents,
        "expression_delivery_receipts": expression_receipts,
        "projection_intents": [
            {
                "target": "surface.sentence_delivery_intent",
                "payload": {
                    "plan_id": plan_id,
                    "synchronization_id": str(expression.get("synchronization_id") or ""),
                    "sentence_index": index,
                    "sentence": text,
                    "sentence_digest": sentence_digest,
                    "request_id": request_id,
                },
            },
            _expression_protocol_projection_intent(
                plan=plan,
                trace=expression_trace,
                delivery_kind="INTENT",
            ),
        ],
    }
    receipt = submit_life_command(
        service=life_command_service,
        reader=life_snapshot_reader,
        command_type="surface_sentence_delivery_intent",
        person_id=person_id,
        command_id=f"life::surface_sentence::{plan_id}::{index}",
        correlation_id=request_id or None,
        payload=payload,
        created_at_ms=created_at_ms,
        metadata={
            "source": "surface_native_sentence_stream",
            "workspace_id": workspace_id,
            "session_id": session_id,
            "sentence_digest": sentence_digest,
        },
    )
    return {
        **dict(receipt),
        "expression_delivery": {
            "status": "HOST_PROTOCOL_STAGED",
            "trace": expression_trace,
        },
    }


def _submit_surface_stream_cancelled(
    *,
    life_command_service: Any,
    life_snapshot_reader: Any,
    agent_input: Any,
    trace_stage_meta: Mapping[str, Any],
    sent_sentence_count: int,
    reason_code: str,
) -> dict[str, Any]:
    """Record an interrupted live stream without claiming unsent content."""

    plan = _affective_turn_plan(trace_stage_meta)
    if not plan:
        return {}
    person_id, workspace_id, session_id, request_id = _surface_stream_scope(agent_input, plan)
    plan_id = str(plan["plan_id"])
    expression = _safe_mapping(plan.get("expression_plan"))
    created_at_ms = int(time.time() * 1000)
    expression_controls, expression_receipts, expression_trace = _expression_control_protocol(
        plan=plan,
        action="CANCEL",
        sent_sentence_count=max(0, int(sent_sentence_count)),
        created_at_ms=created_at_ms,
        reason_code=str(reason_code or "SURFACE_STREAM_CANCELLED")[:120],
    )
    receipt = submit_life_command(
        service=life_command_service,
        reader=life_snapshot_reader,
        command_type="surface_stream_cancelled",
        person_id=person_id,
        command_id=f"life::surface_stream_cancelled::{plan_id}",
        correlation_id=request_id or None,
        payload={
            "affective_turn_plan_ref": plan_id,
            "affective_turn_plan_digest": digest_json(plan),
            "synchronization_id": str(expression.get("synchronization_id") or ""),
            "self_affect_revision": max(
                0,
                _coerce_int(plan.get("self_affect_revision"), default=0),
            ),
            "workspace_id": workspace_id,
            "session_id": session_id,
            "relationship_subject_id": person_id,
            "sent_sentence_count": max(0, int(sent_sentence_count)),
            "reason_code": str(reason_code or "SURFACE_STREAM_CANCELLED")[:120],
            "expression_delivery_controls": expression_controls,
            "expression_delivery_receipts": expression_receipts,
            "projection_intents": [
                _expression_protocol_projection_intent(
                    plan=plan,
                    trace=expression_trace,
                    delivery_kind="CANCEL",
                )
            ],
        },
        created_at_ms=created_at_ms,
        metadata={
            "source": "surface_native_sentence_stream",
            "workspace_id": workspace_id,
            "session_id": session_id,
        },
    )
    return {
        **dict(receipt),
        "expression_delivery": {
            "status": "HOST_CANCEL_STAGED",
            "trace": expression_trace,
        },
    }


def _surface_delivery_authorization(
    agent_input: Any,
    *,
    trace_stage_meta: Mapping[str, Any],
    request_id: str,
    governor_result: Mapping[str, Any],
) -> LifeDeliveryAuthorization | None:
    if (
        not request_id
        or str(governor_result.get("decision") or "").strip().upper()
        != "APPROVE"
    ):
        return None
    raw = _safe_mapping(getattr(agent_input, "delivery_authorization", None))
    if not raw:
        raw = _safe_mapping(dict(trace_stage_meta or {}).get("delivery_authorization"))
    try:
        authorization = LifeDeliveryAuthorization.from_mapping(raw)
    except ValueError:
        return None
    if authorization.request_id != request_id:
        return None
    from host_brain.orchestration.governor_grant import governor_grant_ref  # noqa: PLC0415

    if authorization.governor_grant_ref != governor_grant_ref(
        request_id,
        governor_result,
    ):
        return None
    return authorization
