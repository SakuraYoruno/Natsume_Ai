"""Application handlers for committed Digital Life projection intents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from natsume_contracts.life import LifeProjectionMessage


async def handle_life_projection(
    message: LifeProjectionMessage,
    *,
    agent_runtime_ports: AgentRuntimeApplicationPorts | None = None,
) -> Mapping[str, Any]:
    """Apply one projection after its source command is durably committed."""

    target = str(message.target or "").strip().lower()
    payload = dict(message.payload)
    if target == "surface.post_process":
        return await _handle_surface_post_process(
            message,
            payload,
            agent_runtime_ports=agent_runtime_ports,
        )
    if target in {
        "surface.affective_turn_plan",
        "surface.sentence_delivery_intent",
    }:
        # The live SSE owner has already staged the immutable plan/sentence in
        # the canonical Life journal before making it visible.  This projection
        # acknowledgement deliberately does not emit a second message: replay
        # and connection-resume remain the delivery owner's responsibility.
        return {
            "status": "STAGED_BY_LIVE_SURFACE",
            "target": target,
            "intent_id": message.intent_id,
            "plan_id": str(payload.get("plan_id") or ""),
            "sentence_index": payload.get("sentence_index"),
        }
    if target in {
        "work.environment",
        "life.experience",
        "work_event",
        "experience",
        "life.tick",
        "periodic_tick",
    }:
        # These facts are already durable in the immutable Life journal. Their
        # query projections are derived from the command state/event stream;
        # acknowledging the intent prevents the retired Life/Tribe writers
        # from being resurrected as an asynchronous side channel.
        return {
            "status": "PROJECTED_FROM_LIFE_JOURNAL",
            "target": target,
            "intent_id": message.intent_id,
        }
    raise ValueError(f"unsupported Life projection target: {target}")


async def _handle_surface_post_process(
    message: LifeProjectionMessage,
    payload: dict[str, Any],
    *,
    agent_runtime_ports: AgentRuntimeApplicationPorts | None,
) -> Mapping[str, Any]:
    from host_brain.agent_runtime.postprocess import _async_post_process  # noqa: PLC0415

    if not isinstance(agent_runtime_ports, AgentRuntimeApplicationPorts):
        raise RuntimeError("HOST_AGENT_RUNTIME_PORTS_NOT_INJECTED")

    trace = _mapping(payload.get("trace_stage_meta"))
    trace.update(
        {
            "life_projection_mode": True,
            "life_projection_intent_id": message.intent_id,
            "life_projection_event_id": message.event_id,
            "skip_heavy_persona_postprocess": True,
            "skip_default_mode_on_conversation_end": True,
            "surface_main_chain_used": True,
        }
    )
    model_audit = _mapping(payload.get("model_audit"))
    await _async_post_process(
        application_ports=agent_runtime_ports,
        request_id=str(payload.get("request_id") or message.command_id),
        person_id=str(payload.get("person_id") or message.person_id),
        session_id=str(payload.get("session_id") or ""),
        user_message=str(payload.get("user_message") or ""),
        narrative=str(
            payload.get("narrative")
            or payload.get("assistant_reply")
            or ""
        ),
        emotion_context=_mapping(payload.get("emotion_context")),
        bond_summary=_mapping(payload.get("bond_summary")),
        ts_ms=_integer(payload.get("ts_ms") or payload.get("completed_at_ms")),
        render_target_lang=str(payload.get("render_target_lang") or "zh"),
        total_latency_ms=_integer(payload.get("total_latency_ms")),
        llm_latency_ms=_integer(payload.get("llm_latency_ms")),
        first_token_ms=(
            _integer(payload.get("first_token_ms"))
            if payload.get("first_token_ms") is not None
            else None
        ),
        provider_name=str(
            payload.get("provider_name") or model_audit.get("provider_name") or ""
        ),
        model_id=str(payload.get("model_id") or model_audit.get("model_id") or ""),
        buffer_stream=bool(payload.get("buffer_stream", True)),
        delegated_agents=_mapping_list(payload.get("delegated_agents")),
        retrieval_summary=_mapping(payload.get("retrieval_summary")),
        gov_decision=_mapping(payload.get("gov_decision")),
        pii_ingress_stats=_mapping(payload.get("pii_ingress_stats")),
        pii_egress_stats=_mapping(payload.get("pii_egress_stats")),
        llm_input_digest=str(payload.get("llm_input_digest") or "") or None,
        retrieval_evidence_items=_mapping_list(
            payload.get("retrieval_evidence_items")
        ),
        motive_state=_mapping(payload.get("motive_state")),
        reply_plan=_mapping(payload.get("reply_plan")),
        trace_stage_meta=trace,
        attachments=_mapping_list(payload.get("attachments")),
        multimodal_observations=_mapping(payload.get("multimodal_observations")),
        attachment_summary=str(payload.get("attachment_summary") or ""),
        reply_channel=str(payload.get("reply_channel") or "") or None,
        reply_target=str(payload.get("reply_target") or "") or None,
    )
    return {
        "status": "APPLIED",
        "target": "surface.post_process",
        "intent_id": message.intent_id,
        "request_id": str(payload.get("request_id") or ""),
    }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


__all__ = ["handle_life_projection"]
