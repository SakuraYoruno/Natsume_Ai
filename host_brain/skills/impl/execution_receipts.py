"""Shared, side-effect-free Skill execution receipt builders."""

from __future__ import annotations

from typing import Any


STATUS_REJECTED = "REJECTED"


def rejected_execution_result(
    *,
    event_id: str,
    reason_code: str,
    detail: str,
    ts_ms: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build the common receipt for a governed Skill rejection."""

    return {
        "status": STATUS_REJECTED,
        "event_id": event_id,
        "reason_code": reason_code,
        "detail": detail,
        "manifest_ref": None,
        "request_id": request_id,
        "ts_ms": ts_ms,
    }


__all__ = ["STATUS_REJECTED", "rejected_execution_result"]
