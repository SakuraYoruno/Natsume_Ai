"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any
from natsume_contracts.life import LifeDeliveryAuthorization
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
def _public_result_trace(result: SurfaceMainChainResult) -> dict[str, Any]:
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
    agent_input: Any,
    result: SurfaceMainChainResult,
    envelope: Any,
    narrative: str,
    trace_stage_meta: Mapping[str, Any],
    timings_ms: Mapping[str, int],
    total_latency_ms: int,
    llm_latency_ms: int,
) -> dict[str, Any]:
    """Commit one governed command for every durable Surface completion.

    The former adapter launched two independent background writers.  Projection
    and delivery work is now represented as intents committed atomically with the
    foreground event; downstream workers own their execution and retry policy.
    """

    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    person_id = str(getattr(agent_input, "person_id", "") or "").strip() or "default"
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
    emotion_context = _surface_emotion_context(agent_input, result=result)
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
        "surface_main_chain": _public_result_trace(result),
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
        "first_token_ms": max(0, int(llm_latency_ms or 0)) if narrative else None,
        "buffer_stream": True,
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
            _public_result_trace(result), ensure_ascii=False
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
    }
    projection_intents = [
        {
            "target": "surface.post_process",
            "payload": post_process_payload,
        }
    ]
    deliveries: list[dict[str, Any]] = []
    embodiment_target = _safe_mapping(life_director.get("embodiment_target"))
    if embodiment_target and delivery_authorization is not None:
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
        "surface_result": _public_result_trace(result),
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
    return submit_life_command(
        service=life_command_service,
        reader=life_snapshot_reader,
        **command_payload,
    )


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
