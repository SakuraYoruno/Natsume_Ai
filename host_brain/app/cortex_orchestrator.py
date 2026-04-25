"""Cortex orchestrator entrypoint.

This module is the canonical runtime entry for the Cortex family.
"""

from __future__ import annotations

import time
from typing import Any, AsyncGenerator


async def cortex_orchestrator_sse_generator(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    governor_result: dict[str, Any],
    intent_id: str | None,
    metrics: Any,
) -> AsyncGenerator[str, None]:
    from host_brain.app.agent_runtime_shared import _cortex_orchestrator_sse_generator_impl  # noqa: PLC0415
    from host_brain.conversation.writer import record_user_turn  # noqa: PLC0415

    record_user_turn(
        request_id=request_id,
        session_id=session_id,
        person_id=getattr(request_body, "person_id", None),
        content=str(getattr(request_body, "message", "") or ""),
        ts_ms=int(time.time() * 1000),
        lang=str(getattr(request_body, "render_target_lang", None) or "zh").strip().lower() or "zh",
        metadata={
            "input_source": str(getattr(request_body, "input_source", None) or "").strip(),
            "reply_channel": str(getattr(request_body, "reply_channel", None) or "").strip(),
            "attachments": list(getattr(request_body, "attachments", []) or []),
            "multimodal_observations": dict(getattr(request_body, "multimodal_observations", {}) or {}),
            "attachment_summary": str(getattr(request_body, "attachment_summary", "") or "").strip(),
        },
    )

    async for event in _cortex_orchestrator_sse_generator_impl(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        governor_result=governor_result,
        intent_id=intent_id,
        metrics=metrics,
    ):
        yield event


__all__ = ["cortex_orchestrator_sse_generator"]
