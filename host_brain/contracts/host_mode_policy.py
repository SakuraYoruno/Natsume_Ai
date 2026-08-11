"""Contracts for the runtime-fenced Host automatic-mode policy."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence


HOST_MODE_AUTO_RULES = frozenset(
    {
        "gaming_detect",
        "gaming_exit",
        "idle_to_sleep",
        "wakeup_on_msg",
        "morning_wakeup",
    }
)
DEFAULT_HOST_MODE_AUTO_RULES: Mapping[str, bool] = MappingProxyType(
    {key: True for key in sorted(HOST_MODE_AUTO_RULES)}
)
HOST_MODE_POLICY_EVENT_KINDS = frozenset(
    {
        "INITIALIZED",
        "AUTO_RULES_UPDATED",
        "EVENT_SUBMITTED",
        "INTERACTION_RECORDED",
    }
)


@dataclass(frozen=True, slots=True)
class HostModePolicySnapshotV1:
    runtime_scope_id: str
    revision: int
    auto_rules: Mapping[str, bool]
    last_interaction_ts_ms: int
    operation_id: str
    event_digest: str
    updated_at_ms: int


@dataclass(frozen=True, slots=True)
class HostModePolicyEventV1:
    runtime_scope_id: str
    revision: int
    operation_id: str
    event_kind: str
    input_digest: str
    event_payload: Mapping[str, Any]
    auto_rules: Mapping[str, bool]
    last_interaction_ts_ms: int
    previous_event_digest: str
    event_digest: str
    runtime_owner_id: str
    runtime_lease_id: str
    runtime_fencing_token: int
    runtime_issued_at_ms: int
    runtime_expires_at_ms: int
    observed_at_ms: int
    created_at_ms: int


@dataclass(frozen=True, slots=True)
class HostModePolicyCommitV1:
    snapshot: HostModePolicySnapshotV1
    event: HostModePolicyEventV1
    exact_replay: bool = False


class HostModePolicyJournalPort(Protocol):
    def load(self) -> HostModePolicySnapshotV1 | None: ...

    def load_or_initialize(
        self,
        *,
        initial_auto_rules: Mapping[str, bool],
        initial_last_interaction_ts_ms: int,
        operation_id: str,
        observed_at_ms: int,
    ) -> HostModePolicyCommitV1: ...

    def append(
        self,
        *,
        expected_revision: int,
        operation_id: str,
        event_kind: str,
        input_payload: Mapping[str, Any],
        event_payload: Mapping[str, Any],
        auto_rules: Mapping[str, bool],
        last_interaction_ts_ms: int,
        observed_at_ms: int,
    ) -> HostModePolicyCommitV1: ...

    def get_operation(
        self,
        operation_id: str,
    ) -> HostModePolicyEventV1 | None: ...

    def history(self, *, limit: int = 100) -> Sequence[HostModePolicyEventV1]: ...


class HostModePolicyPort(Protocol):
    def snapshot(self) -> HostModePolicySnapshotV1: ...

    def get_operation(
        self,
        operation_id: str,
    ) -> HostModePolicyEventV1 | None: ...

    def update_auto_rules(
        self,
        rules: Mapping[str, bool],
        *,
        operation_id: str,
        expected_revision: int,
        observed_at_ms: int,
    ) -> HostModePolicyCommitV1: ...

    def record_event(
        self,
        *,
        operation_id: str,
        expected_revision: int,
        event_kind: str,
        input_payload: Mapping[str, Any],
        event_payload: Mapping[str, Any],
        last_interaction_ts_ms: int,
        observed_at_ms: int,
    ) -> HostModePolicyCommitV1: ...


__all__ = [
    "DEFAULT_HOST_MODE_AUTO_RULES",
    "HOST_MODE_AUTO_RULES",
    "HOST_MODE_POLICY_EVENT_KINDS",
    "HostModePolicyCommitV1",
    "HostModePolicyEventV1",
    "HostModePolicyJournalPort",
    "HostModePolicyPort",
    "HostModePolicySnapshotV1",
]
