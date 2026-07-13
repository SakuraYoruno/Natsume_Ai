from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from host_brain.composition.process_services import (
    get_ledger,
    get_session_tactic_ledger_store,
    get_session_topic_ledger_store,
)
from host_brain.persona.context.soul_runtime import get_compiled_soul
from host_brain.app.reply_envelope import can_persist
from host_brain.core.soft_fail import soft_fail
from host_brain.conversation.memory_spine import append_visible_conversation_turn
from host_brain.composition.life import PROCESS_LIFE_PROVIDER

_logger = logging.getLogger("Natsume.conversation.writer")


def record_user_turn(
    *,
    request_id: str,
    session_id: str,
    person_id: str | None,
    content: str,
    ts_ms: int,
    lang: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged_metadata = dict(metadata or {})
    has_multimodal_metadata = bool(
        merged_metadata.get("attachments")
        or merged_metadata.get("multimodal_observations")
        or merged_metadata.get("attachment_summary")
    )
    if not str(session_id or "").strip() or (not str(content or "").strip() and not has_multimodal_metadata):
        return {"status": "SKIPPED", "reason_code": "EMPTY_USER_TURN"}
    normalized_content = str(content)
    with soft_fail("conversation.record_user_turn.soul_metadata", fallback=None):
        merged_metadata.setdefault("soul_id", str(getattr(get_compiled_soul(), "soul_id", "") or "").strip())
    resolved_ts_ms = int(ts_ms or int(time.time() * 1000))
    with soft_fail("conversation.record_user_turn", fallback=None):
        life = PROCESS_LIFE_PROVIDER.require()
        result = append_visible_conversation_turn(
            ledger=get_ledger(),
            life_service=life.command_service,
            life_reader=life.snapshot_reader,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            role="user",
            content=normalized_content,
            ts_ms=resolved_ts_ms,
            lang=lang,
            metadata=merged_metadata or None,
        )
        if str(result.get("status") or "") != "OK":
            return result
        turn = dict(result.get("turn") or {})
        _logger.info(
            "[%s] memory_event_record role=user event_id=%s turn_index=%s session_id=%s content_chars=%s",
            request_id,
            str(result.get("memory_event_id") or ""),
            int(turn.get("turn_index") or 0),
            str(session_id or "").strip(),
            len(normalized_content),
        )
        with soft_fail("conversation.record_user_turn.preference_spine", fallback=None):
            from host_brain.app.recommendation_feedback import record_user_preference_signal_if_present  # noqa: PLC0415

            preference_record = record_user_preference_signal_if_present(
                session_id=session_id,
                person_id=person_id,
                user_message=normalized_content,
                payload={
                    **merged_metadata,
                    "request_id": request_id,
                    "session_id": session_id,
                    "conversation_id": merged_metadata.get("conversation_id") or merged_metadata.get("reply_target") or session_id,
                    "platform": merged_metadata.get("platform") or merged_metadata.get("reply_channel") or "",
                },
                ts_ms=resolved_ts_ms,
            )
            if preference_record:
                _logger.info(
                    "[%s] preference_spine events=%s domain=%s target=%s",
                    request_id,
                    int(preference_record.get("event_count") or 0),
                    str(preference_record.get("domain") or ""),
                    str(preference_record.get("target_label") or ""),
                )
        return result
    return {"status": "DEGRADED", "reason_code": "SOFT_FAIL_USER_TURN"}


def record_assistant_turn(
    *,
    request_id: str,
    session_id: str,
    person_id: str | None,
    content: str,
    ts_ms: int,
    lang: str,
    metadata: dict[str, Any] | None = None,
    reply_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    plan_payload = dict(reply_plan or {}) if isinstance(reply_plan, Mapping) else {}
    merged_metadata = dict(metadata or {})
    envelope_payload = dict(merged_metadata.get("reply_envelope") or {}) if isinstance(merged_metadata.get("reply_envelope"), Mapping) else {}
    content_text = str(content or "").strip()
    if not content_text and envelope_payload and can_persist(envelope_payload):
        response_form = str(envelope_payload.get("response_form") or plan_payload.get("response_form") or "media").strip()
        reaction_id = str(envelope_payload.get("reaction_id") or "").strip()
        content_text = f"[{response_form or 'media'}:{reaction_id or 'final_reply'}]"
    if not str(session_id or "").strip() or not content_text:
        return {"status": "SKIPPED", "reason_code": "EMPTY_ASSISTANT_TURN"}
    normalized_content = content_text
    if envelope_payload and not can_persist(envelope_payload):
        _logger.debug("[%s] skip assistant turn: non-final reply envelope kind=%s", request_id, envelope_payload.get("kind"))
        return {"status": "SKIPPED", "reason_code": "NON_FINAL_ENVELOPE"}
    message_kind = str(merged_metadata.get("message_kind") or "").strip().lower()
    if message_kind and message_kind != "final_reply":
        _logger.debug("[%s] skip assistant turn: message_kind=%s", request_id, message_kind)
        return {"status": "SKIPPED", "reason_code": "NON_FINAL_MESSAGE_KIND"}
    with soft_fail("conversation.record_assistant_turn.soul_metadata", fallback=None):
        merged_metadata.setdefault("soul_id", str(getattr(get_compiled_soul(), "soul_id", "") or "").strip())
    for metadata_key in (
        "primary_topic",
        "response_goal",
        "tactic_family",
        "tactic_variant",
        "trajectory_stage",
        "response_form",
        "response_form_reason",
    ):
        value = str(plan_payload.get(metadata_key) or "").strip()
        if value:
            merged_metadata.setdefault(metadata_key, value)
    resolved_ts_ms = int(ts_ms or int(time.time() * 1000))
    with soft_fail("conversation.record_assistant_turn", fallback=None):
        life = PROCESS_LIFE_PROVIDER.require()
        result = append_visible_conversation_turn(
            ledger=get_ledger(),
            life_service=life.command_service,
            life_reader=life.snapshot_reader,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            role="assistant",
            content=normalized_content,
            ts_ms=resolved_ts_ms,
            lang=lang,
            metadata=merged_metadata or None,
        )
        turn = dict(result.get("turn") or {})
        _logger.info(
            "[%s] memory_event_record role=assistant event_id=%s turn_index=%s session_id=%s content_chars=%s",
            request_id,
            str(result.get("memory_event_id") or ""),
            int(turn.get("turn_index") or 0),
            str(session_id or "").strip(),
            len(normalized_content),
        )
        try:
            tracked_topics = get_session_topic_ledger_store().record_reply_plan(
                session_id=session_id,
                person_id=person_id,
                request_id=request_id,
                turn_index=int(turn.get("turn_index") or 0),
                primary_topic=str(plan_payload.get("primary_topic") or "").strip(),
                answer_style=str(plan_payload.get("response_goal") or plan_payload.get("tactic_family") or "").strip(),
                confidence=float(plan_payload.get("plan_confidence") or 0.0),
                metadata={
                    **merged_metadata,
                    "interaction_move": str(plan_payload.get("interaction_move") or "").strip(),
                    "followup_kind": str(plan_payload.get("followup_kind") or "").strip(),
                    "restate_level": str(plan_payload.get("restate_level") or "").strip(),
                    "tactic_family": str(plan_payload.get("tactic_family") or "").strip(),
                    "tactic_variant": str(plan_payload.get("tactic_variant") or "").strip(),
                    "trajectory_stage": str(plan_payload.get("trajectory_stage") or "").strip(),
                },
            )
            if tracked_topics:
                _logger.info(
                    "[%s] conversation_topic_ledger session_id=%s topics=%s",
                    request_id,
                    str(session_id or "").strip(),
                    ",".join(tracked_topics),
                )
            get_session_tactic_ledger_store().record_reply_plan(
                session_id=session_id,
                request_id=request_id,
                topic_key=str(plan_payload.get("primary_topic") or "").strip(),
                interaction_move=str(plan_payload.get("interaction_move") or "").strip(),
                tactic_family=str(plan_payload.get("tactic_family") or "").strip(),
                tactic_variant=str(plan_payload.get("tactic_variant") or "").strip(),
                trajectory_stage=str(plan_payload.get("trajectory_stage") or "").strip(),
                ts_ms=int(ts_ms or int(time.time() * 1000)),
            )
        except Exception as exc:
            _logger.debug("[%s] conversation_topic_ledger record failed: %s", request_id, exc)
        return result
    return {"status": "DEGRADED", "reason_code": "SOFT_FAIL_ASSISTANT_TURN"}
