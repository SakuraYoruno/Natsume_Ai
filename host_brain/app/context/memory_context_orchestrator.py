from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from host_brain.core.ids import now_ms as _now_ms
from host_brain.core.runtime_helpers import _coerce_mapping as _mapping
from host_brain.persistence.experience_repository import ExperienceRepository


LAYER_ORDER: tuple[str, ...] = (
    "current_user_message",
    "recent_visible_memory_events",
    "previous_visible_assistant_event",
    "pending_life_proactive_episode",
    "private_life_event_summary",
    "short_term_belief",
    "explicit_user_preference_profile",
    "bound_recommendation_feedback",
    "long_term_memory_evidence",
    "conversation_experience_lesson",
    "digital_life_state",
)

BELIEF_TTL_MS = 1_800_000
UNRESOLVED_PREFERENCE_RE = re.compile(
    r"(这本|那本|这部|那部|这个|那个|它).{0,12}(喜欢|不喜欢|不错|好看|对味|讨厌)"
)
CONTEXT_QUESTION_RE = re.compile(r"(怎么了|咋了|为什么|为啥|什么情况|你刚才|刚刚说)")
CONTINUE_RE = re.compile(r"(继续|然后呢|接着说|往下)")
SPECIFIC_REFERENCE_FOLLOWUP_RE = re.compile(
    r"^(?:什么|啥|哪个|哪一个|哪种|哪类|哪件|哪一件)(?P<anchor>[\u4e00-\u9fffA-Za-z0-9_]{1,16})(?:[啊呀呢嘛吧吗？?]*)$"
)


def _text(value: Any, *, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _belief_can_drive_reply(
    belief: Mapping[str, Any], *, now_ms: int | None = None
) -> bool:
    item = _mapping(belief)
    if item.get("can_drive_reply") is False:
        return False
    expires_at = int(item.get("expires_at_ms") or 0)
    if expires_at and int(now_ms or _now_ms()) > expires_at:
        return False
    selected = _text(item.get("selected_intent"), limit=80)
    confidence = float(item.get("confidence") or 0.0)
    return not (selected == "open_chat" and confidence < 0.55)


def _event_time_ms(event: Mapping[str, Any] | None) -> int:
    item = _mapping(event)
    for key in ("created_at_ms", "ts_ms", "sent_at_ms"):
        try:
            value = int(item.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    return 0


def _pending_episode_can_bind(
    *, pending_episode: Mapping[str, Any], latest_assistant: Mapping[str, Any]
) -> bool:
    if not pending_episode:
        return False
    latest_time = _event_time_ms(latest_assistant)
    pending_time = _event_time_ms(pending_episode)
    return not (latest_time and pending_time and latest_time > pending_time)


def _latest_user_before_assistant(
    events: list[dict[str, Any]], *, assistant_event_id: str
) -> dict[str, Any]:
    if not assistant_event_id:
        return {}
    latest_user: dict[str, Any] = {}
    for event in events:
        if str(event.get("event_id") or "") == assistant_event_id:
            return latest_user
        if str(event.get("role") or "") == "user":
            latest_user = event
    return {}


def _binding_decision(
    *,
    message: str,
    visible_events: list[dict[str, Any]],
    pending_episode: Mapping[str, Any],
) -> dict[str, Any]:
    compact = _text(message, limit=200)
    assistants = [
        event
        for event in visible_events
        if str(event.get("role") or "") == "assistant"
    ]
    latest_assistant = assistants[-1] if assistants else {}
    specific_match = SPECIFIC_REFERENCE_FOLLOWUP_RE.search(
        re.sub(r"\s+", "", compact)
    )
    if specific_match and latest_assistant:
        anchor = str(specific_match.group("anchor") or "").strip()
        latest_content = _text(
            latest_assistant.get("content")
            or latest_assistant.get("content_summary"),
            limit=500,
        )
        if anchor and anchor in latest_content:
            previous_user = _latest_user_before_assistant(
                visible_events,
                assistant_event_id=str(latest_assistant.get("event_id") or ""),
            )
            return {
                "binding_kind": "specific_reference_followup",
                "bound_event_id": latest_assistant.get("event_id"),
                "bound_event_kind": latest_assistant.get("event_kind"),
                "bound_content": latest_assistant.get("content_summary")
                or latest_assistant.get("content"),
                "bound_previous_user_content": previous_user.get("content_summary")
                or previous_user.get("content"),
                "reference_anchor": anchor,
                "reason_code": "BOUND_RECENT_VISIBLE_ASSISTANT_REFERENCE",
            }
    if CONTEXT_QUESTION_RE.search(compact):
        if latest_assistant:
            return {
                "binding_kind": "context_question",
                "bound_event_id": latest_assistant.get("event_id"),
                "bound_event_kind": latest_assistant.get("event_kind"),
                "bound_content": latest_assistant.get("content_summary")
                or latest_assistant.get("content"),
                "reason_code": "BOUND_RECENT_VISIBLE_ASSISTANT_EVENT",
            }
        if _pending_episode_can_bind(
            pending_episode=pending_episode, latest_assistant=latest_assistant
        ):
            return {
                "binding_kind": "pending_proactive_episode",
                "bound_event_id": pending_episode.get("episode_id"),
                "bound_event_kind": "life_proactive_sent",
                "bound_content": pending_episode.get("message_text"),
                "reason_code": "PENDING_PROACTIVE_EPISODE_AVAILABLE",
            }
    if CONTINUE_RE.search(compact):
        target = latest_assistant or (visible_events[-1] if visible_events else {})
        if target:
            return {
                "binding_kind": "continue",
                "bound_event_id": target.get("event_id"),
                "bound_event_kind": target.get("event_kind"),
                "bound_content": target.get("content_summary")
                or target.get("content"),
                "reason_code": "BOUND_RECENT_VISIBLE_EVENT",
            }
    if _pending_episode_can_bind(
        pending_episode=pending_episode, latest_assistant=latest_assistant
    ):
        return {
            "binding_kind": "pending_proactive_episode",
            "bound_event_id": pending_episode.get("episode_id"),
            "bound_event_kind": "life_proactive_sent",
            "bound_content": pending_episode.get("message_text"),
            "reason_code": "PENDING_PROACTIVE_EPISODE_AVAILABLE",
        }
    return {"binding_kind": "none", "reason_code": "NO_EXPLICIT_FOLLOWUP_BINDING"}


def _binding_suppresses_long_term_evidence(binding: Mapping[str, Any]) -> bool:
    kind = str(binding.get("binding_kind") or "").strip()
    reason = str(binding.get("reason_code") or "").strip()
    return bool(binding.get("bound_event_id")) and (
        kind
        in {
            "context_question",
            "continue",
            "pending_proactive_episode",
            "specific_reference_followup",
        }
        or reason
        in {
            "BOUND_RECENT_VISIBLE_ASSISTANT_EVENT",
            "BOUND_RECENT_VISIBLE_EVENT",
            "BOUND_RECENT_VISIBLE_ASSISTANT_REFERENCE",
            "PENDING_PROACTIVE_EPISODE_AVAILABLE",
        }
    )


def build_memory_context_debug(
    *,
    ledger: Any,
    session_id: str = "",
    person_id: str = "default",
    user_message: str = "",
    current_user_message: str = "",
    request_id: str = "",
    limit: int = 8,
) -> dict[str, Any]:
    from host_brain.app.conversation_event_resolver import resolve_conversation_event_action

    now = _now_ms()
    sid = _text(session_id, limit=180)
    pid = _text(person_id, limit=120) or "default"
    message = _text(user_message or current_user_message, limit=500)
    repository = ExperienceRepository.from_ledger(ledger)
    snapshot = repository.load_memory_context_snapshot(
        person_id=pid,
        session_id=sid,
        request_id=_text(request_id, limit=180),
        current_user_message=message,
        now_ms=now,
        limit=limit,
        conversation_event_resolver=resolve_conversation_event_action,
    )
    selected_context: dict[str, Any] = {"current_user_message": message}
    suppressed: list[dict[str, Any]] = []
    reason_codes: list[str] = []

    recent_visible_events = list(snapshot.get("recent_visible_events") or [])
    selected_context["recent_visible_memory_events"] = recent_visible_events
    selected_context["private_life_event_summary"] = [
        {
            "event_id": item.get("event_id"),
            "event_kind": item.get("event_kind"),
            "content_summary": item.get("content_summary"),
            "created_at_ms": item.get("created_at_ms"),
        }
        for item in list(snapshot.get("private_events") or [])
    ]
    assistant_events = [
        item
        for item in recent_visible_events
        if str(item.get("role") or "") == "assistant"
    ]
    if assistant_events:
        selected_context["previous_visible_assistant_event"] = assistant_events[-1]

    recent_turns = list(snapshot.get("recent_turns") or [])
    selected_context["current_session_recent_turns"] = recent_turns
    proactive_turn = _mapping(snapshot.get("proactive_turn"))
    if proactive_turn:
        selected_context["previous_life_proactive_turn"] = proactive_turn
    pending_proactive_episode = _mapping(snapshot.get("pending_proactive_episode"))
    if pending_proactive_episode:
        selected_context["pending_life_proactive_episode"] = pending_proactive_episode
        if not proactive_turn:
            selected_context["previous_life_proactive_turn"] = {
                "turn_id": pending_proactive_episode.get("episode_id"),
                "request_id": pending_proactive_episode.get("source_outbox_id")
                or pending_proactive_episode.get("episode_id"),
                "role": "assistant",
                "content": pending_proactive_episode.get("message_text"),
                "ts_ms": pending_proactive_episode.get("sent_at_ms"),
                "turn_index": None,
                "metadata": {
                    "source_kind": "life_proactive",
                    "life_source_kind": _mapping(
                        pending_proactive_episode.get("metadata")
                    ).get("life_source_kind"),
                    "motive": pending_proactive_episode.get("motive"),
                    "proactive_episode_id": pending_proactive_episode.get(
                        "episode_id"
                    ),
                },
            }
    binding_decision = _binding_decision(
        message=message,
        visible_events=recent_visible_events,
        pending_episode=pending_proactive_episode,
    )

    conversation_event_resolution = _mapping(
        snapshot.get("conversation_event_resolution")
    )
    selected_context["conversation_event_resolution"] = conversation_event_resolution
    if str(conversation_event_resolution.get("status") or "") == "RESOLVED":
        reason_codes.append(
            str(
                conversation_event_resolution.get("reason_code")
                or "CONVERSATION_EVENT_RESOLVED"
            )
        )

    belief = _mapping(snapshot.get("belief"))
    if belief:
        if _belief_can_drive_reply(belief, now_ms=now):
            selected_context["short_term_belief"] = belief
        else:
            suppressed.append(
                {
                    "layer": "short_term_belief",
                    "reason_code": "BELIEF_EXPIRED_OR_LOW_CONFIDENCE",
                    "item": belief,
                }
            )
            reason_codes.append("BELIEF_EXPIRED_OR_LOW_CONFIDENCE")

    try:
        from host_brain.app.context.dialogue_frame import (  # noqa: PLC0415
            build_dialogue_frame,
        )

        dialogue_frame = build_dialogue_frame(
            current_user_message=message,
            recent_turns=recent_turns,
            recent_visible_events=recent_visible_events,
            conversation_event_resolution=conversation_event_resolution,
            previous_belief=belief,
        )
    except Exception as exc:
        dialogue_frame = {
            "schema_version": "Natsume.dialogue_frame.v1",
            "status": "UNRESOLVED",
            "reason_code": "DIALOGUE_FRAME_ERROR",
            "error": str(exc)[:200],
        }
    selected_context["dialogue_frame"] = dialogue_frame
    if str(dialogue_frame.get("status") or "") == "RESOLVED":
        reason_codes.append("DIALOGUE_FRAME_RESOLVED")

    selected_context["explicit_user_preference_profile"] = list(
        snapshot.get("preference_profiles") or []
    )
    selected_context["bound_recommendation_feedback"] = list(
        snapshot.get("recommendation_deliveries") or []
    )

    suppress_long_term = _binding_suppresses_long_term_evidence(
        binding_decision
    ) or bool(_mapping(dialogue_frame).get("suppress_long_term_memory"))
    memory_rows = [] if suppress_long_term else list(
        snapshot.get("semantic_memories") or []
    )
    if suppress_long_term:
        reason = (
            "SUPPRESSED_BY_DIALOGUE_FRAME"
            if bool(_mapping(dialogue_frame).get("suppress_long_term_memory"))
            else "SUPPRESSED_BY_RECENT_VISIBLE_BINDING"
        )
        suppressed.append(
            {
                "layer": "long_term_memory_evidence",
                "reason_code": reason,
                "binding": binding_decision,
                "dialogue_frame": dialogue_frame,
            }
        )
        reason_codes.append(reason)
    memory_evidence = []
    for row in memory_rows:
        summary = _text(row.get("summary"), limit=500)
        if UNRESOLVED_PREFERENCE_RE.search(summary):
            suppressed.append(
                {
                    "layer": "long_term_memory_evidence",
                    "reason_code": "UNRESOLVED_PREFERENCE_MEMORY",
                    "item": summary,
                }
            )
            reason_codes.append("UNRESOLVED_PREFERENCE_MEMORY")
            continue
        memory_evidence.append(
            {
                "semantic_id": row.get("semantic_id"),
                "summary": summary,
                "confidence": row.get("confidence"),
                "tags": row.get("tags") or [],
                "ts_ms": row.get("ts_ms"),
            }
        )
    selected_context["long_term_memory_evidence"] = memory_evidence

    lessons = []
    for row in list(snapshot.get("lessons") or []):
        success = int(row.get("success_count") or 0)
        failure = int(row.get("failure_count") or 0)
        confidence = float(row.get("confidence") or 0.0)
        lesson = {
            "lesson_id": row.get("lesson_id"),
            "domain": row.get("domain"),
            "pattern": row.get("pattern"),
            "lesson": row.get("lesson"),
            "success_count": success,
            "failure_count": failure,
            "confidence": confidence,
            "updated_at_ms": row.get("updated_at_ms"),
        }
        if failure > success and confidence < 0.85:
            suppressed.append(
                {
                    "layer": "conversation_experience_lesson",
                    "reason_code": "FAILED_LESSON_DISABLED",
                    "item": lesson,
                }
            )
            reason_codes.append("FAILED_LESSON_DISABLED")
            continue
        lessons.append(lesson)
    selected_context["conversation_experience_lesson"] = lessons
    selected_context["digital_life_state"] = _mapping(
        snapshot.get("digital_life_state")
    )

    return {
        "selected_context": selected_context,
        "selected_events": {
            "recent_visible": recent_visible_events,
            "previous_visible_assistant": selected_context.get(
                "previous_visible_assistant_event", {}
            ),
            "pending_life_proactive_episode": pending_proactive_episode,
        },
        "binding_decision": binding_decision,
        "conversation_event_resolution": conversation_event_resolution,
        "layer_order": list(LAYER_ORDER),
        "suppressed_items": suppressed,
        "reason_codes": sorted(set(reason_codes)),
        "current_turn_binding": dict(binding_decision),
        "memory_evidence": memory_evidence,
        "projection_status": {
            "recent_visible": [
                item.get("projection_status", {}) for item in recent_visible_events
            ],
        },
    }


def memory_health(*, ledger: Any, person_id: str = "default") -> dict[str, Any]:
    pid = _text(person_id, limit=120) or "default"
    health: dict[str, Any] = ExperienceRepository.from_ledger(ledger).memory_health(
        person_id=pid,
        now_ms=_now_ms(),
        belief_ttl_ms=BELIEF_TTL_MS,
    )
    try:
        from host_brain.persistence.context_memory_repository import (  # noqa: PLC0415
            MemoryEventRepository,
        )

        event_health = MemoryEventRepository.from_ledger(ledger).health(person_id=pid)
        health.update(event_health)
        health["conversation_turn_spine_gaps"] = int(
            event_health.get("visible_turn_projection_gaps", 0)
        )
    except Exception:
        health.update(
            {
                "memory_event_projection_gaps": 0,
                "visible_turn_projection_gaps": 0,
                "proactive_episode_projection_gaps": 0,
                "private_life_event_projection_gaps": 0,
                "orphan_projection_rows": 0,
            }
        )
    return health


def run_memory_sanitize(
    *, ledger: Any, person_id: str = "default", dry_run: bool = False
) -> dict[str, Any]:
    return ExperienceRepository.from_ledger(ledger).sanitize_memory(
        person_id=_text(person_id, limit=120) or "default",
        now_ms=_now_ms(),
        dry_run=bool(dry_run),
    )


__all__ = [
    "BELIEF_TTL_MS",
    "LAYER_ORDER",
    "build_memory_context_debug",
    "memory_health",
    "run_memory_sanitize",
]
