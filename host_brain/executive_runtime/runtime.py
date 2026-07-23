"""Fail-closed compatibility boundary for the retired Executive Runtime."""

from __future__ import annotations

from typing import Any, AsyncGenerator


async def _executive_runtime_core_impl(
    *,
    ports: Any,
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
    bond_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    pii_ingress_stats: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """Fail closed for every route after the production cutover."""

    del (
        ports,
        request_body,
        request_id,
        session_id,
        ts_ms,
        emotion_context,
        emotion_snapshot,
        governor_result,
        intent_id,
        metrics,
        person_id,
        sanitized_message,
        bond_summary,
        retrieval_evidence_items,
        retrieval_summary,
        render_target_lang,
        llm_runtime,
        pii_ingress_stats,
    )
    declared_route = str(dict(intent_draft or {}).get("route") or "").strip().lower()
    if declared_route in {"chat", "skill", "delegate", "plan"}:
        raise PermissionError("CANONICAL_ROUTE_REQUIRES_GOVERNED_TASK_RUNTIME")
    if declared_route in {"task_progress", "recent_turn"}:
        raise PermissionError("READ_ROUTE_REQUIRES_DURABLE_PROJECTION")
    raise PermissionError("EXECUTIVE_RUNTIME_RETIRED")
    if False:  # pragma: no cover - preserves the async-generator contract.
        yield ""


__all__ = ["_executive_runtime_core_impl"]
