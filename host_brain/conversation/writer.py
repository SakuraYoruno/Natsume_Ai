from __future__ import annotations

import logging
import time
from typing import Any

from host_brain.app.dependencies import get_conversation_turn_store, get_session_topic_state_store
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
    with soft_fail("conversation.record_user_turn", fallback=None):
        turn = get_conversation_turn_store().append_generated(
            session_id=session_id,
            person_id=person_id,
            request_id=request_id,
            role="user",
            content=normalized_content,
            ts_ms=int(ts_ms or int(time.time() * 1000)),
            lang=lang,
            metadata=metadata,
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
) -> None:
    if not str(session_id or "").strip() or not str(content or "").strip():
        return
    normalized_content = str(content)
    with soft_fail("conversation.record_assistant_turn", fallback=None):
        turn = get_conversation_turn_store().append_generated(
            session_id=session_id,
            person_id=person_id,
            request_id=request_id,
            role="assistant",
            content=normalized_content,
            ts_ms=int(ts_ms or int(time.time() * 1000)),
            lang=lang,
            metadata=metadata,
        )
        _logger.info(
            "[%s] conversation_record role=assistant turn_index=%s session_id=%s content_chars=%s",
            request_id,
            int(turn.turn_index),
            str(session_id or "").strip(),
            len(normalized_content),
        )
        try:
            tracked_topics = get_session_topic_state_store().upsert_answered_topics(
                session_id=session_id,
                person_id=person_id,
                request_id=request_id,
                turn_index=int(turn.turn_index),
                assistant_text=normalized_content,
                metadata=metadata,
            )
            if tracked_topics:
                _logger.info(
                    "[%s] conversation_topic_state session_id=%s topics=%s",
                    request_id,
                    str(session_id or "").strip(),
                    ",".join(tracked_topics),
                )
        except Exception as exc:
            _logger.debug("[%s] conversation_topic_state upsert failed: %s", request_id, exc)
