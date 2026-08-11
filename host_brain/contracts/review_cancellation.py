"""Pure TaskNode status transition selected by Graph cancellation."""

from __future__ import annotations


def cancelled_review_target_status(status: str) -> str:
    """Return the only legal cancellation target for a Review checkpoint."""

    normalized = str(status or "").strip().upper()
    if normalized in {
        "WAITING_APPROVAL",
        "WAITING_ACCEPTANCE_REVIEW",
        "RETRY_PENDING",
    }:
        return "CANCELLED"
    if normalized in {
        "CREATED",
        "BLOCKED",
        "READY",
        "LEASED",
        "RUNNING",
        "EXECUTED",
        "VERIFYING",
        "VERIFIED",
    }:
        return "CANCEL_REQUESTED"
    raise ValueError(
        "Review checkpoint has no legal Graph cancellation transition"
    )


__all__ = ["cancelled_review_target_status"]
