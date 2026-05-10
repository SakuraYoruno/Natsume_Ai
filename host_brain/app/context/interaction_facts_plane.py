from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.conversation import conversation_max_turns

from .affect_state import AffectStateBlock, resolve_affect_state
from .conversation_interpretation import ConversationInterpretation, interpret_conversation
from .drive_state import DriveStateBlock, resolve_drive_state
from .emotional_act import EmotionalAct, resolve_emotional_act
from .interaction_thread import InteractionThreadState, resolve_interaction_thread
from .perception_packet import PerceptionPacket, build_perception_packet
from .relationship_state import RelationshipStateBlock, resolve_relationship_state
from .social_act import SocialAct, resolve_social_act
from .tactic_ledger import TacticLedgerBlock
from .topic_ledger import TopicLedgerBlock


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _clean_text(raw: Any) -> str:
    return str(raw or "").strip()


@dataclass(frozen=True)
class InteractionFactsPlane:
    """Read-only facts for normal chat generation.

    This plane deliberately excludes reply policies, tactic families,
    trajectories, and fallback text.
    """

    user_message: str = ""
    history_turns: list[dict[str, Any]] = field(default_factory=list)
    older_summary: str = ""
    relationship_stage: str = "STRANGER"
    relationship_state: dict[str, Any] = field(default_factory=dict)
    affect_state: dict[str, Any] = field(default_factory=dict)
    drive_state: dict[str, Any] = field(default_factory=dict)
    conversation_interpretation: dict[str, Any] = field(default_factory=dict)
    ongoing_thread: dict[str, Any] = field(default_factory=dict)
    emotional_act: dict[str, Any] = field(default_factory=dict)
    social_act: dict[str, Any] = field(default_factory=dict)
    perception: dict[str, Any] = field(default_factory=dict)
    memory_snippets: list[str] = field(default_factory=list)
    route_seed: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_message": self.user_message,
            "history_turns": list(self.history_turns or []),
            "older_summary": self.older_summary,
            "relationship_stage": self.relationship_stage,
            "relationship_state": dict(self.relationship_state or {}),
            "affect_state": dict(self.affect_state or {}),
            "drive_state": dict(self.drive_state or {}),
            "conversation_interpretation": dict(self.conversation_interpretation or {}),
            "ongoing_thread": dict(self.ongoing_thread or {}),
            "emotional_act": dict(self.emotional_act or {}),
            "social_act": dict(self.social_act or {}),
            "perception": dict(self.perception or {}),
            "memory_snippets": list(self.memory_snippets or []),
            "route_seed": dict(self.route_seed or {}),
        }


def _normalize_eval_history(eval_history_turns: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for index, raw_turn in enumerate(list(eval_history_turns or [])):
        if not isinstance(raw_turn, Mapping):
            continue
        role = _clean_text(raw_turn.get("role")).lower()
        if role not in {"user", "assistant"}:
            continue
        content = _clean_text(raw_turn.get("content") or raw_turn.get("text"))
        if not content:
            continue
        turns.append(
            {
                "turn_id": _clean_text(raw_turn.get("turn_id") or f"eval_{index}"),
                "request_id": _clean_text(raw_turn.get("request_id")),
                "role": role,
                "content": content,
                "turn_index": int(raw_turn.get("turn_index") or index),
                "lang": _clean_text(raw_turn.get("lang") or "zh"),
                "metadata": _safe_mapping(raw_turn.get("metadata")),
            }
        )
    return turns


def _extract_memory_snippets(retrieval_payload: Mapping[str, Any] | None) -> list[str]:
    payload = _safe_mapping(retrieval_payload)
    snippets: list[str] = []
    for item in list(payload.get("evidence_items") or payload.get("items") or []):
        if not isinstance(item, Mapping):
            continue
        text = _clean_text(
            item.get("content")
            or item.get("text")
            or item.get("snippet")
            or item.get("summary")
        )
        if text and text not in snippets:
            snippets.append(text)
        if len(snippets) >= 5:
            break
    return snippets


def assemble_interaction_facts_plane(
    *,
    runtime_payload: Mapping[str, Any],
    history_max_turns: int | None = None,
    allow_older_summary: bool = True,
) -> InteractionFactsPlane:
    from host_brain.app.dependencies import (  # noqa: PLC0415
        get_conversation_history,
        get_conversation_turn_store,
        get_session_tactic_ledger_store,
        get_session_topic_ledger_store,
    )

    payload = _safe_mapping(runtime_payload)
    request_payload = _safe_mapping(payload.get("request"))
    request_id = _clean_text(request_payload.get("request_id"))
    session_id = _clean_text(request_payload.get("session_id"))
    person_id = _clean_text(request_payload.get("person_id"))
    user_message = _clean_text(request_payload.get("user_message"))
    render_target_lang = _clean_text(payload.get("render_target_lang") or request_payload.get("render_target_lang") or "zh")
    render_target_lang = "ja" if render_target_lang == "ja" else "zh"

    eval_history = [
        dict(item or {})
        for item in list(_safe_mapping(payload.get("ab_eval")).get("history_turns") or [])
        if isinstance(item, Mapping)
    ]
    history_turns: list[dict[str, Any]] = []
    older_summary = ""
    current_turn_index: int | None = None
    if eval_history:
        history_turns = _normalize_eval_history(eval_history)
        current_turn_index = len(history_turns)
    elif request_id and session_id:
        max_turns = max(1, int(history_max_turns if history_max_turns is not None else conversation_max_turns()))
        window = get_conversation_history().fetch_window_for_messages(
            session_id=session_id,
            max_turns=max_turns,
            exclude_request_id=request_id,
            render_target_lang=render_target_lang,
            summary_threshold=(0 if not allow_older_summary else None),
        )
        history_turns = [
            {
                "turn_id": turn.turn_id,
                "request_id": turn.request_id,
                "role": turn.role,
                "content": turn.content,
                "turn_index": turn.turn_index,
                "lang": turn.lang,
                "metadata": dict(turn.metadata or {}),
            }
            for turn in list(window.recent_turns or [])
        ]
        older_summary = _clean_text(window.older_summary)
        try:
            current_turn_index = int(get_conversation_turn_store().next_turn_index(session_id=session_id) - 1)
        except Exception:
            current_turn_index = None

    multimodal_payload = _safe_mapping(payload.get("multimodal_input"))
    observations = _safe_mapping(
        multimodal_payload.get("observations")
        or request_payload.get("multimodal_observations")
    )
    perception: PerceptionPacket = build_perception_packet(
        multimodal_observations=observations,
        multimodal_payload=multimodal_payload,
        attachment_summary=_clean_text(request_payload.get("attachment_summary")),
        user_text=user_message,
    )

    topic_ledger: TopicLedgerBlock = (
        get_session_topic_ledger_store().fetch_block(
            session_id=session_id,
            current_turn_index=current_turn_index,
        )
        if session_id
        else TopicLedgerBlock()
    )
    tactic_ledger: TacticLedgerBlock = (
        get_session_tactic_ledger_store().fetch_block(session_id=session_id)
        if session_id
        else TacticLedgerBlock()
    )

    preliminary: ConversationInterpretation = interpret_conversation(
        recent_turns=history_turns[-6:],
        user_message=user_message,
        topic_ledger=topic_ledger,
        multimodal_observations=perception.as_dict(),
    )
    thread: InteractionThreadState = resolve_interaction_thread(
        recent_turns=history_turns[-8:],
        current_literal_topic=preliminary.literal_topic,
        current_user_message=user_message,
        multimodal_observations=perception.as_dict(),
    )
    interpretation: ConversationInterpretation = interpret_conversation(
        recent_turns=history_turns[-6:],
        user_message=user_message,
        topic_ledger=topic_ledger,
        multimodal_observations=perception.as_dict(),
        interaction_thread=thread,
    )

    merged_emotion = _safe_mapping(payload.get("emotion_context")) or _safe_mapping(_safe_mapping(payload.get("emotion")).get("state"))
    affect: AffectStateBlock = resolve_affect_state(
        emotion_context=merged_emotion,
        body_sense=_safe_mapping(payload.get("body_sense")),
        offline_state=_safe_mapping(payload.get("offline_state")),
        silence_state=_safe_mapping(payload.get("silence_state")),
    )
    prompt_context = _safe_mapping(payload.get("prompt_context"))
    bond_summary = _safe_mapping(prompt_context.get("bond_summary") or payload.get("bond_summary"))
    relationship: RelationshipStateBlock = resolve_relationship_state(
        bond_summary=bond_summary,
        social_brain_state=_safe_mapping(payload.get("social_brain_state")),
    )
    drive: DriveStateBlock = resolve_drive_state(
        motive_state=_safe_mapping(payload.get("motive_state")),
        affect_state=affect,
        relationship_state=relationship,
        dialogue_state={
            "current_user_act": interpretation.literal_topic,
            "repeat_question_count": interpretation.repeat_question_count,
            "user_stance": interpretation.user_stance,
        },
    )

    route_seed = _safe_mapping(payload.get("intent_draft"))
    if person_id:
        route_seed.setdefault("person_id", person_id)

    affect_payload = affect.as_dict()
    for key in (
        "relational_read",
        "emotion_reason",
        "response_emotion",
        "emotional_boundary",
        "emotion_target",
    ):
        value = _clean_text(merged_emotion.get(key))
        if value:
            affect_payload[key] = value
    emotional_act: EmotionalAct = resolve_emotional_act(
        user_message=user_message,
        conversation_interpretation=interpretation.as_dict(),
        affect_state=affect_payload,
        relationship_state=relationship.as_dict(),
        explicit_context=merged_emotion,
    )
    social_act: SocialAct = resolve_social_act(
        user_message=user_message,
        conversation_interpretation=interpretation.as_dict(),
    )

    return InteractionFactsPlane(
        user_message=user_message,
        history_turns=history_turns,
        older_summary=older_summary,
        relationship_stage=relationship.bond_stage,
        relationship_state=relationship.as_dict(),
        affect_state=affect_payload,
        drive_state=drive.as_dict(),
        conversation_interpretation=interpretation.as_dict(),
        ongoing_thread=thread.as_dict(),
        emotional_act=emotional_act.as_dict(),
        social_act=social_act.as_dict(),
        perception=perception.as_dict(),
        memory_snippets=_extract_memory_snippets(_safe_mapping(payload.get("retrieval"))),
        route_seed=route_seed,
    )


__all__ = [
    "InteractionFactsPlane",
    "assemble_interaction_facts_plane",
    "resolve_emotional_act",
    "resolve_social_act",
]
