from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from typing import Any


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
UNSUPPORTED_PROACTIVE_ACTION_RE = re.compile(
    r"(盯着屏幕|一直等你|一直守着|寸步不离|收拾了一遍|把周围收拾|刚把周围收拾)"
)
UNRESOLVED_PREFERENCE_RE = re.compile(r"(这本|那本|这部|那部|这个|那个|它).{0,12}(喜欢|不喜欢|不错|好看|对味|讨厌)")
CONTEXT_QUESTION_RE = re.compile(r"(怎么了|咋了|为什么|为啥|什么情况|你刚才|刚刚说)")
CONTINUE_RE = re.compile(r"(继续|然后呢|接着说|往下)")
SPECIFIC_REFERENCE_FOLLOWUP_RE = re.compile(
    r"^(?:什么|啥|哪个|哪一个|哪种|哪类|哪件|哪一件)(?P<anchor>[\u4e00-\u9fffA-Za-z0-9_]{1,16})(?:[啊呀呢嘛吧吗？?]*)$"
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _conn(ledger: Any) -> Any:
    conn = getattr(ledger, "_conn", None)
    if conn is None:
        raise RuntimeError("ledger sqlite connection unavailable")
    return conn


def _loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _text(value: Any, *, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _belief_can_drive_reply(belief: Mapping[str, Any], *, now_ms: int | None = None) -> bool:
    item = _mapping(belief)
    if item.get("can_drive_reply") is False:
        return False
    expires_at = int(item.get("expires_at_ms") or 0)
    if expires_at and int(now_ms or _now_ms()) > expires_at:
        return False
    selected = _text(item.get("selected_intent"), limit=80)
    confidence = float(item.get("confidence") or 0.0)
    if selected == "open_chat" and confidence < 0.55:
        return False
    return True


def _last_life_proactive_turn(
    conn: Any,
    *,
    session_id: str,
    limit: int = 12,
    before_ms: int | None = None,
) -> dict[str, Any]:
    if not session_id:
        return {}
    clauses = ["session_id=?"]
    params: list[Any] = [session_id]
    if before_ms:
        clauses.append("ts_ms <= ?")
        params.append(int(before_ms))
    params.append(max(1, int(limit)))
    rows = conn.execute(
        f"""
SELECT turn_id, request_id, role, content, ts_ms, turn_index, metadata
FROM conversation_turn
WHERE {' AND '.join(clauses)}
ORDER BY ts_ms DESC
LIMIT ?
""",
        params,
    ).fetchall()
    for row in rows:
        metadata = _mapping(_loads(row["metadata"], {}))
        if str(row["role"] or "") == "assistant" and str(metadata.get("source_kind") or "") == "life_proactive":
            return {
                "turn_id": row["turn_id"],
                "request_id": row["request_id"],
                "role": row["role"],
                "content": row["content"],
                "ts_ms": row["ts_ms"],
                "turn_index": row["turn_index"],
                "metadata": metadata,
            }
    return {}


def _last_pending_proactive_episode(conn: Any, *, person_id: str, now_ms: int) -> dict[str, Any]:
    if not _table_exists(conn, "proactive_interaction_episode"):
        return {}
    row = conn.execute(
        """
SELECT episode_id, source_action_id, source_outbox_id, motive, message_text, expected_response_kind, sent_at_ms, metadata_json
FROM proactive_interaction_episode
WHERE person_id=? AND outcome='pending'
ORDER BY sent_at_ms DESC
LIMIT 1
""",
        (person_id,),
    ).fetchone()
    if not row:
        return {}
    sent_at = int(row["sent_at_ms"] or 0)
    if sent_at and int(now_ms or _now_ms()) - sent_at > 3_600_000:
        return {}
    metadata = _mapping(_loads(row["metadata_json"], {}))
    return {
        "episode_id": row["episode_id"],
        "source_action_id": row["source_action_id"],
        "source_outbox_id": row["source_outbox_id"],
        "motive": row["motive"],
        "message_text": row["message_text"],
        "expected_response_kind": row["expected_response_kind"],
        "sent_at_ms": sent_at,
        "metadata": metadata,
    }


def _recent_memory_events(
    conn: Any,
    *,
    person_id: str,
    session_id: str,
    visibility: str,
    limit: int,
    before_ms: int | None = None,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "memory_event"):
        return []
    clauses = ["person_id=?", "active=1"]
    params: list[Any] = [person_id]
    if session_id:
        clauses.append("session_id=?")
        params.append(session_id)
    if visibility:
        clauses.append("visibility=?")
        params.append(visibility)
    if before_ms:
        clauses.append("created_at_ms <= ?")
        params.append(int(before_ms))
    params.append(max(1, min(int(limit or 8), 50)))
    rows = conn.execute(
        f"""
SELECT event_id, session_id, conversation_id, source_kind, event_kind, visibility, role,
       content, content_summary, refs_json, projection_status_json, created_at_ms
FROM memory_event
WHERE {' AND '.join(clauses)}
ORDER BY created_at_ms DESC, id DESC
LIMIT ?
""",
        params,
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        events.append(
            {
                "event_id": row["event_id"],
                "session_id": row["session_id"],
                "conversation_id": row["conversation_id"],
                "source_kind": row["source_kind"],
                "event_kind": row["event_kind"],
                "visibility": row["visibility"],
                "role": row["role"],
                "content": row["content"],
                "content_summary": row["content_summary"],
                "refs": _loads(row["refs_json"], {}),
                "projection_status": _loads(row["projection_status_json"], {}),
                "created_at_ms": row["created_at_ms"],
            }
        )
    return list(reversed(events))


def _current_request_user_event_boundary(
    conn: Any,
    *,
    person_id: str,
    session_id: str,
    request_id: str,
) -> int:
    rid = _text(request_id, limit=180)
    if not rid or not _table_exists(conn, "memory_event"):
        return 0
    clauses = ["person_id=?", "role='user'", "active=1", "json_extract(refs_json, '$.request_id')=?"]
    params: list[Any] = [person_id, rid]
    if session_id:
        clauses.append("session_id=?")
        params.append(session_id)
    try:
        row = conn.execute(
            f"""
SELECT created_at_ms
FROM memory_event
WHERE {' AND '.join(clauses)}
ORDER BY created_at_ms ASC, id ASC
LIMIT 1
""",
            params,
        ).fetchone()
    except Exception:
        return 0
    if row is None:
        return 0
    try:
        return int(row["created_at_ms"] or 0)
    except Exception:
        return 0


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


def _pending_episode_can_bind(*, pending_episode: Mapping[str, Any], latest_assistant: Mapping[str, Any]) -> bool:
    if not pending_episode:
        return False
    latest_time = _event_time_ms(latest_assistant)
    pending_time = _event_time_ms(pending_episode)
    if latest_time and pending_time and latest_time > pending_time:
        return False
    return True


def _current_message_event_boundary(events: list[dict[str, Any]], *, message: str) -> int:
    compact = _text(message, limit=500)
    if not compact:
        return 0
    for event in reversed(events):
        if str(event.get("role") or "") != "user":
            continue
        if _text(event.get("content") or event.get("content_summary"), limit=500) == compact:
            return _event_time_ms(event)
    return 0


def _latest_user_before_assistant(events: list[dict[str, Any]], *, assistant_event_id: str) -> dict[str, Any]:
    if not assistant_event_id:
        return {}
    latest_user: dict[str, Any] = {}
    for event in events:
        if str(event.get("event_id") or "") == assistant_event_id:
            return latest_user
        if str(event.get("role") or "") == "user":
            latest_user = event
    return {}


def _binding_decision(*, message: str, visible_events: list[dict[str, Any]], pending_episode: Mapping[str, Any]) -> dict[str, Any]:
    compact = _text(message, limit=200)
    assistants = [event for event in visible_events if str(event.get("role") or "") == "assistant"]
    latest_assistant = assistants[-1] if assistants else {}
    specific_match = SPECIFIC_REFERENCE_FOLLOWUP_RE.search(re.sub(r"\s+", "", compact))
    if specific_match and latest_assistant:
        anchor = str(specific_match.group("anchor") or "").strip()
        latest_content = _text(latest_assistant.get("content") or latest_assistant.get("content_summary"), limit=500)
        if anchor and anchor in latest_content:
            previous_user = _latest_user_before_assistant(
                visible_events,
                assistant_event_id=str(latest_assistant.get("event_id") or ""),
            )
            return {
                "binding_kind": "specific_reference_followup",
                "bound_event_id": latest_assistant.get("event_id"),
                "bound_event_kind": latest_assistant.get("event_kind"),
                "bound_content": latest_assistant.get("content_summary") or latest_assistant.get("content"),
                "bound_previous_user_content": previous_user.get("content_summary") or previous_user.get("content"),
                "reference_anchor": anchor,
                "reason_code": "BOUND_RECENT_VISIBLE_ASSISTANT_REFERENCE",
            }
    if CONTEXT_QUESTION_RE.search(compact):
        target = latest_assistant
        if target:
            return {
                "binding_kind": "context_question",
                "bound_event_id": target.get("event_id"),
                "bound_event_kind": target.get("event_kind"),
                "bound_content": target.get("content_summary") or target.get("content"),
                "reason_code": "BOUND_RECENT_VISIBLE_ASSISTANT_EVENT",
            }
        if _pending_episode_can_bind(pending_episode=pending_episode, latest_assistant=latest_assistant):
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
                "bound_content": target.get("content_summary") or target.get("content"),
                "reason_code": "BOUND_RECENT_VISIBLE_EVENT",
            }
    if _pending_episode_can_bind(pending_episode=pending_episode, latest_assistant=latest_assistant):
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
        kind in {"context_question", "continue", "pending_proactive_episode", "specific_reference_followup"}
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
    conn = _conn(ledger)
    now = _now_ms()
    sid = _text(session_id, limit=180)
    pid = _text(person_id, limit=120) or "default"
    message = _text(user_message or current_user_message, limit=500)
    rid = _text(request_id, limit=180)
    selected_context: dict[str, Any] = {
        "current_user_message": message,
    }
    suppressed: list[dict[str, Any]] = []
    reason_codes: list[str] = []

    current_boundary_ms = _current_request_user_event_boundary(
        conn,
        person_id=pid,
        session_id=sid,
        request_id=rid,
    )
    recent_visible_events = _recent_memory_events(
        conn,
        person_id=pid,
        session_id=sid,
        visibility="visible",
        limit=limit,
        before_ms=current_boundary_ms or None,
    )
    if not current_boundary_ms:
        current_boundary_ms = _current_message_event_boundary(recent_visible_events, message=message)
    if current_boundary_ms:
        recent_visible_events = [
            event for event in recent_visible_events if _event_time_ms(event) <= current_boundary_ms
        ]
    selected_context["recent_visible_memory_events"] = recent_visible_events
    selected_context["private_life_event_summary"] = [
        {
            "event_id": item.get("event_id"),
            "event_kind": item.get("event_kind"),
            "content_summary": item.get("content_summary"),
            "created_at_ms": item.get("created_at_ms"),
        }
        for item in _recent_memory_events(conn, person_id=pid, session_id=sid, visibility="private", limit=5)
    ]
    assistant_events = [item for item in recent_visible_events if str(item.get("role") or "") == "assistant"]
    if assistant_events:
        selected_context["previous_visible_assistant_event"] = assistant_events[-1]

    recent_turns: list[dict[str, Any]] = []
    if sid:
        turn_clauses = ["session_id=?"]
        turn_params: list[Any] = [sid]
        if current_boundary_ms:
            turn_clauses.append("ts_ms <= ?")
            turn_params.append(int(current_boundary_ms))
        turn_params.append(max(1, int(limit)))
        rows = conn.execute(
            f"""
SELECT turn_id, request_id, role, content, ts_ms, turn_index, metadata
FROM conversation_turn
WHERE {' AND '.join(turn_clauses)}
ORDER BY ts_ms DESC
LIMIT ?
""",
            turn_params,
        ).fetchall()
        for row in reversed(rows):
            recent_turns.append(
                {
                    "turn_id": row["turn_id"],
                    "request_id": row["request_id"],
                    "role": row["role"],
                    "content": row["content"],
                    "ts_ms": row["ts_ms"],
                    "turn_index": row["turn_index"],
                    "metadata": _mapping(_loads(row["metadata"], {})),
                }
            )
    selected_context["current_session_recent_turns"] = recent_turns
    proactive_turn = _last_life_proactive_turn(conn, session_id=sid, before_ms=current_boundary_ms or None)
    if proactive_turn:
        selected_context["previous_life_proactive_turn"] = proactive_turn
    pending_proactive_episode = _last_pending_proactive_episode(conn, person_id=pid, now_ms=now)
    if pending_proactive_episode:
        selected_context["pending_life_proactive_episode"] = pending_proactive_episode
        if not proactive_turn:
            selected_context["previous_life_proactive_turn"] = {
                "turn_id": pending_proactive_episode.get("episode_id"),
                "request_id": pending_proactive_episode.get("source_outbox_id") or pending_proactive_episode.get("episode_id"),
                "role": "assistant",
                "content": pending_proactive_episode.get("message_text"),
                "ts_ms": pending_proactive_episode.get("sent_at_ms"),
                "turn_index": None,
                "metadata": {
                    "source_kind": "life_proactive",
                    "life_source_kind": _mapping(pending_proactive_episode.get("metadata")).get("life_source_kind"),
                    "motive": pending_proactive_episode.get("motive"),
                    "proactive_episode_id": pending_proactive_episode.get("episode_id"),
                },
            }
    binding_decision = _binding_decision(message=message, visible_events=recent_visible_events, pending_episode=pending_proactive_episode)
    conversation_event_resolution: dict[str, Any] = {}
    try:
        from host_brain.app.conversation_event_resolver import resolve_conversation_event_action  # noqa: PLC0415

        conversation_event_resolution = resolve_conversation_event_action(
            ledger=conn,
            person_id=pid,
            session_id=sid,
            current_user_message=message,
            now_ms=now,
            before_ms=current_boundary_ms or None,
        )
    except Exception as exc:
        conversation_event_resolution = {
            "schema_version": "Natsume.conversation_event_resolution.v1",
            "status": "UNRESOLVED",
            "reason_code": "CONVERSATION_EVENT_RESOLVER_ERROR",
            "error": str(exc)[:200],
        }
    selected_context["conversation_event_resolution"] = conversation_event_resolution
    if str(conversation_event_resolution.get("status") or "") == "RESOLVED":
        reason = str(conversation_event_resolution.get("reason_code") or "CONVERSATION_EVENT_RESOLVED")
        reason_codes.append(reason)

    belief = {}
    if sid and _table_exists(conn, "conversation_belief_state"):
        row = conn.execute(
            """
SELECT belief_json, updated_at_ms
FROM conversation_belief_state
WHERE session_id=? AND person_id=?
LIMIT 1
""",
            (sid, pid),
        ).fetchone()
        if row:
            belief = _mapping(_loads(row["belief_json"], {}))
            belief["_updated_at_ms"] = int(row["updated_at_ms"] or 0)
            if _belief_can_drive_reply(belief, now_ms=now):
                selected_context["short_term_belief"] = belief
            else:
                suppressed.append({"layer": "short_term_belief", "reason_code": "BELIEF_EXPIRED_OR_LOW_CONFIDENCE", "item": belief})
                reason_codes.append("BELIEF_EXPIRED_OR_LOW_CONFIDENCE")

    dialogue_frame: dict[str, Any] = {}
    try:
        from host_brain.app.context.dialogue_frame import build_dialogue_frame  # noqa: PLC0415

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

    pref_rows = conn.execute(
        """
SELECT domain, profile_json, updated_at_ms
FROM user_preference_profile
WHERE person_id=?
ORDER BY updated_at_ms DESC
LIMIT 5
""",
        (pid,),
    ).fetchall() if _table_exists(conn, "user_preference_profile") else []
    selected_context["explicit_user_preference_profile"] = [
        {"domain": row["domain"], "profile": _loads(row["profile_json"], {}), "updated_at_ms": row["updated_at_ms"]}
        for row in pref_rows
    ]

    delivery_rows = conn.execute(
        """
SELECT delivery_id, domain, items_json, created_at_ms
FROM recommendation_delivery_log
WHERE active=1
  AND (
    person_id=?
    OR (? != '' AND session_id=?)
    OR (? != '' AND conversation_id=?)
  )
ORDER BY created_at_ms DESC
LIMIT 3
""",
        (pid, sid, sid, sid, sid),
    ).fetchall() if _table_exists(conn, "recommendation_delivery_log") else []
    selected_context["bound_recommendation_feedback"] = [
        {"delivery_id": row["delivery_id"], "domain": row["domain"], "items": _loads(row["items_json"], []), "created_at_ms": row["created_at_ms"]}
        for row in delivery_rows
    ]

    if _binding_suppresses_long_term_evidence(binding_decision) or bool(_mapping(dialogue_frame).get("suppress_long_term_memory")):
        memory_rows = []
        suppressed.append(
            {
                "layer": "long_term_memory_evidence",
                "reason_code": "SUPPRESSED_BY_DIALOGUE_FRAME" if bool(_mapping(dialogue_frame).get("suppress_long_term_memory")) else "SUPPRESSED_BY_RECENT_VISIBLE_BINDING",
                "binding": binding_decision,
                "dialogue_frame": dialogue_frame,
            }
        )
        reason_codes.append("SUPPRESSED_BY_DIALOGUE_FRAME" if bool(_mapping(dialogue_frame).get("suppress_long_term_memory")) else "SUPPRESSED_BY_RECENT_VISIBLE_BINDING")
    else:
        memory_rows = conn.execute(
            """
SELECT semantic_id, summary, confidence, tags, ts_ms
FROM semantic_memory
WHERE deleted_at IS NULL
ORDER BY ts_ms DESC
LIMIT 8
"""
        ).fetchall() if _table_exists(conn, "semantic_memory") else []
    memory_evidence = []
    for row in memory_rows:
        summary = _text(row["summary"], limit=500)
        if UNRESOLVED_PREFERENCE_RE.search(summary):
            suppressed.append({"layer": "long_term_memory_evidence", "reason_code": "UNRESOLVED_PREFERENCE_MEMORY", "item": summary})
            reason_codes.append("UNRESOLVED_PREFERENCE_MEMORY")
            continue
        memory_evidence.append(
            {
                "semantic_id": row["semantic_id"],
                "summary": summary,
                "confidence": row["confidence"],
                "tags": _loads(row["tags"], []),
                "ts_ms": row["ts_ms"],
            }
        )
    selected_context["long_term_memory_evidence"] = memory_evidence

    lesson_rows = conn.execute(
        """
SELECT lesson_id, domain, pattern, lesson, success_count, failure_count, confidence, updated_at_ms
FROM conversation_experience_lesson
WHERE person_id=? AND active=1
ORDER BY updated_at_ms DESC
LIMIT 8
""",
        (pid,),
    ).fetchall() if _table_exists(conn, "conversation_experience_lesson") else []
    lessons = []
    for row in lesson_rows:
        success = int(row["success_count"] or 0)
        failure = int(row["failure_count"] or 0)
        confidence = float(row["confidence"] or 0.0)
        lesson = {
            "lesson_id": row["lesson_id"],
            "domain": row["domain"],
            "pattern": row["pattern"],
            "lesson": row["lesson"],
            "success_count": success,
            "failure_count": failure,
            "confidence": confidence,
            "updated_at_ms": row["updated_at_ms"],
        }
        if failure > success and confidence < 0.85:
            suppressed.append({"layer": "conversation_experience_lesson", "reason_code": "FAILED_LESSON_DISABLED", "item": lesson})
            reason_codes.append("FAILED_LESSON_DISABLED")
            continue
        lessons.append(lesson)
    selected_context["conversation_experience_lesson"] = lessons

    state_row = conn.execute("SELECT state_json FROM digital_life_state WHERE person_id=? LIMIT 1", (pid,)).fetchone() if _table_exists(conn, "digital_life_state") else None
    selected_context["digital_life_state"] = _loads(state_row["state_json"], {}) if state_row else {}

    current_turn_binding = dict(binding_decision)

    return {
        "selected_context": selected_context,
        "selected_events": {
            "recent_visible": recent_visible_events,
            "previous_visible_assistant": selected_context.get("previous_visible_assistant_event", {}),
            "pending_life_proactive_episode": pending_proactive_episode,
        },
        "binding_decision": binding_decision,
        "conversation_event_resolution": selected_context.get("conversation_event_resolution", {}),
        "layer_order": list(LAYER_ORDER),
        "suppressed_items": suppressed,
        "reason_codes": sorted(set(reason_codes)),
        "current_turn_binding": current_turn_binding,
        "memory_evidence": memory_evidence,
        "projection_status": {
            "recent_visible": [item.get("projection_status", {}) for item in recent_visible_events],
        },
    }


def _table_exists(conn: Any, name: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)).fetchone()
    return bool(row)


def memory_health(*, ledger: Any, person_id: str = "default") -> dict[str, Any]:
    conn = _conn(ledger)
    now = _now_ms()
    pid = _text(person_id, limit=120) or "default"
    bad_unresolved_preferences = conn.execute(
        """
SELECT COUNT(*)
FROM semantic_memory
WHERE deleted_at IS NULL
  AND (summary LIKE '%这本%喜欢%' OR summary LIKE '%那本%喜欢%' OR summary LIKE '%这个%不错%' OR summary LIKE '%那个%不错%')
"""
    ).fetchone()[0] if _table_exists(conn, "semantic_memory") else 0
    active_failed_lessons = conn.execute(
        """
SELECT COUNT(*)
FROM conversation_experience_lesson
WHERE active=1 AND failure_count > success_count AND confidence < 0.85
"""
    ).fetchone()[0] if _table_exists(conn, "conversation_experience_lesson") else 0
    cross_session_belief_leaks = conn.execute(
        """
SELECT COUNT(*)
FROM conversation_belief_state
WHERE person_id=?
  AND json_extract(belief_json, '$.can_drive_reply') IS NOT 0
  AND json_extract(belief_json, '$.scope') != 'session'
""",
        (pid,),
    ).fetchone()[0] if _table_exists(conn, "conversation_belief_state") else 0
    stale_active_topics = conn.execute(
        """
SELECT COUNT(*)
FROM conversation_belief_state
WHERE person_id=?
  AND json_extract(belief_json, '$.can_drive_reply') IS NOT 0
  AND COALESCE(json_extract(belief_json, '$.expires_at_ms'), updated_at_ms + ?) < ?
""",
        (pid, BELIEF_TTL_MS, now),
    ).fetchone()[0] if _table_exists(conn, "conversation_belief_state") else 0
    proactive_turn_write_failures = conn.execute(
        """
SELECT COUNT(*)
FROM proactive_life_outbox p
WHERE p.status='SENT'
  AND NOT EXISTS (
    SELECT 1 FROM conversation_turn t
    WHERE t.request_id=p.outbox_id AND t.role='assistant'
  )
"""
    ).fetchone()[0] if _table_exists(conn, "proactive_life_outbox") else 0
    conversation_turn_spine_gaps = 0
    semantic_memory_without_spine_source = conn.execute(
        """
SELECT COUNT(*)
FROM semantic_memory
WHERE deleted_at IS NULL
  AND source_memory_ids NOT LIKE '%lifeexp_%'
  AND source_memory_ids NOT LIKE '%turn::%'
  AND source_memory_ids NOT LIKE '%conversation_turn%'
  AND source_memory_ids NOT LIKE '%life_episode::%'
  AND source_memory_ids NOT LIKE '%life_memory_collection::%'
"""
    ).fetchone()[0] if _table_exists(conn, "semantic_memory") else 0
    health = {
        "bad_unresolved_preferences": int(bad_unresolved_preferences),
        "active_failed_lessons": int(active_failed_lessons),
        "cross_session_belief_leaks": int(cross_session_belief_leaks),
        "stale_active_topics": int(stale_active_topics),
        "proactive_turn_write_failures": int(proactive_turn_write_failures),
        "conversation_turn_spine_gaps": int(conversation_turn_spine_gaps),
        "semantic_memory_without_spine_source": int(semantic_memory_without_spine_source),
    }
    try:
        from host_brain.app.memory_event_store import memory_event_health  # noqa: PLC0415

        event_health = memory_event_health(ledger=ledger, person_id=pid)
        health.update(event_health)
        health["conversation_turn_spine_gaps"] = int(event_health.get("visible_turn_projection_gaps", 0))
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


def run_memory_sanitize(*, ledger: Any, person_id: str = "default", dry_run: bool = False) -> dict[str, Any]:
    conn = _conn(ledger)
    pid = _text(person_id, limit=120) or "default"
    now = _now_ms()
    summary = {
        "belief_deleted": 0,
        "lessons_disabled": 0,
        "semantic_soft_deleted": 0,
        "life_memory_soft_deleted": 0,
        "proactive_outbox_marked": 0,
        "proactive_turns_backfilled": 0,
        "reason_counts": {},
        "dry_run": bool(dry_run),
    }

    def bump(reason: str, count: int = 1) -> None:
        if count:
            summary["reason_counts"][reason] = int(summary["reason_counts"].get(reason, 0)) + int(count)

    if _table_exists(conn, "conversation_belief_state"):
        rows = conn.execute(
            """
SELECT session_id, person_id, belief_json, updated_at_ms
FROM conversation_belief_state
WHERE person_id=?
""",
            (pid,),
        ).fetchall()
        for row in rows:
            belief = _mapping(_loads(row["belief_json"], {}))
            sid = str(row["session_id"] or "")
            reasons = []
            if sid.startswith("codex_") or sid.startswith("asr_req_"):
                reasons.append("TEST_OR_ASR_SESSION_BELIEF")
            if not _belief_can_drive_reply(belief, now_ms=now):
                reasons.append("STALE_OR_LOW_CONFIDENCE_BELIEF")
            if reasons:
                belief.update({"can_drive_reply": False, "suppressed_reason": ",".join(sorted(set(reasons))), "suppressed_at_ms": now})
                summary["belief_deleted"] += 1
                bump("belief:" + reasons[0])
                if not dry_run:
                    conn.execute(
                        "UPDATE conversation_belief_state SET belief_json=?, updated_at_ms=? WHERE session_id=? AND person_id=?",
                        (_dumps(belief), now, row["session_id"], row["person_id"]),
                    )

    if _table_exists(conn, "conversation_experience_lesson"):
        if dry_run:
            count = conn.execute(
                "SELECT COUNT(*) FROM conversation_experience_lesson WHERE active=1 AND failure_count > success_count AND confidence < 0.85"
            ).fetchone()[0]
        else:
            cur = conn.execute(
                "UPDATE conversation_experience_lesson SET active=0 WHERE active=1 AND failure_count > success_count AND confidence < 0.85"
            )
            count = int(cur.rowcount or 0)
        summary["lessons_disabled"] += int(count)
        bump("lesson:failed_more_than_success", int(count))

    if _table_exists(conn, "semantic_memory"):
        semantic_where = """
deleted_at IS NULL AND (
  summary LIKE '%这本%喜欢%' OR summary LIKE '%那本%喜欢%' OR summary LIKE '%这个%不错%' OR summary LIKE '%那个%不错%'
  OR summary LIKE '稳定事实：skill | %FAILED%'
  OR summary LIKE '%测试代号%'
)
"""
        if dry_run:
            count = conn.execute(f"SELECT COUNT(*) FROM semantic_memory WHERE {semantic_where}").fetchone()[0]
        else:
            cur = conn.execute(f"UPDATE semantic_memory SET deleted_at=? WHERE {semantic_where}", (now,))
            count = int(cur.rowcount or 0)
        summary["semantic_soft_deleted"] += int(count)
        bump("semantic:dirty_or_unresolved", int(count))

    if _table_exists(conn, "life_memory_collection"):
        if dry_run:
            count = conn.execute(
                "SELECT COUNT(*) FROM life_memory_collection WHERE retention_state NOT LIKE 'suppressed%' AND (session_id LIKE 'codex_%' OR title LIKE '%测试%')"
            ).fetchone()[0]
        else:
            cur = conn.execute(
                "UPDATE life_memory_collection SET retention_state='suppressed_memory_sanitize', updated_ts_ms=? WHERE retention_state NOT LIKE 'suppressed%' AND (session_id LIKE 'codex_%' OR title LIKE '%测试%')",
                (now,),
            )
            count = int(cur.rowcount or 0)
        summary["life_memory_soft_deleted"] += int(count)
        bump("life_memory:test_collection", int(count))

    if _table_exists(conn, "proactive_life_outbox"):
        rows = conn.execute("SELECT outbox_id, message_json, gate_json FROM proactive_life_outbox ORDER BY created_at_ms DESC LIMIT 500").fetchall()
        marked = 0
        for row in rows:
            msg = _mapping(_loads(row["message_json"], {}))
            content = _text(msg.get("content"), limit=500)
            if not content or not UNSUPPORTED_PROACTIVE_ACTION_RE.search(content):
                continue
            gate = _mapping(_loads(row["gate_json"], {}))
            gate["content_quality"] = {"status": "REJECTED_AFTER_AUDIT", "reason_code": "UNSUPPORTED_PROACTIVE_ACTION"}
            marked += 1
            if not dry_run:
                conn.execute("UPDATE proactive_life_outbox SET gate_json=? WHERE outbox_id=?", (_dumps(gate), row["outbox_id"]))
        summary["proactive_outbox_marked"] = marked
        bump("proactive:unsupported_action", marked)

        if _table_exists(conn, "conversation_turn"):
            backfill_rows = conn.execute(
                """
SELECT outbox_id, person_id, source_kind, motive, pressure, target_json, message_json, result_json, created_at_ms
FROM proactive_life_outbox p
WHERE p.person_id=? AND p.status='SENT'
  AND NOT EXISTS (
    SELECT 1 FROM conversation_turn t
    WHERE t.request_id=p.outbox_id AND t.role='assistant'
  )
ORDER BY p.created_at_ms ASC
LIMIT 500
""",
                (pid,),
            ).fetchall()
            backfilled = 0
            for row in backfill_rows:
                target = _mapping(_loads(row["target_json"], {}))
                message = _mapping(_loads(row["message_json"], {}))
                result = _mapping(_loads(row["result_json"], {}))
                content = _text(
                    message.get("content") or message.get("text") or message.get("narrative") or result.get("content") or result.get("text"),
                    limit=2000,
                )
                conversation_id = _text(
                    target.get("conversation_id") or target.get("reply_target") or target.get("target_id") or target.get("id"),
                    limit=160,
                )
                platform = _text(target.get("platform") or target.get("reply_channel") or target.get("kind"), limit=80)
                session_id = _text(target.get("session_id"), limit=180)
                if not session_id and conversation_id:
                    session_id = conversation_id if conversation_id.startswith(("qq_", "wx_", "telegram_", "discord_")) else (f"qq_dm_{conversation_id}" if platform == "qq" else conversation_id)
                if not content or not session_id:
                    continue
                turn_index_row = conn.execute(
                    "SELECT COALESCE(MAX(turn_index), 0) + 1 FROM conversation_turn WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                turn_index = int(turn_index_row[0] or 1)
                metadata = {
                    "source_kind": "life_proactive",
                    "backfilled": True,
                    "life_source_kind": row["source_kind"],
                    "motive": row["motive"],
                    "pressure": float(row["pressure"] or 0.0),
                    "proactive_outbox_id": row["outbox_id"],
                    "reply_channel": platform,
                    "reply_target": conversation_id,
                }
                backfilled += 1
                if not dry_run:
                    conn.execute(
                        """
INSERT OR IGNORE INTO conversation_turn(
    turn_id, session_id, person_id, request_id, role, content,
    ts_ms, turn_index, lang, token_count, corrupted, metadata, created_at_ms
) VALUES(?, ?, ?, ?, 'assistant', ?, ?, ?, 'zh', ?, 0, ?, ?)
""",
                        (
                            f"turn::life_proactive_backfill::{row['outbox_id']}",
                            session_id,
                            row["person_id"] or pid,
                            row["outbox_id"],
                            content,
                            int(row["created_at_ms"] or now),
                            turn_index,
                            max(1, len(content) // 2),
                            _dumps(metadata),
                            now,
                        ),
                    )
            summary["proactive_turns_backfilled"] = backfilled
            bump("proactive:turn_backfilled", backfilled)

    if not dry_run:
        conn.execute(
            """
CREATE TABLE IF NOT EXISTS memory_sanitize_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id TEXT NOT NULL DEFAULT 'default',
    summary_json TEXT NOT NULL DEFAULT '{}',
    created_at_ms INTEGER NOT NULL
)
"""
        )
        conn.execute(
            "INSERT INTO memory_sanitize_audit(person_id, summary_json, created_at_ms) VALUES(?, ?, ?)",
            (pid, _dumps(summary), now),
        )
        conn.commit()
    return summary


__all__ = [
    "BELIEF_TTL_MS",
    "LAYER_ORDER",
    "build_memory_context_debug",
    "memory_health",
    "run_memory_sanitize",
]
