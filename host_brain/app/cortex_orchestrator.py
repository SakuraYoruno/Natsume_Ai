"""Cortex orchestrator entrypoint.

This module is the canonical runtime entry for the Cortex family.
"""

from __future__ import annotations

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

