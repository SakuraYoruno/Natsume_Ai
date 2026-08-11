"""Typed durable status boundary for the Host-owned resource manager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable


RESOURCE_MANAGER_STATUS_EVENT_TYPE = "RESOURCE_MANAGER_STATUS_UPDATED"
RESOURCE_MANAGER_STATUS_AGGREGATE_TYPE = "RESOURCE_MANAGER_STATUS"


class ResourceManagerStatusJournalError(RuntimeError):
    """Fail-closed durable status error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(
            reason_code or "RESOURCE_MANAGER_STATUS_JOURNAL_FAILED"
        )
        self.detail = str(detail or "resource manager status journal failed")
        super().__init__(f"{self.reason_code}:{self.detail}")


@dataclass(frozen=True, slots=True)
class ResourceManagerStatusHeadV1:
    runtime_scope_id: str
    revision: int
    event_id: str
    payload_digest: str
    observed_at_ms: int
    payload: Mapping[str, Any]


@runtime_checkable
class ResourceManagerStatusJournalPort(Protocol):
    """One runtime-scope journal with exact replay and monotonic revision."""

    @property
    def runtime_scope_id(self) -> str: ...

    def recover_head(self) -> ResourceManagerStatusHeadV1 | None: ...

    def append_status(
        self,
        *,
        expected_revision: int,
        reason: str,
        observed_at_ms: int,
        payload: Mapping[str, Any],
    ) -> ResourceManagerStatusHeadV1: ...


__all__ = [
    "RESOURCE_MANAGER_STATUS_AGGREGATE_TYPE",
    "RESOURCE_MANAGER_STATUS_EVENT_TYPE",
    "ResourceManagerStatusHeadV1",
    "ResourceManagerStatusJournalError",
    "ResourceManagerStatusJournalPort",
]
