"""Public value objects for the canonical durable TaskStore.

This module owns only stable persistence-facing contracts.  Connection,
transaction, SQL, and row-codec authority remain in ``task_repository``.
``task_repository`` re-exports these names so existing callers keep their
original import path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Any, Mapping
import uuid


TASK_STORE_INVALID = "TASK_STORE_INVALID"

_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_MAX_OUTBOX_EVIDENCE_REFS = 32


class TaskStoreError(RuntimeError):
    """Fail-closed persistence error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or TASK_STORE_INVALID)
        self.detail = str(detail or "task store operation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


class AttemptStatus(StrEnum):
    CREATED = "CREATED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    RECOVERABLE = "RECOVERABLE"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"


class LeaseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class IdempotencyStatus(StrEnum):
    RESERVED = "RESERVED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"
    CANCELLED = "CANCELLED"


class IdempotencyRecoveryAction(StrEnum):
    COMMIT = "COMMIT"
    RELEASE = "RELEASE"
    REACQUIRE = "REACQUIRE"


class AcceptanceEffectCommitScope(StrEnum):
    NODE = "NODE"
    GRAPH = "GRAPH"


class AcceptanceEffectStatus(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class AcceptanceEffectIntentSpec:
    """Immutable input contract for one acceptance-bound side effect.

    The payload itself remains outside TaskStore.  This record freezes only its
    durable identity, digest, execution binding, and dispatch idempotency key.
    """

    effect_id: str
    graph_id: str
    graph_revision: int
    node_id: str
    attempt_id: str
    commit_scope: AcceptanceEffectCommitScope | str
    effect_type: str
    payload_ref: str
    payload: Mapping[str, Any]
    payload_sha256: str
    idempotency_key: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AcceptanceEffectIntentSpec":
        if not isinstance(value, Mapping):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "acceptance effect intent must be a mapping or AcceptanceEffectIntentSpec",
            )
        return cls(
            effect_id=value.get("effect_id"),
            graph_id=value.get("graph_id"),
            graph_revision=value.get("graph_revision"),
            node_id=value.get("node_id"),
            attempt_id=value.get("attempt_id"),
            commit_scope=value.get("commit_scope"),
            effect_type=value.get("effect_type"),
            payload_ref=value.get("payload_ref"),
            payload=value.get("payload"),
            payload_sha256=value.get("payload_sha256"),
            idempotency_key=value.get("idempotency_key"),
        )


def build_outbox_payload_ref(
    *,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
) -> str:
    """Return the canonical durable payload identity for a state event."""

    aggregate_kind = str(aggregate_type or "").strip()
    aggregate_ref = str(aggregate_id or "").strip()
    try:
        version = int(aggregate_version)
    except (TypeError, ValueError) as exc:
        raise TaskStoreError(TASK_STORE_INVALID, "outbox aggregate_version is invalid") from exc
    if not aggregate_kind or not aggregate_ref or version < 0:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "outbox aggregate identity and non-negative version are required",
        )
    return f"taskstore://event/{aggregate_kind}/{aggregate_ref}/{version}"


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    event_type: str
    payload_ref: str
    idempotency_key: str
    attempt_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    outbox_id: str = field(default_factory=lambda: f"outbox::{uuid.uuid4().hex}")
    available_ts_ms: int | None = None

    @classmethod
    def create(
        cls,
        *,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
        payload_ref: str,
        idempotency_key: str | None = None,
        attempt_id: str | None = None,
        evidence_refs: tuple[str, ...] = (),
        outbox_id: str | None = None,
        available_ts_ms: int | None = None,
    ) -> "OutboxMessage":
        generated_id = str(outbox_id or f"outbox::{uuid.uuid4().hex}")
        return cls(
            aggregate_type=str(aggregate_type or "").strip(),
            aggregate_id=str(aggregate_id or "").strip(),
            aggregate_version=int(aggregate_version),
            event_type=str(event_type or "").strip(),
            payload_ref=str(payload_ref or "").strip(),
            idempotency_key=str(idempotency_key or generated_id).strip(),
            attempt_id=str(attempt_id or "").strip() or None,
            evidence_refs=tuple(str(item).strip() for item in evidence_refs if str(item).strip()),
            outbox_id=generated_id,
            available_ts_ms=available_ts_ms,
        )

    def validate(self) -> None:
        if not all(
            (
                self.outbox_id,
                self.aggregate_type,
                self.aggregate_id,
                self.event_type,
                self.payload_ref,
                self.idempotency_key,
            )
        ):
            raise TaskStoreError(TASK_STORE_INVALID, "outbox identity and payload_ref are required")
        if self.aggregate_version < 0:
            raise TaskStoreError(TASK_STORE_INVALID, "outbox aggregate_version must be non-negative")
        expected_payload_ref = build_outbox_payload_ref(
            aggregate_type=self.aggregate_type,
            aggregate_id=self.aggregate_id,
            aggregate_version=self.aggregate_version,
        )
        if self.payload_ref != expected_payload_ref:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "outbox payload_ref must exactly bind aggregate identity/version: "
                f"expected {expected_payload_ref}",
            )
        if len(self.evidence_refs) > _MAX_OUTBOX_EVIDENCE_REFS:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                f"outbox evidence_refs exceed {_MAX_OUTBOX_EVIDENCE_REFS}",
            )
        if any(
            not _DURABLE_REFERENCE_PATTERN.fullmatch(str(reference or "").strip())
            for reference in self.evidence_refs
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "outbox evidence_refs must contain only bounded durable references",
            )


@dataclass(frozen=True, slots=True)
class OperationReservationResult:
    acquired: bool
    reservation: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BudgetConsumptionResult:
    accepted: bool
    reason_code: str | None
    reservation: dict[str, Any]
    exceeded_dimensions: tuple[str, ...]
    usage_id: str | None = None
    replayed: bool = False
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    recovery_id: str
    recovered_attempt_ids: tuple[str, ...]
    expired_lease_ids: tuple[str, ...]
    cancelled_attempt_ids: tuple[str, ...] = ()
    cancelled_lease_ids: tuple[str, ...] = ()
    released_lease_ids: tuple[str, ...] = ()
    cancelled_node_ids: tuple[str, ...] = ()
    waiting_reconciliation_node_ids: tuple[str, ...] = ()
    recovery_required_reservation_ids: tuple[str, ...] = ()

    @property
    def recovered_count(self) -> int:
        return len(self.recovered_attempt_ids)


__all__ = [
    "AcceptanceEffectCommitScope",
    "AcceptanceEffectIntentSpec",
    "AcceptanceEffectStatus",
    "AttemptStatus",
    "BudgetConsumptionResult",
    "IdempotencyRecoveryAction",
    "IdempotencyStatus",
    "LeaseStatus",
    "OperationReservationResult",
    "OutboxMessage",
    "RecoveryReport",
    "TASK_STORE_INVALID",
    "TaskStoreError",
    "build_outbox_payload_ref",
]
