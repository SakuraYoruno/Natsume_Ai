"""Executive router entrypoint.

This module is the canonical runtime entry for the dispatch/router layer.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator


async def executive_router_sse_generator(
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
    person_id: str,
    sanitized_message: str,
    recall_slot_text: dict[str, Any],
    bond_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    pii_ingress_stats: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    from host_brain.app.executive_runtime import executive_runtime_sse_generator  # noqa: PLC0415

    async for event in executive_runtime_sse_generator(
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
        person_id=person_id,
        sanitized_message=sanitized_message,
        recall_slot_text=recall_slot_text,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        pii_ingress_stats=pii_ingress_stats,
    ):
        yield event


__all__ = ["executive_router_sse_generator"]

