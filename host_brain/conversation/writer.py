from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from host_brain.app.dependencies import (
    get_conversation_turn_store,
    get_reply_surface_memory_store,
    get_session_tactic_ledger_store,
    get_session_topic_ledger_store,
)
from host_brain.app.context.soul_runtime import get_compiled_soul
from host_brain.persona.reply_surface_memory import (
    build_assistant_followup_context_fingerprint,
    build_reply_surface_memory_key,
)
from host_brain.persona.fallback_policy import is_surface_cacheable_reply
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
    if not str(session_id or "").strip() or not str(content or "").strip():
        return
    normalized_content = str(content)
    merged_metadata = dict(metadata or {})
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
    if not str(session_id or "").strip() or not str(content or "").strip():
        return
    normalized_content = str(content)
    plan_payload = dict(reply_plan or {}) if isinstance(reply_plan, Mapping) else {}
    merged_metadata = dict(metadata or {})
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
    followup_context_fingerprint = ""
    if str(plan_payload.get("primary_topic") or "").strip() == "assistant_reply_followup":
        followup_context_fingerprint = build_assistant_followup_context_fingerprint(
            reply_contract=str(plan_payload.get("reply_contract") or "").strip()
            or str((plan_payload.get("contract") or {}).get("text") if isinstance(plan_payload.get("contract"), Mapping) else "").strip()
        )
        if followup_context_fingerprint:
            merged_metadata.setdefault("reply_surface_context_fingerprint", followup_context_fingerprint)
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
        try:
            primary_topic = str(plan_payload.get("primary_topic") or "").strip()
            tactic_family = str(plan_payload.get("tactic_family") or "").strip()
            tactic_variant = str(plan_payload.get("tactic_variant") or "").strip()
            response_form = str(plan_payload.get("response_form") or "").strip()
            response_form_reason = str(plan_payload.get("response_form_reason") or "").strip()
            provider_name = str(merged_metadata.get("provider_name") or "").strip()
            model_id = str(merged_metadata.get("model_id") or "").strip()
            fallback_used = bool(merged_metadata.get("fallback"))
            message_kind = str(merged_metadata.get("message_kind") or "").strip().lower()
            allowed_topics = {
                "greeting",
                "presence_ping",
                "light_ack",
                "playful_ping",
                "identity",
                "relationship_boundary",
                "motivation",
                "help_commitment",
                "rejection",
                "emoji_social_reply",
                "emoji_social_followup",
                "assistant_reply_followup",
            }
            cacheable_response_forms = {"text", "text_plus_emoji", "text_plus_sticker"}
            if (
                primary_topic in allowed_topics
                and response_form in cacheable_response_forms
                and (primary_topic != "assistant_reply_followup" or bool(followup_context_fingerprint))
                and not fallback_used
                and message_kind in {"", "final_reply"}
                and model_id != "deterministic-plan-renderer"
                and provider_name != "host"
                and len(normalized_content.strip()) >= 4
                and is_surface_cacheable_reply(text=normalized_content, metadata=merged_metadata)
            ):
                cache_key = build_reply_surface_memory_key(
                    soul_id=str(merged_metadata.get("soul_id") or "").strip(),
                    primary_topic=primary_topic,
                    tactic_family=tactic_family,
                    tactic_variant=tactic_variant,
                    response_form_reason=response_form_reason,
                    render_target_lang=lang,
                    context_fingerprint=followup_context_fingerprint,
                )
                get_reply_surface_memory_store().remember(
                    key=cache_key,
                    text=normalized_content,
                    ts_ms=int(ts_ms or int(time.time() * 1000)),
                    request_id=request_id,
                    session_id=session_id,
                )
        except Exception as exc:
            _logger.debug("[%s] conversation_reply_surface_memory remember failed: %s", request_id, exc)
