from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReplySurfacePrewarmResult:
    status: str
    remembered: int
    limit: int
    latency_ms: int
    reason_code: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "status": self.status,
            "remembered": self.remembered,
            "limit": self.limit,
            "latency_ms": self.latency_ms,
        }
        if self.reason_code:
            payload["reason_code"] = self.reason_code
        if self.detail:
            payload["detail"] = self.detail
        return payload


def resolve_reply_surface_prewarm_limit(raw: Any | None = None) -> int:
    value = raw
    if value is None:
        value = os.environ.get("HOST_REPLY_SURFACE_MEMORY_PREWARM_LIMIT") or "1024"
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = 1024
    return max(1, min(parsed, 20_000))


def run_reply_surface_cache_prewarm(*, limit: int | None = None, force: bool = True) -> dict[str, Any]:
    """Refresh reply-surface memory from already generated assistant history.

    The prewarm step deliberately learns from real model replies persisted in
    conversation_turn. It does not introduce canned dialogue or author new text.
    """
    resolved_limit = resolve_reply_surface_prewarm_limit(limit)
    started = time.monotonic()
    try:
        from host_brain.app.dependencies import prewarm_reply_surface_memory_store  # noqa: PLC0415

        remembered = prewarm_reply_surface_memory_store(
            force=bool(force),
            limit=resolved_limit,
        )
        result = ReplySurfacePrewarmResult(
            status="OK",
            remembered=max(0, int(remembered or 0)),
            limit=resolved_limit,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        )
        return result.as_dict()
    except Exception as exc:
        result = ReplySurfacePrewarmResult(
            status="FAILED",
            remembered=0,
            limit=resolved_limit,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            reason_code="REPLY_SURFACE_PREWARM_FAILED",
            detail=str(exc),
        )
        return result.as_dict()


__all__ = [
    "ReplySurfacePrewarmResult",
    "resolve_reply_surface_prewarm_limit",
    "run_reply_surface_cache_prewarm",
]
