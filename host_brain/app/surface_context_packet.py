from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.app.chat_runtime_input import build_chat_runtime_input
from host_brain.app.context.prompt_context_plane import assemble_prompt_context_plane
from host_brain.app.relational_chat_detector import detect_relational_chat_topic
from host_brain.persona.surface_affect_control import build_surface_affect_control_packet
from host_brain.persona.surface_memory_recall import MemoryRecallPacket, memory_recall_packet_from_mapping


SURFACE_CONTEXT_NODE_ID = "surface_main_chain_context"
_COMPACT_LANE_TOPICS = {
    "identity",
    "relationship_boundary",
    "motivation",
    "help_commitment",
    "rejection",
    "greeting",
    "presence_ping",
    "light_ack",
    "playful_ping",
    "assistant_reply_followup",
}
_COMPACT_LANE_THREADS = {
    "identity_loop",
    "boundary_probe",
    "help_reassurance",
    "phatic",
    "assistant_reply_followup",
}
_MEMORY_PACKET_KEYS = (
    "surface_memory_recall_packet",
    "memory_recall_packet",
    "memory_packet",
)


def build_surface_context_packet(
    *,
    user_message: str,
    render_target_lang: str = "zh",
    emotion_context: Mapping[str, Any] | None = None,
    emotion_snapshot: Mapping[str, Any] | None = None,
    retrieval_summary: Mapping[str, Any] | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    person_id: str = "",
    session_id: str = "",
    request_id: str = "",
    thread_id: str | None = None,
    bond_summary: Mapping[str, Any] | None = None,
    intent_draft: Mapping[str, Any] | None = None,
    eval_history_turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build compressed app context for the surface main chain.

    This deliberately stops before rendering prompt messages. The final surface
    layer receives only the compiled ReplyControlPacket, not raw history.
    """

    runtime_payload = _build_surface_runtime_payload(
        user_message=user_message,
        render_target_lang=render_target_lang,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        person_id=person_id,
        session_id=session_id,
        request_id=request_id,
        thread_id=thread_id,
        bond_summary=bond_summary,
        intent_draft=intent_draft,
        eval_history_turns=eval_history_turns,
    )
    relational_topic = detect_relational_chat_topic(user_message)
    initial_compact_lane = bool(str(relational_topic or "").strip())
    context_plane = assemble_prompt_context_plane(
        node_id=SURFACE_CONTEXT_NODE_ID,
        runtime_payload=runtime_payload,
        history_max_turns=(6 if initial_compact_lane else None),
        allow_older_summary=(not initial_compact_lane),
    )
    if (
        not initial_compact_lane
        and _should_use_compact_surface_context(
            relational_topic=str(relational_topic or ""),
            context_plane=context_plane,
        )
    ):
        context_plane = assemble_prompt_context_plane(
            node_id=SURFACE_CONTEXT_NODE_ID,
            runtime_payload=runtime_payload,
            history_max_turns=6,
            allow_older_summary=False,
        )
    return _surface_context_payload_from_plane(
        context_plane,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        bond_summary=bond_summary,
        retrieval_summary=retrieval_summary,
    )


def _build_surface_runtime_payload(
    *,
    user_message: str,
    render_target_lang: str,
    emotion_context: Mapping[str, Any] | None,
    emotion_snapshot: Mapping[str, Any] | None,
    retrieval_summary: Mapping[str, Any] | None,
    retrieval_evidence_items: list[dict[str, Any]] | None,
    person_id: str,
    session_id: str,
    request_id: str,
    thread_id: str | None,
    bond_summary: Mapping[str, Any] | None,
    intent_draft: Mapping[str, Any] | None,
    eval_history_turns: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    runtime_input = build_chat_runtime_input(
        request_id=request_id,
        person_id=person_id,
        session_id=session_id,
        thread_id=thread_id,
        user_message=user_message,
        render_target_lang=render_target_lang,
        retrieval_summary=dict(retrieval_summary or {"status": "SKIPPED"}),
        retrieval_evidence_items=list(retrieval_evidence_items or []),
        emotion_state=dict(emotion_context or {}),
        emotion_snapshot=dict(emotion_snapshot or {}),
        bond_summary=dict(bond_summary or {}),
        attachments=[],
        multimodal_observations={},
        attachment_summary="",
        scene_type="CHAT",
    )
    payload = runtime_input.to_runtime_payload(
        prompt_context={
            "bond_summary": dict(bond_summary or {}),
            "request_id": request_id,
            "person_id": person_id,
            "session_id": session_id,
            "thread_id": thread_id,
        },
        prompt_context_validation={},
        prompt_context_observability={},
    )
    payload["bond_summary"] = dict(bond_summary or {})
    payload["emotion_context"] = dict(emotion_context or {})
    payload["emotion_snapshot"] = dict(emotion_snapshot or {})
    payload["intent_draft"] = dict(intent_draft or {})
    if eval_history_turns:
        payload["ab_eval"] = {
            "history_turns": [
                dict(item or {})
                for item in list(eval_history_turns or [])
                if isinstance(item, Mapping)
            ]
        }
    return payload


def _surface_context_payload_from_plane(
    context_plane: Any,
    *,
    emotion_context: Mapping[str, Any] | None,
    emotion_snapshot: Mapping[str, Any] | None,
    bond_summary: Mapping[str, Any] | None,
    retrieval_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    continuity = getattr(context_plane, "continuity", None)
    reply_plan = dict(getattr(getattr(context_plane, "reply_plan", None), "state", {}) or {})
    memory_packet = _memory_packet_from_retrieval_summary(retrieval_summary)
    affect_state_payload = _state_as_dict(getattr(getattr(context_plane, "affect", None), "state", None))
    relationship_state_payload = _state_as_dict(getattr(getattr(context_plane, "relationship", None), "state", None))
    affect_packet = build_surface_affect_control_packet(
        emotion_state=affect_state_payload or dict(emotion_context or {}) or dict(emotion_snapshot or {}),
        bond_summary={
            **dict(bond_summary or {}),
            **relationship_state_payload,
        },
        relationship_clues=tuple(memory_packet.relationship_clues if memory_packet else ()),
        task_mode=str(
            reply_plan.get("task_mode")
            or reply_plan.get("primary_topic")
            or reply_plan.get("response_goal")
            or ""
        ),
    )
    payload: dict[str, Any] = {
        "conversation_history": {
            "turns_injected": int(getattr(continuity, "turns_injected", 0) or 0),
            "fetched_turns": int(getattr(continuity, "fetched_turns", 0) or 0),
            "older_turn_count": int(getattr(continuity, "older_turn_count", 0) or 0),
            "summary_present": bool(getattr(continuity, "summary", "")),
            "oldest_ts_ms": getattr(continuity, "oldest_ts_ms", None),
            "newest_ts_ms": getattr(continuity, "newest_ts_ms", None),
            "skipped_corrupted_count": int(getattr(continuity, "skipped_corrupted_count", 0) or 0),
            "skipped_empty_count": int(getattr(continuity, "skipped_empty_count", 0) or 0),
            "deduped_same_role_count": int(getattr(continuity, "deduped_same_role_count", 0) or 0),
        },
        "reply_plan": reply_plan,
        "surface_affect_packet": affect_packet.as_dict(),
    }
    if memory_packet is not None:
        payload["surface_memory_recall_packet"] = memory_packet.as_dict()
    for output_key, plane_attr in (
        ("conversation_interpretation", "conversation_interpretation"),
        ("interaction_thread", "interaction_thread"),
        ("interaction_state", "interaction_state"),
        ("topic_ledger", "topic_ledger"),
        ("tactic_ledger", "tactic_ledger"),
        ("affect_state", "affect"),
        ("relationship_state", "relationship"),
        ("drive_state", "drive"),
        ("multimodal", "multimodal"),
    ):
        state = getattr(getattr(context_plane, plane_attr, None), "state", None)
        state_payload = _state_as_dict(state)
        if state_payload:
            payload[output_key] = state_payload
    return payload


def _should_use_compact_surface_context(*, relational_topic: str, context_plane: Any) -> bool:
    if str(relational_topic or "").strip():
        return True
    topic = _extract_context_topic(context_plane).lower()
    thread_name = _extract_context_thread(context_plane).lower()
    return topic in _COMPACT_LANE_TOPICS or thread_name in _COMPACT_LANE_THREADS


def _memory_packet_from_retrieval_summary(retrieval_summary: Mapping[str, Any] | None) -> MemoryRecallPacket | None:
    summary = dict(retrieval_summary or {})
    for key in _MEMORY_PACKET_KEYS:
        raw = summary.get(key)
        if isinstance(raw, (MemoryRecallPacket, Mapping)):
            return memory_recall_packet_from_mapping(raw)
    return None


def _extract_context_topic(context_plane: Any) -> str:
    interaction_state = getattr(getattr(context_plane, "interaction_state", None), "state", None)
    payload = _state_as_dict(interaction_state)
    return str(
        getattr(interaction_state, "current_topic", "")
        or payload.get("current_topic")
        or ""
    ).strip()


def _extract_context_thread(context_plane: Any) -> str:
    interaction_state = getattr(getattr(context_plane, "interaction_state", None), "state", None)
    interaction_state_payload = _state_as_dict(interaction_state)
    thread = str(
        getattr(interaction_state, "ongoing_thread", "")
        or interaction_state_payload.get("ongoing_thread")
        or ""
    ).strip()
    if thread:
        return thread
    interaction_thread = getattr(getattr(context_plane, "interaction_thread", None), "state", None)
    interaction_thread_payload = _state_as_dict(interaction_thread)
    return str(
        getattr(interaction_thread, "thread_kind", "")
        or interaction_thread_payload.get("thread_kind")
        or ""
    ).strip()


def _state_as_dict(state: Any) -> dict[str, Any]:
    if state is None:
        return {}
    if hasattr(state, "as_dict"):
        payload = state.as_dict()
        return dict(payload or {}) if isinstance(payload, Mapping) else {}
    if isinstance(state, Mapping):
        return dict(state)
    return {}


__all__ = ["SURFACE_CONTEXT_NODE_ID", "build_surface_context_packet"]
