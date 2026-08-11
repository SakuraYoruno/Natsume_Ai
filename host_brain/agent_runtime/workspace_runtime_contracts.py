"""Immutable values shared by the durable Workspace runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


WORKSPACE_RUNTIME_BINDING_INVALID = "WORKSPACE_RUNTIME_BINDING_INVALID"
WORKSPACE_HEARTBEAT_LOST = "WORKSPACE_HEARTBEAT_LOST"
WORKSPACE_CLEANUP_UNAVAILABLE = "WORKSPACE_CLEANUP_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class WorkspaceCleanupReceipt:
    workspace_id: str
    receipt_ref: str
    digest: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class WorkspaceArtifactPromotionReceipt:
    workspace_id: str
    accepted_event_id: str
    promotion_event_id: str
    artifact_ref: str
    no_change_receipt_ref: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class WorkspaceArtifactAbandonmentReceipt:
    workspace_id: str
    abandonment_event_id: str
    tombstone_ref: str
    reason_code: str
    replayed: bool


class DurableWorkspaceRuntimeError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or WORKSPACE_RUNTIME_BINDING_INVALID)
        self.detail = str(detail or "durable workspace runtime failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def required_workspace_value(value: Any, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > maximum:
        raise DurableWorkspaceRuntimeError(
            WORKSPACE_RUNTIME_BINDING_INVALID,
            f"{field} is missing or invalid",
        )
    return normalized


__all__ = [
    "DurableWorkspaceRuntimeError",
    "WORKSPACE_CLEANUP_UNAVAILABLE",
    "WORKSPACE_HEARTBEAT_LOST",
    "WORKSPACE_RUNTIME_BINDING_INVALID",
    "WorkspaceArtifactAbandonmentReceipt",
    "WorkspaceArtifactPromotionReceipt",
    "WorkspaceCleanupReceipt",
    "required_workspace_value",
]
