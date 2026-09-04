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
TASK_STORE_DURABLE_DATA_INVALID = "TASK_STORE_DURABLE_DATA_INVALID"
TASK_STORE_CAS_CONFLICT = "TASK_STORE_CAS_CONFLICT"
TASK_STORE_SCHEMA_NOT_READY = "TASK_STORE_SCHEMA_NOT_READY"
TASK_STORE_NOT_FOUND = "TASK_STORE_NOT_FOUND"
TASK_STORE_LEASE_CONFLICT = "TASK_STORE_LEASE_CONFLICT"
TASK_STORE_OWNERSHIP_CONFLICT = "TASK_STORE_OWNERSHIP_CONFLICT"
TASK_STORE_OUTBOX_CONFLICT = "TASK_STORE_OUTBOX_CONFLICT"
TASK_STORE_CANCELLATION_CONFLICT = "TASK_STORE_CANCELLATION_CONFLICT"
TASK_STORE_BUDGET_EXCEEDED = "TASK_STORE_BUDGET_EXCEEDED"
TASK_STORE_BUDGET_ALREADY_EXCEEDED = "ALREADY_EXCEEDED"
TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED = "TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED"
TASK_STORE_SCHEDULER_CAPACITY = "TASK_STORE_SCHEDULER_CAPACITY"
TASK_STORE_NODE_ARTIFACT_RECONCILIATION_REQUIRED = (
    "TASK_STORE_NODE_ARTIFACT_RECONCILIATION_REQUIRED"
)

_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_MAX_OUTBOX_EVIDENCE_REFS = 32
REVISIONED_OUTBOX_AGGREGATE_TYPES = frozenset(
    {
        "TASK_GRAPH",
        "GRAPH_CANCELLATION_EFFECT_RECONCILIATION",
    }
)
_PARALLEL_SETTLEMENT_DECISION_REQUIRED_COLUMNS = frozenset(
    {
        "decision_ref",
        "schema_version",
        "graph_id",
        "graph_revision",
        "graph_version_before",
        "graph_version_after",
        "budget_id",
        "budget_version_before",
        "budget_version_after",
        "decision_kind",
        "selected_node_id",
        "graph_target_status",
        "budget_target_status",
        "payload_json",
        "payload_sha256",
        "graph_outbox_id",
        "budget_outbox_id",
        "created_ts_ms",
    }
)
_PARALLEL_SUCCESS_DECISION_REQUIRED_COLUMNS = frozenset(
    {
        "decision_ref",
        "schema_version",
        "graph_id",
        "graph_revision",
        "graph_version_before",
        "graph_version_after",
        "budget_id",
        "budget_version_before",
        "budget_version_after",
        "result_node_id",
        "result_attempt_id",
        "result_receipt_ref",
        "payload_json",
        "payload_sha256",
        "graph_outbox_id",
        "budget_outbox_id",
        "created_ts_ms",
    }
)


class TaskStoreError(RuntimeError):
    """Fail-closed persistence error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or TASK_STORE_INVALID)
        self.detail = str(detail or "task store operation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


def _required_task_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise TaskStoreError(TASK_STORE_INVALID, f"{field_name} is required")
    return text


def _non_negative_task_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "expected versions must be non-negative integers",
        )
    return int(value)


def _task_node_subject(
    graph_id: str,
    graph_revision: int,
    node_id: str,
) -> tuple[str, int, str]:
    graph_ref = _required_task_text(graph_id, field_name="graph_id")
    node_ref = _required_task_text(node_id, field_name="node_id")
    if (
        isinstance(graph_revision, bool)
        or not isinstance(graph_revision, int)
        or graph_revision < 1
    ):
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "graph_revision must be a positive integer",
        )
    return graph_ref, int(graph_revision), node_ref


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
    graph_revision: int | None = None,
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
    if aggregate_kind in REVISIONED_OUTBOX_AGGREGATE_TYPES:
        if graph_revision is not None:
            if type(graph_revision) is not int or graph_revision < 1:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "revisioned payload identity requires a positive graph_revision",
                )
            aggregate_ref = f"{aggregate_ref}@{graph_revision}"
    elif graph_revision is not None:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "non-revisioned payload identity must not carry graph_revision",
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
    graph_revision: int | None = None
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
        graph_revision: int | None = None,
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
            graph_revision=graph_revision,
            attempt_id=str(attempt_id or "").strip() or None,
            evidence_refs=tuple(str(item).strip() for item in evidence_refs if str(item).strip()),
            outbox_id=generated_id,
            available_ts_ms=available_ts_ms,
        )

    def validate(
        self,
        *,
        allow_legacy_graph_revision: bool = False,
        allow_legacy_graph_payload_ref: bool = False,
    ) -> None:
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
        if self.aggregate_type in REVISIONED_OUTBOX_AGGREGATE_TYPES:
            if not (
                self.aggregate_type == "TASK_GRAPH"
                and allow_legacy_graph_revision
                and self.graph_revision is None
            ) and (
                type(self.graph_revision) is not int or self.graph_revision < 1
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "revisioned outbox requires a positive integer graph_revision",
                )
        elif self.graph_revision is not None:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "non-revisioned outbox must not carry graph_revision",
            )
        expected_payload_ref = build_outbox_payload_ref(
            aggregate_type=self.aggregate_type,
            aggregate_id=self.aggregate_id,
            aggregate_version=self.aggregate_version,
            graph_revision=self.graph_revision,
        )
        legacy_payload_ref = (
            build_outbox_payload_ref(
                aggregate_type=self.aggregate_type,
                aggregate_id=self.aggregate_id,
                aggregate_version=self.aggregate_version,
            )
            if self.aggregate_type == "TASK_GRAPH"
            else None
        )
        if self.payload_ref != expected_payload_ref and not (
            allow_legacy_graph_payload_ref
            and legacy_payload_ref is not None
            and self.payload_ref == legacy_payload_ref
        ):
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
    "REVISIONED_OUTBOX_AGGREGATE_TYPES",
    "RecoveryReport",
    "TASK_STORE_CAS_CONFLICT",
    "TASK_STORE_BUDGET_ALREADY_EXCEEDED",
    "TASK_STORE_BUDGET_EXCEEDED",
    "TASK_STORE_CANCELLATION_CONFLICT",
    "TASK_STORE_DURABLE_DATA_INVALID",
    "TASK_STORE_INVALID",
    "TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED",
    "TASK_STORE_LEASE_CONFLICT",
    "TASK_STORE_NOT_FOUND",
    "TASK_STORE_OUTBOX_CONFLICT",
    "TASK_STORE_OWNERSHIP_CONFLICT",
    "TASK_STORE_SCHEMA_NOT_READY",
    "TASK_STORE_SCHEDULER_CAPACITY",
    "TaskStoreError",
    "build_outbox_payload_ref",
]
