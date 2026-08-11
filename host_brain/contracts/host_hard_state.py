"""Public contracts for the durable Host HardState mode authority."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence


HOST_SYS_MODES = frozenset(
    {"normal", "focus", "gaming", "sleep", "maintenance", "locked"}
)
HOST_MODE_TRANSITIONS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "normal": frozenset({"focus", "gaming", "sleep", "maintenance", "locked"}),
        "focus": frozenset({"normal"}),
        "gaming": frozenset({"normal"}),
        "sleep": frozenset({"normal"}),
        "maintenance": frozenset({"normal", "locked"}),
        "locked": frozenset({"normal", "maintenance"}),
    }
)


@dataclass(frozen=True, slots=True)
class HostHardStateSnapshotV1:
    """Verified canonical head of one runtime-scoped mode journal."""

    runtime_scope_id: str
    revision: int
    sys_mode: str
    event_id: str
    event_digest: str
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class HostHardStateEventV1:
    """Immutable event recovered from the canonical mode journal."""

    runtime_scope_id: str
    revision: int
    event_id: str
    event_kind: str
    from_mode: str
    to_mode: str
    source: str
    reason: str
    previous_event_digest: str
    event_digest: str
    runtime_owner_id: str
    runtime_lease_id: str
    runtime_fencing_token: int
    runtime_issued_at_ms: int
    runtime_expires_at_ms: int
    transition_ts_ms: int
    created_at_ms: int

    def as_mode_history(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "from_mode": self.from_mode,
            "to_mode": self.to_mode,
            "source": self.source,
            "reason": self.reason,
            "ts_ms": self.transition_ts_ms,
            "success": True,
            "error": None,
            "revision": self.revision,
            "event_digest": self.event_digest,
            "runtime_scope_id": self.runtime_scope_id,
            "runtime_owner_id": self.runtime_owner_id,
            "runtime_lease_id": self.runtime_lease_id,
            "runtime_fencing_token": self.runtime_fencing_token,
        }


@dataclass(frozen=True, slots=True)
class HostHardStateCommitV1:
    """Result of one initialized or transitioned mode event."""

    snapshot: HostHardStateSnapshotV1
    event: HostHardStateEventV1
    exact_replay: bool = False


class HostHardStateJournalPort(Protocol):
    """Durable CAS/fencing boundary consumed by the app-local owner."""

    def load(self) -> HostHardStateSnapshotV1 | None: ...

    def load_or_initialize(
        self,
        *,
        initial_mode: str,
        event_id: str,
        transition_ts_ms: int,
    ) -> HostHardStateCommitV1: ...

    def transition(
        self,
        *,
        expected_revision: int,
        expected_mode: str,
        target_mode: str,
        event_id: str,
        source: str,
        reason: str,
        transition_ts_ms: int,
    ) -> HostHardStateCommitV1: ...

    def replay_transition(
        self,
        *,
        target_mode: str,
        event_id: str,
        source: str,
        reason: str,
    ) -> HostHardStateCommitV1 | None: ...

    def history(self, *, limit: int = 100) -> Sequence[HostHardStateEventV1]: ...


__all__ = [
    "HOST_MODE_TRANSITIONS",
    "HOST_SYS_MODES",
    "HostHardStateCommitV1",
    "HostHardStateEventV1",
    "HostHardStateJournalPort",
    "HostHardStateSnapshotV1",
]
