"""Fail-closed import boundary for the retired Executive Runtime.

No production Host route may construct Executive ports, select a route, plan,
or append a second dispatch history through this module.  Kept only so stale
imports receive the same explicit rejection as the core compatibility guard.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

async def executive_runtime_sse_generator(**kwargs: Any) -> AsyncGenerator[str, None]:
    """Reject a stale Executive entry without building any service graph."""

    declared_route = str(
        dict(kwargs.get("intent_draft") or {}).get("route") or ""
    ).strip().lower()
    if declared_route in {"chat", "skill", "delegate", "plan"}:
        raise PermissionError("CANONICAL_ROUTE_REQUIRES_GOVERNED_TASK_RUNTIME")
    if declared_route in {"task_progress", "recent_turn"}:
        raise PermissionError("READ_ROUTE_REQUIRES_DURABLE_PROJECTION")
    raise PermissionError("EXECUTIVE_RUNTIME_RETIRED")
    if False:  # pragma: no cover - preserves the async-generator contract.
        yield ""


__all__ = [
    "executive_runtime_sse_generator",
]
