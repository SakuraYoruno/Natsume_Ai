from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from host_brain.app.dependencies import (
    get_conversation_turn_store,
    get_session_tactic_ledger_store,
    get_session_topic_ledger_store,
)
from host_brain.app.context.soul_runtime import get_compiled_soul
from host_brain.app.reply_envelope import can_persist
from host_brain.core.soft_fail import soft_fail

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
) -> None:
    merged_metadata = dict(metadata or {})
    has_multimodal_metadata = bool(
        merged_metadata.get("attachments")
        or merged_metadata.get("multimodal_observations")
        or merged_metadata.get("attachment_summary")
    )
    if not str(session_id or "").strip() or (not str(content or "").strip() and not has_multimodal_metadata):
        return
    normalized_content = str(content)
    with soft_fail("conversation.record_user_turn.soul_metadata", fallback=None):
        merged_metadata.setdefault("soul_id", str(getattr(get_compiled_soul(), "soul_id", "") or "").strip())
    with soft_fail("conversation.record_user_turn", fallback=None):
        turn = get_conversation_turn_store().append_generated(
            session_id=session_id,
            person_id=person_id,
            request_id=request_id,
            role="user",
            content=normalized_content,
            ts_ms=int(ts_ms or int(time.time() * 1000)),
            lang=lang,
            metadata=merged_metadata or None,
        )
        _logger.info(
            "[%s] conversation_record role=user turn_index=%s session_id=%s content_chars=%s",
            request_id,
            int(turn.turn_index),
            str(session_id or "").strip(),
            len(normalized_content),
        )


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
) -> None:
    plan_payload = dict(reply_plan or {}) if isinstance(reply_plan, Mapping) else {}
    merged_metadata = dict(metadata or {})
    envelope_payload = dict(merged_metadata.get("reply_envelope") or {}) if isinstance(merged_metadata.get("reply_envelope"), Mapping) else {}
    content_text = str(content or "").strip()
    if not content_text and envelope_payload and can_persist(envelope_payload):
        response_form = str(envelope_payload.get("response_form") or plan_payload.get("response_form") or "media").strip()
        reaction_id = str(envelope_payload.get("reaction_id") or "").strip()
        content_text = f"[{response_form or 'media'}:{reaction_id or 'final_reply'}]"
    if not str(session_id or "").strip() or not content_text:
        return
    normalized_content = content_text
    if envelope_payload and not can_persist(envelope_payload):
        _logger.debug("[%s] skip assistant turn: non-final reply envelope kind=%s", request_id, envelope_payload.get("kind"))
        return
    message_kind = str(merged_metadata.get("message_kind") or "").strip().lower()
    if message_kind and message_kind != "final_reply":
        _logger.debug("[%s] skip assistant turn: message_kind=%s", request_id, message_kind)
        return
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
    with soft_fail("conversation.record_assistant_turn", fallback=None):
        turn = get_conversation_turn_store().append_generated(
            session_id=session_id,
            person_id=person_id,
            request_id=request_id,
            role="assistant",
            content=normalized_content,
            ts_ms=int(ts_ms or int(time.time() * 1000)),
            lang=lang,
            metadata=merged_metadata or None,
        )
        _logger.info(
            "[%s] conversation_record role=assistant turn_index=%s session_id=%s content_chars=%s",
            request_id,
            int(turn.turn_index),
            str(session_id or "").strip(),
            len(normalized_content),
        )
        try:
            tracked_topics = get_session_topic_ledger_store().record_reply_plan(
                session_id=session_id,
                person_id=person_id,
                request_id=request_id,
                turn_index=int(turn.turn_index),
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
