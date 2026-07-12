"""Durable storage primitives for the canonical task runtime.

The store is deliberately not a scheduler.  It persists the contracts frozen by
TASK-0466 and provides transactional compare-and-swap, attempt ownership,
leases, recovery, business idempotency, budgets, and an outbox.  Legacy
``AgentJobRegistry`` remains the read authority during this shadow phase.

Schema creation is owned by the central audit migration catalog.  This module
never creates tables inline and fails closed unless the 0052 base and 0054
forward recovery migration have both been applied to the selected database.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
import base64
import binascii
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Iterator, Mapping, Protocol
import uuid

try:
    from cryptography.exceptions import InvalidSignature as _Ed25519InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey as _Ed25519PublicKey,
    )
except ImportError:  # pragma: no cover - exercised through dependency fail-closed test
    _Ed25519InvalidSignature = None
    _Ed25519PublicKey = None

from host_brain.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
    validate_budget_transition,
)
from host_brain.agent_runtime.acceptance_judge import (
    AcceptanceGate,
    AcceptanceJudgement,
)
from host_brain.agent_runtime.contract_errors import ContractViolation
from host_brain.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
    validate_task_graph_transition,
    validate_task_node_transition,
)
from host_brain.agent_runtime.verification_policy import (
    VerificationGate,
    VerificationResult,
)
from host_brain.core.ids import now_ms


TASK_STORE_SCHEMA_NOT_READY = "TASK_STORE_SCHEMA_NOT_READY"
TASK_STORE_NOT_FOUND = "TASK_STORE_NOT_FOUND"
TASK_STORE_CAS_CONFLICT = "TASK_STORE_CAS_CONFLICT"
TASK_STORE_LEASE_CONFLICT = "TASK_STORE_LEASE_CONFLICT"
TASK_STORE_OWNERSHIP_CONFLICT = "TASK_STORE_OWNERSHIP_CONFLICT"
TASK_STORE_INVALID = "TASK_STORE_INVALID"
TASK_STORE_OUTBOX_CONFLICT = "TASK_STORE_OUTBOX_CONFLICT"
TASK_STORE_BUDGET_EXCEEDED = "TASK_STORE_BUDGET_EXCEEDED"
TASK_STORE_DURABLE_DATA_INVALID = "TASK_STORE_DURABLE_DATA_INVALID"
TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED = "TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED"
TASK_STORE_RECONCILIATION_PROOF_REQUIRED = "TASK_STORE_RECONCILIATION_PROOF_REQUIRED"
TASK_STORE_RECONCILIATION_PROOF_INVALID = "TASK_STORE_RECONCILIATION_PROOF_INVALID"
TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED = "TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED"
TASK_STORE_CANCELLATION_CONFLICT = "TASK_STORE_CANCELLATION_CONFLICT"

TASK_RUNTIME_TABLES = frozenset(
    {
        "task_runtime_graph",
        "task_runtime_node",
        "task_runtime_attempt",
        "task_runtime_lease",
        "task_runtime_heartbeat",
        "task_runtime_idempotency_reservation",
        "task_runtime_budget_reservation",
        "task_runtime_outbox",
    }
)
_TASK_RUNTIME_REQUIRED_COLUMNS = {
    "task_runtime_idempotency_reservation": frozenset(
        {
            "attempt_id",
            "lease_id",
            "owner_id",
            "fencing_token",
            "expires_ts_ms",
            "legacy_receipt_raw",
            "reconciliation_proof_ref",
            "reconciliation_proof_digest",
            "reconciliation_proof_key_id",
            "reconciliation_proof_signature",
            "reconciliation_action",
            "reconciliation_outcome",
            "reconciler_actor_id",
            "reconciliation_recovery_version",
            "recovery_reason_code",
            "recovery_ts_ms",
        }
    ),
}
_DURABLE_REFERENCE_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_MAX_OUTBOX_EVIDENCE_REFS = 32
_LEGACY_RECEIPT_REF_PATTERN = re.compile(r"^legacy-receipt://0052/rowid--?[0-9]+$")


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


class ReconciliationProofVerifier(Protocol):
    """Verification-only boundary; it intentionally exposes no signing API."""

    def has_key(self, key_id: str) -> bool: ...

    def verify(self, *, key_id: str, message: bytes, signature: str) -> bool: ...


class Ed25519ReconciliationProofVerifier:
    """Verify authority attestations using public keys only.

    Signing keys and signing helpers deliberately do not exist in the runtime
    module.  Production composition may instead inject a remote implementation
    of :class:`ReconciliationProofVerifier` with the same verification-only API.
    """

    def __init__(self, public_keys: Mapping[str, bytes]) -> None:
        if _Ed25519PublicKey is None:
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
                "cryptography with Ed25519 support is required for proof verification",
            )
        keys: dict[str, Any] = {}
        for raw_key_id, raw_key in public_keys.items():
            key_id = str(raw_key_id or "").strip()
            try:
                key_bytes = bytes(raw_key)
            except (TypeError, ValueError) as exc:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "Ed25519 public keys must be byte strings",
                ) from exc
            if not key_id or len(key_bytes) != 32:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "reconciliation public keys require key_id and exactly 32 bytes",
                )
            try:
                keys[key_id] = _Ed25519PublicKey.from_public_bytes(key_bytes)
            except ValueError as exc:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"invalid Ed25519 public key for key_id {key_id}",
                ) from exc
        if not keys:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "at least one reconciliation public key is required",
            )
        self._public_keys = keys

    def has_key(self, key_id: str) -> bool:
        return str(key_id or "").strip() in self._public_keys

    def verify(self, *, key_id: str, message: bytes, signature: str) -> bool:
        key = self._public_keys.get(str(key_id or "").strip())
        supplied = str(signature or "").strip()
        if key is None or not supplied.startswith("ed25519:"):
            return False
        encoded = supplied.removeprefix("ed25519:")
        try:
            signature_bytes = base64.b64decode(
                encoded,
                altchars=b"-_",
                validate=True,
            )
        except (binascii.Error, ValueError):
            return False
        if len(signature_bytes) != 64:
            return False
        try:
            key.verify(signature_bytes, bytes(message))
        except (ValueError, TypeError):
            return False
        except Exception as exc:
            if _Ed25519InvalidSignature is not None and isinstance(
                exc,
                _Ed25519InvalidSignature,
            ):
                return False
            raise
        return True


_ATTEMPT_TRANSITIONS: dict[AttemptStatus, frozenset[AttemptStatus]] = {
    AttemptStatus.CREATED: frozenset({AttemptStatus.LEASED, AttemptStatus.CANCEL_REQUESTED}),
    AttemptStatus.LEASED: frozenset(
        {AttemptStatus.RUNNING, AttemptStatus.RECOVERABLE, AttemptStatus.CANCEL_REQUESTED}
    ),
    AttemptStatus.RUNNING: frozenset(
        {
            AttemptStatus.SUCCEEDED,
            AttemptStatus.FAILED,
            AttemptStatus.RECOVERABLE,
            AttemptStatus.CANCEL_REQUESTED,
        }
    ),
    AttemptStatus.RECOVERABLE: frozenset(
        {AttemptStatus.LEASED, AttemptStatus.CANCEL_REQUESTED}
    ),
    AttemptStatus.SUCCEEDED: frozenset(),
    AttemptStatus.FAILED: frozenset(),
    AttemptStatus.CANCEL_REQUESTED: frozenset({AttemptStatus.CANCELLED}),
    AttemptStatus.CANCELLED: frozenset(),
}


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


def build_operation_fingerprint(
    *,
    business_scope: str,
    operation: str,
    parameters: Mapping[str, Any] | None = None,
) -> str:
    """Build a stable business fingerprint without embedding secret payloads."""

    scope = str(business_scope or "").strip()
    operation_name = str(operation or "").strip()
    if not scope or not operation_name:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "business_scope and operation are required for an operation fingerprint",
        )
    canonical = _dumps(
        {
            "business_scope": scope,
            "operation": operation_name,
            "parameters": dict(parameters or {}),
        }
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def build_reconciliation_attestation_binding(
    *,
    reservation_id: str,
    business_scope: str,
    operation_fingerprint: str,
    recovery_version: int,
    reconciler_actor_id: str,
    attestation_ref: str,
    recovery_action: str,
    reconciliation_outcome: str,
    receipt_ref: str | None = None,
) -> bytes:
    """Build canonical bytes signed by the isolated reconciliation authority."""

    try:
        normalized_recovery_version = int(recovery_version)
    except (TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_RECONCILIATION_PROOF_INVALID,
            "recovery_version must be a non-negative integer",
        ) from exc
    action = str(recovery_action or "").strip().upper()
    outcome = str(reconciliation_outcome or "").strip().upper()
    receipt = str(receipt_ref or "").strip() or None
    if outcome not in {"NO_SIDE_EFFECT", "SIDE_EFFECT_CONFIRMED"}:
        raise TaskStoreError(
            TASK_STORE_RECONCILIATION_PROOF_INVALID,
            "reconciliation_outcome must be NO_SIDE_EFFECT or SIDE_EFFECT_CONFIRMED",
        )
    expected_outcome = {
        "COMMIT": "SIDE_EFFECT_CONFIRMED",
        "RELEASE": "NO_SIDE_EFFECT",
        "REACQUIRE": "NO_SIDE_EFFECT",
    }.get(action)
    if expected_outcome is None or outcome != expected_outcome:
        raise TaskStoreError(
            TASK_STORE_RECONCILIATION_PROOF_INVALID,
            "recovery_action and reconciliation_outcome are inconsistent",
        )
    if outcome == "SIDE_EFFECT_CONFIRMED":
        if receipt is None or not _DURABLE_REFERENCE_PATTERN.fullmatch(receipt):
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_PROOF_INVALID,
                "SIDE_EFFECT_CONFIRMED attestation requires a durable receipt_ref",
            )
    elif receipt is not None:
        raise TaskStoreError(
            TASK_STORE_RECONCILIATION_PROOF_INVALID,
            "NO_SIDE_EFFECT attestation cannot bind a receipt_ref",
        )
    binding = {
        "reservation_id": str(reservation_id or "").strip(),
        "business_scope": str(business_scope or "").strip(),
        "operation_fingerprint": str(operation_fingerprint or "").strip(),
        "recovery_version": normalized_recovery_version,
        "reconciler_actor_id": str(reconciler_actor_id or "").strip(),
        "attestation_ref": str(attestation_ref or "").strip(),
        "recovery_action": action,
        "reconciliation_outcome": outcome,
        "receipt_ref": receipt,
    }
    if (
        not binding["reservation_id"]
        or not binding["business_scope"]
        or not binding["operation_fingerprint"]
        or int(binding["recovery_version"]) < 0
        or not binding["reconciler_actor_id"]
        or not _DURABLE_REFERENCE_PATTERN.fullmatch(str(binding["attestation_ref"]))
    ):
        raise TaskStoreError(
            TASK_STORE_RECONCILIATION_PROOF_INVALID,
            "reconciliation attestation binding fields are invalid",
        )
    return _dumps(binding).encode("utf-8")


class DurableTaskStore:
    """SQLite-backed canonical TaskStore with transaction-scoped outbox writes."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
        reconciliation_proof_verifier: ReconciliationProofVerifier | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        self._reconciliation_proof_verifier = reconciliation_proof_verifier
        if verify_schema:
            self.assert_schema_ready()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _now(self) -> int:
        """Return the trusted injected clock; callers cannot override lease time."""

        return int(self._clock())

    def assert_schema_ready(self) -> None:
        if not self.db_path.exists():
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                f"task runtime database does not exist: {self.db_path}",
            )
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            columns = {
                table: {
                    str(column[1])
                    for column in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                for table in _TASK_RUNTIME_REQUIRED_COLUMNS
            }
            reservation_schema_row = conn.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'table' AND name = 'task_runtime_idempotency_reservation'
                """
            ).fetchone()
        finally:
            conn.close()
        present = {str(row[0]) for row in rows}
        missing = sorted(TASK_RUNTIME_TABLES - present)
        if missing:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                f"central task runtime migration is not applied; missing tables: {missing}",
            )
        missing_columns = {
            table: sorted(required - columns.get(table, set()))
            for table, required in _TASK_RUNTIME_REQUIRED_COLUMNS.items()
            if required - columns.get(table, set())
        }
        if missing_columns:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                f"task runtime schema is stale; missing columns: {missing_columns}",
            )
        reservation_schema = str(
            reservation_schema_row[0] if reservation_schema_row is not None else ""
        )
        if "RECOVERY_REQUIRED" not in reservation_schema:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime recovery migration 0054 is not applied",
            )

    # -- Graph and node persistence -------------------------------------------------

    def create_graph(
        self,
        graph: TaskGraph,
        *,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        ts_ms = self._now()
        with self._transaction() as conn:
            self.insert_graph_in_transaction(
                conn,
                graph,
                outbox=outbox,
                ts_ms=ts_ms,
            )
        return self.get_graph(graph.graph_id, graph.revision)

    def insert_graph_in_transaction(
        self,
        conn: sqlite3.Connection,
        graph: TaskGraph,
        *,
        outbox: OutboxMessage,
        ts_ms: int,
    ) -> None:
        """Insert one canonical graph and its event into an existing UoW.

        The caller owns commit, rollback, and connection lifetime.  This narrow
        hook lets a higher-level admission repository atomically bind a command
        to the exact graph it creates without duplicating TaskStore SQL or
        opening a nested SQLite transaction.
        """

        if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "connection-scoped graph insertion requires an active SQLite transaction",
            )
        graph.validate()
        try:
            trusted_ts_ms = int(ts_ms)
        except (TypeError, ValueError) as exc:
            raise TaskStoreError(TASK_STORE_INVALID, "graph timestamp is invalid") from exc
        if trusted_ts_ms < 0:
            raise TaskStoreError(TASK_STORE_INVALID, "graph timestamp must be non-negative")
        conn.execute(
            """
            INSERT INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref, status,
                status_version, graph_json, source_kind, source_ref,
                legacy_snapshot_json, created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, ?, 0, ?, 'CANONICAL', ?, NULL, ?, ?)
            """,
            (
                graph.graph_id,
                graph.revision,
                graph.root_work_order_id,
                graph.planner_ref,
                graph.status.value,
                _dumps(graph.to_dict()),
                f"{graph.graph_id}@{graph.revision}",
                trusted_ts_ms,
                trusted_ts_ms,
            ),
        )
        for node in graph.nodes:
            conn.execute(
                """
                INSERT INTO task_runtime_node(
                    graph_id, graph_revision, node_id, status, status_version,
                    node_json, cancellation_requested_ts_ms, created_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    graph.graph_id,
                    graph.revision,
                    node.node_id,
                    node.status.value,
                    node.status_version,
                    _dumps(node.to_dict()),
                    trusted_ts_ms,
                    trusted_ts_ms,
                ),
            )
        self.insert_outbox_in_transaction(
            conn,
            outbox,
            ts_ms=trusted_ts_ms,
            expected_aggregate=("TASK_GRAPH", graph.graph_id, 0),
            expected_event_type="TASK_GRAPH_CREATED",
            expected_attempt_id=None,
        )

    def insert_outbox_in_transaction(
        self,
        conn: sqlite3.Connection,
        message: OutboxMessage,
        *,
        ts_ms: int,
        expected_aggregate: tuple[str, str, int],
        expected_event_type: str,
        expected_attempt_id: str | None = None,
    ) -> None:
        """Append a validated TaskStore outbox row inside an existing UoW."""

        if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "connection-scoped outbox insertion requires an active SQLite transaction",
            )
        self._insert_outbox(
            conn,
            message,
            ts_ms=int(ts_ms),
            expected_aggregate=expected_aggregate,
            expected_event_type=expected_event_type,
            expected_attempt_id=expected_attempt_id,
        )

    def get_graph(self, graph_id: str, revision: int) -> dict[str, Any]:
        conn = self._connect()
        try:
            graph_row = conn.execute(
                "SELECT * FROM task_runtime_graph WHERE graph_id = ? AND revision = ?",
                (str(graph_id), int(revision)),
            ).fetchone()
            if graph_row is None:
                raise TaskStoreError(TASK_STORE_NOT_FOUND, f"TaskGraph not found: {graph_id}@{revision}")
            node_rows = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id
                """,
                (str(graph_id), int(revision)),
            ).fetchall()
            return _graph_record(graph_row, node_rows)
        finally:
            conn.close()

    def list_graphs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent validated graph records for versioned read adapters."""

        size = max(1, min(int(limit), 500))
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            graph_rows = conn.execute(
                """
                SELECT * FROM task_runtime_graph
                ORDER BY updated_ts_ms DESC, graph_id, revision DESC
                LIMIT ?
                """,
                (size,),
            ).fetchall()
            records: list[dict[str, Any]] = []
            for graph_row in graph_rows:
                node_rows = conn.execute(
                    """
                    SELECT * FROM task_runtime_node
                    WHERE graph_id = ? AND graph_revision = ?
                    ORDER BY node_id
                    """,
                    (str(graph_row["graph_id"]), int(graph_row["revision"])),
                ).fetchall()
                records.append(_graph_record(graph_row, node_rows))
            conn.commit()
            return records
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_node(self, graph_id: str, graph_revision: int, node_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                """,
                (str(graph_id), int(graph_revision), str(node_id)),
            ).fetchone()
            if row is None:
                raise TaskStoreError(TASK_STORE_NOT_FOUND, f"TaskNode not found: {node_id}")
            return _node_record(row)
        finally:
            conn.close()

    def read_scheduler_snapshot(self, graph_id: str, revision: int) -> dict[str, Any]:
        """Return one consistent read-only view for shadow scheduling."""
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            graph_row = self._require_graph(conn, graph_id, revision)
            node_rows = conn.execute(
                "SELECT * FROM task_runtime_node WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id",
                (str(graph_id), int(revision)),
            ).fetchall()
            attempt_rows = conn.execute(
                "SELECT * FROM task_runtime_attempt WHERE graph_id = ? AND graph_revision = ? ORDER BY node_id, attempt_no, attempt_id",
                (str(graph_id), int(revision)),
            ).fetchall()
            lease_rows = conn.execute(
                """SELECT lease.* FROM task_runtime_lease AS lease
                   JOIN task_runtime_attempt AS attempt ON attempt.attempt_id = lease.attempt_id
                   WHERE attempt.graph_id = ? AND attempt.graph_revision = ? AND lease.status = 'ACTIVE'
                   ORDER BY lease.attempt_id, lease.fencing_token""",
                (str(graph_id), int(revision)),
            ).fetchall()
            budget_rows = conn.execute(
                """SELECT * FROM task_runtime_budget_reservation
                   WHERE graph_id = ? AND graph_revision = ?
                   ORDER BY node_id, budget_id""",
                (str(graph_id), int(revision)),
            ).fetchall()
            snapshot = {
                "graph_record": _graph_record(graph_row, node_rows),
                "attempts": [dict(row) for row in attempt_rows],
                "active_leases": [dict(row) for row in lease_rows],
                "budgets": [self._budget_record(row) for row in budget_rows],
            }
            conn.commit()
            return snapshot
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def transition_graph(
        self,
        graph_id: str,
        revision: int,
        *,
        expected_version: int,
        target_status: TaskGraphStatus | str,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        ts_ms = self._now()
        with self._transaction() as conn:
            row = self._require_graph(conn, graph_id, revision)
            self._require_version(row, expected_version, subject=f"TaskGraph {graph_id}@{revision}")
            target = validate_task_graph_transition(str(row["status"]), target_status)
            node_rows = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id
                """,
                (str(graph_id), int(revision)),
            ).fetchall()
            graph_payload = _graph_record(row, node_rows)["graph"]
            graph_payload["status"] = target.value
            cursor = conn.execute(
                """
                UPDATE task_runtime_graph
                SET status = ?, status_version = status_version + 1,
                    graph_json = ?, updated_ts_ms = ?
                WHERE graph_id = ? AND revision = ? AND status_version = ?
                """,
                (
                    target.value,
                    _dumps(graph_payload),
                    ts_ms,
                    str(graph_id),
                    int(revision),
                    int(expected_version),
                ),
            )
            self._require_cas(cursor, subject=f"TaskGraph {graph_id}@{revision}")
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=("TASK_GRAPH", str(graph_id), int(expected_version) + 1),
                expected_event_type=f"TASK_GRAPH_{target.value}",
                expected_attempt_id=None,
            )
        return self.get_graph(graph_id, revision)

    def transition_node(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        expected_version: int,
        target_status: TaskNodeStatus | str,
        outbox: OutboxMessage,
        verification_result: VerificationResult | Mapping[str, Any] | None = None,
        acceptance_judgement: AcceptanceJudgement | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ts_ms = self._now()
        with self._transaction() as conn:
            row = self._require_node(conn, graph_id, graph_revision, node_id)
            self._require_version(row, expected_version, subject=f"TaskNode {node_id}")
            target = validate_task_node_transition(str(row["status"]), target_status)
            if target in {TaskNodeStatus.CANCEL_REQUESTED, TaskNodeStatus.CANCELLED}:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "node cancellation must use request_cancel_node for atomic fencing",
                )
            node_payload = _validated_node_payload(row)
            if target in {TaskNodeStatus.VERIFIED, TaskNodeStatus.VERIFICATION_FAILED}:
                graph_row = self._require_graph(conn, graph_id, graph_revision)
                try:
                    VerificationGate.validate_node_transition(
                        verification_result,
                        target_status=target.value,
                        graph_id=str(graph_id),
                        node_id=str(node_id),
                        root_work_order_id=str(graph_row["root_work_order_id"]),
                        outbox_evidence_refs=outbox.evidence_refs,
                    )
                except ContractViolation as exc:
                    raise TaskStoreError(exc.reason_code, exc.detail) from exc
            if target is TaskNodeStatus.ACCEPTED:
                graph_row = self._require_graph(conn, graph_id, graph_revision)
                try:
                    AcceptanceGate.validate_node_transition(
                        acceptance_judgement,
                        target_status=target.value,
                        graph_id=str(graph_id),
                        node_id=str(node_id),
                        root_work_order_id=str(graph_row["root_work_order_id"]),
                        node_acceptance_criteria=tuple(node_payload.get("acceptance_criteria") or ()),
                        outbox_evidence_refs=outbox.evidence_refs,
                    )
                except ContractViolation as exc:
                    raise TaskStoreError(exc.reason_code, exc.detail) from exc
            node_payload["status"] = target.value
            node_payload["status_version"] = int(expected_version) + 1
            cancel_ts = ts_ms if target is TaskNodeStatus.CANCEL_REQUESTED else row["cancellation_requested_ts_ms"]
            cursor = conn.execute(
                """
                UPDATE task_runtime_node
                SET status = ?, status_version = status_version + 1, node_json = ?,
                    cancellation_requested_ts_ms = ?, updated_ts_ms = ?
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                  AND status_version = ?
                """,
                (
                    target.value,
                    _dumps(node_payload),
                    cancel_ts,
                    ts_ms,
                    str(graph_id),
                    int(graph_revision),
                    str(node_id),
                    int(expected_version),
                ),
            )
            self._require_cas(cursor, subject=f"TaskNode {node_id}")
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "TASK_NODE",
                    f"{graph_id}@{int(graph_revision)}/{node_id}",
                    int(expected_version) + 1,
                ),
                expected_event_type=f"TASK_NODE_{target.value}",
                expected_attempt_id=None,
            )
        return self.get_node(graph_id, graph_revision, node_id)

    def request_cancel_node(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        expected_version: int,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        ts_ms = self._now()
        node_aggregate_id = f"{graph_id}@{int(graph_revision)}/{node_id}"
        direct_cancel_blocked = False
        with self._transaction() as conn:
            node = self._require_node(conn, graph_id, graph_revision, node_id)
            self._require_version(node, expected_version, subject=f"TaskNode {node_id}")
            try:
                cancel_target = validate_task_node_transition(
                    str(node["status"]),
                    TaskNodeStatus.CANCEL_REQUESTED,
                )
            except ContractViolation:
                cancel_target = validate_task_node_transition(
                    str(node["status"]),
                    TaskNodeStatus.CANCELLED,
                )
            if cancel_target is TaskNodeStatus.CANCELLED:
                unresolved = conn.execute(
                    """
                    SELECT 1
                    FROM task_runtime_attempt AS a
                    LEFT JOIN task_runtime_idempotency_reservation AS r
                      ON r.attempt_id = a.attempt_id
                     AND r.status IN ('RESERVED', 'RECOVERY_REQUIRED')
                    WHERE a.graph_id = ? AND a.graph_revision = ? AND a.node_id = ?
                      AND (
                        a.status IN (
                          'CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE', 'CANCEL_REQUESTED'
                        )
                        OR r.reservation_id IS NOT NULL
                      )
                    LIMIT 1
                    """,
                    (str(graph_id), int(graph_revision), str(node_id)),
                ).fetchone()
                direct_cancel_blocked = unresolved is not None
            if not direct_cancel_blocked:
                node_payload = _validated_node_payload(node)
                node_payload["status"] = cancel_target.value
                node_payload["status_version"] = int(expected_version) + 1
                cursor = conn.execute(
                    """
                    UPDATE task_runtime_node
                    SET status = ?, status_version = status_version + 1,
                        node_json = ?, cancellation_requested_ts_ms = ?, updated_ts_ms = ?
                    WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                      AND status_version = ?
                    """,
                    (
                        cancel_target.value,
                        _dumps(node_payload),
                        ts_ms,
                        ts_ms,
                        str(graph_id),
                        int(graph_revision),
                        str(node_id),
                        int(expected_version),
                    ),
                )
                self._require_cas(cursor, subject=f"TaskNode {node_id}")
                self._insert_outbox(
                    conn,
                    outbox,
                    ts_ms=ts_ms,
                    expected_aggregate=(
                        "TASK_NODE",
                        node_aggregate_id,
                        int(expected_version) + 1,
                    ),
                    expected_event_type=f"TASK_NODE_{cancel_target.value}",
                    expected_attempt_id=None,
                )

            attempts = conn.execute(
                """
                SELECT * FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                  AND status IN (
                    'CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE', 'CANCEL_REQUESTED'
                  )
                ORDER BY attempt_id
                """,
                (str(graph_id), int(graph_revision), str(node_id)),
            ).fetchall()
            for attempt in attempts:
                attempt_id = str(attempt["attempt_id"])
                attempt_version = int(attempt["status_version"])
                attempt_cursor = conn.execute(
                    """
                    UPDATE task_runtime_attempt
                    SET status = 'CANCEL_REQUESTED', owner_id = NULL,
                        status_version = status_version + 1, updated_ts_ms = ?
                    WHERE attempt_id = ? AND status_version = ?
                      AND status IN (
                        'CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE', 'CANCEL_REQUESTED'
                      )
                    """,
                    (ts_ms, attempt_id, attempt_version),
                )
                self._require_cas(attempt_cursor, subject=f"Attempt {attempt_id}")
                self._insert_outbox(
                    conn,
                    OutboxMessage.create(
                        aggregate_type="TASK_ATTEMPT",
                        aggregate_id=attempt_id,
                        aggregate_version=attempt_version + 1,
                        event_type="TASK_ATTEMPT_CANCEL_REQUESTED",
                        payload_ref=build_outbox_payload_ref(
                            aggregate_type="TASK_ATTEMPT",
                            aggregate_id=attempt_id,
                            aggregate_version=attempt_version + 1,
                        ),
                        idempotency_key=(
                            f"task-attempt-cancel-requested:{attempt_id}:{attempt_version + 1}"
                        ),
                        attempt_id=attempt_id,
                    ),
                    ts_ms=ts_ms,
                    expected_aggregate=("TASK_ATTEMPT", attempt_id, attempt_version + 1),
                    expected_event_type="TASK_ATTEMPT_CANCEL_REQUESTED",
                    expected_attempt_id=attempt_id,
                )
                leases = conn.execute(
                    """
                    SELECT * FROM task_runtime_lease
                    WHERE attempt_id = ? AND status = 'ACTIVE'
                    ORDER BY lease_id
                    """,
                    (attempt_id,),
                ).fetchall()
                for lease in leases:
                    lease_id = str(lease["lease_id"])
                    lease_version = int(lease["status_version"])
                    lease_cursor = conn.execute(
                        """
                        UPDATE task_runtime_lease
                        SET status = 'CANCELLED', released_ts_ms = ?,
                            status_version = status_version + 1
                        WHERE lease_id = ? AND status = 'ACTIVE' AND status_version = ?
                        """,
                        (ts_ms, lease_id, lease_version),
                    )
                    self._require_cas(lease_cursor, subject=f"Lease {lease_id}")
                    self._insert_outbox(
                        conn,
                        OutboxMessage.create(
                            aggregate_type="TASK_LEASE",
                            aggregate_id=lease_id,
                            aggregate_version=lease_version + 1,
                            event_type="TASK_LEASE_CANCELLED",
                            payload_ref=build_outbox_payload_ref(
                                aggregate_type="TASK_LEASE",
                                aggregate_id=lease_id,
                                aggregate_version=lease_version + 1,
                            ),
                            idempotency_key=(
                                f"task-lease-cancelled:{lease_id}:{lease_version + 1}"
                            ),
                            attempt_id=attempt_id,
                        ),
                        ts_ms=ts_ms,
                        expected_aggregate=("TASK_LEASE", lease_id, lease_version + 1),
                        expected_event_type="TASK_LEASE_CANCELLED",
                        expected_attempt_id=attempt_id,
                    )
                reservations = conn.execute(
                    """
                    SELECT * FROM task_runtime_idempotency_reservation
                    WHERE attempt_id = ? AND status = 'RESERVED'
                    ORDER BY reservation_id
                    """,
                    (attempt_id,),
                ).fetchall()
                for reservation in reservations:
                    self._mark_operation_recovery_required(
                        conn,
                        reservation,
                        reason_code="NODE_CANCEL_REQUESTED",
                        ts_ms=ts_ms,
                    )
            orphan_leases = conn.execute(
                """
                SELECT l.*, a.attempt_id AS bound_attempt_id
                FROM task_runtime_lease AS l
                JOIN task_runtime_attempt AS a ON a.attempt_id = l.attempt_id
                WHERE a.graph_id = ? AND a.graph_revision = ? AND a.node_id = ?
                  AND l.status = 'ACTIVE'
                ORDER BY l.lease_id
                """,
                (str(graph_id), int(graph_revision), str(node_id)),
            ).fetchall()
            for lease in orphan_leases:
                lease_id = str(lease["lease_id"])
                attempt_id = str(lease["bound_attempt_id"])
                lease_version = int(lease["status_version"])
                lease_cursor = conn.execute(
                    """
                    UPDATE task_runtime_lease
                    SET status = 'CANCELLED', released_ts_ms = ?,
                        status_version = status_version + 1
                    WHERE lease_id = ? AND status = 'ACTIVE' AND status_version = ?
                    """,
                    (ts_ms, lease_id, lease_version),
                )
                self._require_cas(lease_cursor, subject=f"Lease {lease_id}")
                self._insert_outbox(
                    conn,
                    OutboxMessage.create(
                        aggregate_type="TASK_LEASE",
                        aggregate_id=lease_id,
                        aggregate_version=lease_version + 1,
                        event_type="TASK_LEASE_CANCELLED_NODE",
                        payload_ref=build_outbox_payload_ref(
                            aggregate_type="TASK_LEASE",
                            aggregate_id=lease_id,
                            aggregate_version=lease_version + 1,
                        ),
                        idempotency_key=(
                            f"node-cancel-orphan-lease:{lease_id}:{lease_version + 1}"
                        ),
                        attempt_id=attempt_id,
                    ),
                    ts_ms=ts_ms,
                    expected_aggregate=("TASK_LEASE", lease_id, lease_version + 1),
                    expected_event_type="TASK_LEASE_CANCELLED_NODE",
                    expected_attempt_id=attempt_id,
                )
            orphan_reservations = conn.execute(
                """
                SELECT r.*
                FROM task_runtime_idempotency_reservation AS r
                JOIN task_runtime_attempt AS a ON a.attempt_id = r.attempt_id
                WHERE a.graph_id = ? AND a.graph_revision = ? AND a.node_id = ?
                  AND r.status = 'RESERVED'
                ORDER BY r.reservation_id
                """,
                (str(graph_id), int(graph_revision), str(node_id)),
            ).fetchall()
            for reservation in orphan_reservations:
                self._mark_operation_recovery_required(
                    conn,
                    reservation,
                    reason_code="NODE_CANCEL_REQUESTED",
                    ts_ms=ts_ms,
                )
        if direct_cancel_blocked:
            raise TaskStoreError(
                TASK_STORE_CANCELLATION_CONFLICT,
                "direct-cancel fenced workers but node awaits reconciliation/recovery",
            )
        return self.get_node(graph_id, graph_revision, node_id)

    # -- Attempts, leases, heartbeat, and recovery ---------------------------------

    def create_attempt(
        self,
        *,
        attempt_id: str,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_no: int,
        expected_node_version: int,
        operation_scope: str | None = None,
        operation_fingerprint: str | None = None,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        attempt_ref = str(attempt_id or "").strip()
        scope = str(operation_scope or "").strip() or None
        fingerprint = str(operation_fingerprint or "").strip() or None
        if not attempt_ref or int(attempt_no) < 1 or ((scope is None) != (fingerprint is None)):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "attempt_id, positive attempt_no, and paired operation scope/fingerprint are required",
            )
        ts_ms = self._now()
        with self._transaction() as conn:
            node = self._require_node(conn, graph_id, graph_revision, node_id)
            self._require_version(node, expected_node_version, subject=f"TaskNode {node_id}")
            if str(node["status"]) != TaskNodeStatus.READY.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"attempt requires READY node, got {node['status']}",
                )
            try:
                conn.execute(
                    """
                    INSERT INTO task_runtime_attempt(
                        attempt_id, graph_id, graph_revision, node_id, attempt_no,
                        status, status_version, owner_id, operation_scope,
                        operation_fingerprint, created_ts_ms, acquired_ts_ms,
                        started_ts_ms, completed_ts_ms, updated_ts_ms, error_ref
                    ) VALUES (?, ?, ?, ?, ?, 'CREATED', 0, NULL, ?, ?, ?, NULL, NULL, NULL, ?, NULL)
                    """,
                    (
                        attempt_ref,
                        str(graph_id),
                        int(graph_revision),
                        str(node_id),
                        int(attempt_no),
                        scope,
                        fingerprint,
                        ts_ms,
                        ts_ms,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    f"attempt identity or live ownership conflict for TaskNode {node_id}",
                ) from exc
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=("TASK_ATTEMPT", attempt_ref, 0),
                expected_event_type="TASK_ATTEMPT_CREATED",
                expected_attempt_id=attempt_ref,
            )
        return self.get_attempt(attempt_ref)

    def get_attempt(self, attempt_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = self._require_attempt(conn, attempt_id)
            return dict(row)
        finally:
            conn.close()

    def acquire_lease(
        self,
        attempt_id: str,
        *,
        owner_id: str,
        ttl_ms: int,
        expected_attempt_version: int,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        owner = str(owner_id or "").strip()
        if not owner or int(ttl_ms) <= 0:
            raise TaskStoreError(TASK_STORE_INVALID, "lease owner and positive ttl_ms are required")
        ts_ms = self._now()
        expires_ts_ms = ts_ms + int(ttl_ms)
        lease_id = f"lease::{uuid.uuid4().hex}"
        heartbeat_id = f"heartbeat::{uuid.uuid4().hex}"
        with self._transaction() as conn:
            attempt = self._require_attempt(conn, attempt_id)
            self._require_version(
                attempt,
                expected_attempt_version,
                subject=f"Attempt {attempt_id}",
            )
            self._assert_node_not_cancelled_for_attempt(conn, attempt)
            active = conn.execute(
                """
                SELECT * FROM task_runtime_lease
                WHERE attempt_id = ? AND status = 'ACTIVE'
                """,
                (str(attempt_id),),
            ).fetchone()
            current_status = AttemptStatus(str(attempt["status"]))
            if active is not None and int(active["expires_ts_ms"]) > ts_ms:
                if str(active["owner_id"]) == owner:
                    return dict(active)
                raise TaskStoreError(
                    TASK_STORE_LEASE_CONFLICT,
                    f"attempt {attempt_id} is leased by {active['owner_id']}",
                )
            if active is not None:
                expired_cursor = conn.execute(
                    """
                    UPDATE task_runtime_lease
                    SET status = 'EXPIRED', released_ts_ms = ?,
                        status_version = status_version + 1
                    WHERE lease_id = ? AND status = 'ACTIVE'
                    """,
                    (ts_ms, str(active["lease_id"])),
                )
                self._require_cas(expired_cursor, subject=f"Lease {active['lease_id']}")
                expired_lease_version = int(active["status_version"]) + 1
                self._insert_outbox(
                    conn,
                    OutboxMessage.create(
                        aggregate_type="TASK_LEASE",
                        aggregate_id=str(active["lease_id"]),
                        aggregate_version=expired_lease_version,
                        event_type="TASK_LEASE_EXPIRED",
                        payload_ref=build_outbox_payload_ref(
                            aggregate_type="TASK_LEASE",
                            aggregate_id=str(active["lease_id"]),
                            aggregate_version=expired_lease_version,
                        ),
                        idempotency_key=(
                            f"lease-expired:{active['lease_id']}:{expired_lease_version}"
                        ),
                        attempt_id=str(attempt_id),
                    ),
                    ts_ms=ts_ms,
                    expected_aggregate=(
                        "TASK_LEASE",
                        str(active["lease_id"]),
                        expired_lease_version,
                    ),
                    expected_event_type="TASK_LEASE_EXPIRED",
                    expected_attempt_id=str(attempt_id),
                )
                if current_status in {AttemptStatus.LEASED, AttemptStatus.RUNNING}:
                    current_status = AttemptStatus.RECOVERABLE
            elif active is None and current_status in {AttemptStatus.LEASED, AttemptStatus.RUNNING}:
                current_status = AttemptStatus.RECOVERABLE
            if current_status not in {AttemptStatus.CREATED, AttemptStatus.RECOVERABLE}:
                raise TaskStoreError(
                    TASK_STORE_LEASE_CONFLICT,
                    f"attempt {attempt_id} in {current_status.value} cannot acquire a lease",
                )
            self._mark_attempt_reservations_recovery_required(
                conn,
                attempt_id=str(attempt_id),
                ts_ms=ts_ms,
            )

            node = self._require_node(
                conn,
                str(attempt["graph_id"]),
                int(attempt["graph_revision"]),
                str(attempt["node_id"]),
            )
            if str(node["status"]) == TaskNodeStatus.READY.value:
                node_payload = _validated_node_payload(node)
                node_payload["status"] = TaskNodeStatus.LEASED.value
                node_payload["status_version"] = int(node["status_version"]) + 1
                node_cursor = conn.execute(
                    """
                    UPDATE task_runtime_node
                    SET status = 'LEASED', status_version = status_version + 1,
                        node_json = ?, updated_ts_ms = ?
                    WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                      AND status_version = ?
                    """,
                    (
                        _dumps(node_payload),
                        ts_ms,
                        str(attempt["graph_id"]),
                        int(attempt["graph_revision"]),
                        str(attempt["node_id"]),
                        int(node["status_version"]),
                    ),
                )
                self._require_cas(node_cursor, subject=f"TaskNode {attempt['node_id']}")
                node_aggregate_id = (
                    f"{attempt['graph_id']}@{int(attempt['graph_revision'])}/{attempt['node_id']}"
                )
                node_version = int(node["status_version"]) + 1
                self._insert_outbox(
                    conn,
                    OutboxMessage.create(
                        aggregate_type="TASK_NODE",
                        aggregate_id=node_aggregate_id,
                        aggregate_version=node_version,
                        event_type="TASK_NODE_LEASED",
                        payload_ref=build_outbox_payload_ref(
                            aggregate_type="TASK_NODE",
                            aggregate_id=node_aggregate_id,
                            aggregate_version=node_version,
                        ),
                        idempotency_key=(
                            f"task-node-leased:{node_aggregate_id}:{node_version}"
                        ),
                        attempt_id=str(attempt_id),
                    ),
                    ts_ms=ts_ms,
                    expected_aggregate=("TASK_NODE", node_aggregate_id, node_version),
                    expected_event_type="TASK_NODE_LEASED",
                    expected_attempt_id=str(attempt_id),
                )
            elif str(node["status"]) not in {
                TaskNodeStatus.LEASED.value,
                TaskNodeStatus.RUNNING.value,
            }:
                raise TaskStoreError(
                    TASK_STORE_LEASE_CONFLICT,
                    f"node {attempt['node_id']} in {node['status']} cannot be leased",
                )

            fencing_row = conn.execute(
                "SELECT COALESCE(MAX(fencing_token), 0) FROM task_runtime_lease WHERE attempt_id = ?",
                (str(attempt_id),),
            ).fetchone()
            fencing_token = int(fencing_row[0]) + 1
            cursor = conn.execute(
                """
                UPDATE task_runtime_attempt
                SET status = 'LEASED', owner_id = ?, acquired_ts_ms = ?,
                    status_version = status_version + 1, updated_ts_ms = ?
                WHERE attempt_id = ? AND status_version = ?
                """,
                (owner, ts_ms, ts_ms, str(attempt_id), int(expected_attempt_version)),
            )
            self._require_cas(cursor, subject=f"Attempt {attempt_id}")
            conn.execute(
                """
                INSERT INTO task_runtime_lease(
                    lease_id, attempt_id, owner_id, fencing_token, acquired_ts_ms,
                    heartbeat_ts_ms, expires_ts_ms, released_ts_ms, status, status_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'ACTIVE', 0)
                """,
                (
                    lease_id,
                    str(attempt_id),
                    owner,
                    fencing_token,
                    ts_ms,
                    ts_ms,
                    expires_ts_ms,
                ),
            )
            conn.execute(
                """
                INSERT INTO task_runtime_heartbeat(
                    heartbeat_id, lease_id, attempt_id, owner_id, fencing_token,
                    observed_ts_ms, expires_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    heartbeat_id,
                    lease_id,
                    str(attempt_id),
                    owner,
                    fencing_token,
                    ts_ms,
                    expires_ts_ms,
                ),
            )
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "TASK_ATTEMPT",
                    str(attempt_id),
                    int(expected_attempt_version) + 1,
                ),
                expected_event_type="TASK_ATTEMPT_LEASED",
                expected_attempt_id=str(attempt_id),
            )
            lease = conn.execute(
                "SELECT * FROM task_runtime_lease WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()
            return dict(lease)

    def heartbeat_lease(
        self,
        lease_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        expected_lease_version: int,
        ttl_ms: int,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        ts_ms = self._now()
        if int(ttl_ms) <= 0:
            raise TaskStoreError(TASK_STORE_INVALID, "positive heartbeat ttl_ms is required")
        expires_ts_ms = ts_ms + int(ttl_ms)
        with self._transaction() as conn:
            lease = self._require_lease(conn, lease_id)
            self._require_version(lease, expected_lease_version, subject=f"Lease {lease_id}")
            attempt = self._require_attempt(conn, str(lease["attempt_id"]))
            self._assert_node_not_cancelled_for_attempt(conn, attempt)
            self._assert_lease_owner(
                lease,
                owner_id=owner_id,
                fencing_token=fencing_token,
                now_ts_ms=ts_ms,
            )
            cursor = conn.execute(
                """
                UPDATE task_runtime_lease
                SET heartbeat_ts_ms = ?, expires_ts_ms = ?,
                    status_version = status_version + 1
                WHERE lease_id = ? AND status = 'ACTIVE' AND status_version = ?
                """,
                (ts_ms, expires_ts_ms, str(lease_id), int(expected_lease_version)),
            )
            self._require_cas(cursor, subject=f"Lease {lease_id}")
            conn.execute(
                """
                INSERT INTO task_runtime_heartbeat(
                    heartbeat_id, lease_id, attempt_id, owner_id, fencing_token,
                    observed_ts_ms, expires_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"heartbeat::{uuid.uuid4().hex}",
                    str(lease_id),
                    str(lease["attempt_id"]),
                    str(owner_id),
                    int(fencing_token),
                    ts_ms,
                    expires_ts_ms,
                ),
            )
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "TASK_LEASE",
                    str(lease_id),
                    int(expected_lease_version) + 1,
                ),
                expected_event_type="TASK_LEASE_HEARTBEAT",
                expected_attempt_id=str(lease["attempt_id"]),
            )
            updated = self._require_lease(conn, lease_id)
            return dict(updated)

    def transition_attempt(
        self,
        attempt_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        target_status: AttemptStatus | str,
        outbox: OutboxMessage,
        error_ref: str | None = None,
    ) -> dict[str, Any]:
        ts_ms = self._now()
        with self._transaction() as conn:
            attempt = self._require_attempt(conn, attempt_id)
            self._require_version(attempt, expected_version, subject=f"Attempt {attempt_id}")
            self._assert_node_not_cancelled_for_attempt(conn, attempt)
            lease = self._active_lease(conn, attempt_id)
            if lease is None:
                raise TaskStoreError(
                    TASK_STORE_OWNERSHIP_CONFLICT,
                    f"attempt {attempt_id} has no active lease",
                )
            self._assert_lease_owner(
                lease,
                owner_id=owner_id,
                fencing_token=fencing_token,
                now_ts_ms=ts_ms,
            )
            target = _validate_attempt_transition(str(attempt["status"]), target_status)
            started_ts = ts_ms if target is AttemptStatus.RUNNING else attempt["started_ts_ms"]
            completed_ts = (
                ts_ms
                if target in {AttemptStatus.SUCCEEDED, AttemptStatus.FAILED, AttemptStatus.CANCELLED}
                else attempt["completed_ts_ms"]
            )
            cursor = conn.execute(
                """
                UPDATE task_runtime_attempt
                SET status = ?, status_version = status_version + 1,
                    started_ts_ms = ?, completed_ts_ms = ?, updated_ts_ms = ?, error_ref = ?
                WHERE attempt_id = ? AND status_version = ? AND owner_id = ?
                """,
                (
                    target.value,
                    started_ts,
                    completed_ts,
                    ts_ms,
                    str(error_ref or "").strip() or None,
                    str(attempt_id),
                    int(expected_version),
                    str(owner_id),
                ),
            )
            self._require_cas(cursor, subject=f"Attempt {attempt_id}")
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=("TASK_ATTEMPT", str(attempt_id), int(expected_version) + 1),
                expected_event_type=f"TASK_ATTEMPT_{target.value}",
                expected_attempt_id=str(attempt_id),
            )
            if target in {
                AttemptStatus.SUCCEEDED,
                AttemptStatus.FAILED,
                AttemptStatus.CANCELLED,
            }:
                lease_target = (
                    LeaseStatus.CANCELLED
                    if target is AttemptStatus.CANCELLED
                    else LeaseStatus.RELEASED
                )
                lease_version = int(lease["status_version"]) + 1
                lease_cursor = conn.execute(
                    """
                    UPDATE task_runtime_lease
                    SET status = ?, released_ts_ms = ?,
                        status_version = status_version + 1
                    WHERE lease_id = ? AND status = 'ACTIVE' AND status_version = ?
                    """,
                    (
                        lease_target.value,
                        ts_ms,
                        str(lease["lease_id"]),
                        int(lease["status_version"]),
                    ),
                )
                self._require_cas(
                    lease_cursor,
                    subject=f"Lease {lease['lease_id']}",
                )
                terminal_event_type = (
                    "TASK_LEASE_CANCELLED_TERMINAL"
                    if lease_target is LeaseStatus.CANCELLED
                    else "TASK_LEASE_RELEASED_TERMINAL"
                )
                self._insert_outbox(
                    conn,
                    OutboxMessage.create(
                        aggregate_type="TASK_LEASE",
                        aggregate_id=str(lease["lease_id"]),
                        aggregate_version=lease_version,
                        event_type=terminal_event_type,
                        payload_ref=build_outbox_payload_ref(
                            aggregate_type="TASK_LEASE",
                            aggregate_id=str(lease["lease_id"]),
                            aggregate_version=lease_version,
                        ),
                        idempotency_key=(
                            f"attempt-terminal-lease:{attempt_id}:{lease['lease_id']}:"
                            f"{lease_version}"
                        ),
                        attempt_id=str(attempt_id),
                    ),
                    ts_ms=ts_ms,
                    expected_aggregate=(
                        "TASK_LEASE",
                        str(lease["lease_id"]),
                        lease_version,
                    ),
                    expected_event_type=terminal_event_type,
                    expected_attempt_id=str(attempt_id),
                )
            return dict(self._require_attempt(conn, attempt_id))

    def release_lease(
        self,
        lease_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        expected_lease_version: int,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        ts_ms = self._now()
        with self._transaction() as conn:
            lease = self._require_lease(conn, lease_id)
            self._require_version(lease, expected_lease_version, subject=f"Lease {lease_id}")
            self._assert_lease_owner(
                lease,
                owner_id=owner_id,
                fencing_token=fencing_token,
                now_ts_ms=ts_ms,
            )
            cursor = conn.execute(
                """
                UPDATE task_runtime_lease
                SET status = 'RELEASED', released_ts_ms = ?,
                    status_version = status_version + 1
                WHERE lease_id = ? AND status_version = ? AND status = 'ACTIVE'
                """,
                (ts_ms, str(lease_id), int(expected_lease_version)),
            )
            self._require_cas(cursor, subject=f"Lease {lease_id}")
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "TASK_LEASE",
                    str(lease_id),
                    int(expected_lease_version) + 1,
                ),
                expected_event_type="TASK_LEASE_RELEASED",
                expected_attempt_id=str(lease["attempt_id"]),
            )
            return dict(self._require_lease(conn, lease_id))

    def recover_startup(
        self,
        *,
        recovery_id: str,
    ) -> RecoveryReport:
        recovery_ref = str(recovery_id or "").strip()
        if not recovery_ref:
            raise TaskStoreError(TASK_STORE_INVALID, "recovery_id is required")
        ts_ms = self._now()
        recovered: list[str] = []
        expired: list[str] = []
        cancelled: list[str] = []
        cancelled_leases: list[str] = []
        released_leases: list[str] = []
        cancelled_nodes: list[str] = []
        waiting_reconciliation_nodes: list[str] = []
        recovery_required_reservations: set[str] = set()
        with self._transaction() as conn:
            terminal_leases = conn.execute(
                """
                SELECT l.*, a.status AS attempt_status,
                       n.status AS node_status
                FROM task_runtime_lease AS l
                JOIN task_runtime_attempt AS a ON a.attempt_id = l.attempt_id
                JOIN task_runtime_node AS n
                  ON n.graph_id = a.graph_id
                 AND n.graph_revision = a.graph_revision
                 AND n.node_id = a.node_id
                WHERE l.status = 'ACTIVE'
                  AND a.status IN ('SUCCEEDED', 'FAILED', 'CANCELLED')
                ORDER BY l.lease_id
                """
            ).fetchall()
            for lease in terminal_leases:
                lease_id = str(lease["lease_id"])
                attempt_id = str(lease["attempt_id"])
                cancel_lease = (
                    str(lease["attempt_status"]) == AttemptStatus.CANCELLED.value
                    or str(lease["node_status"])
                    in {
                        TaskNodeStatus.CANCEL_REQUESTED.value,
                        TaskNodeStatus.CANCELLED.value,
                    }
                )
                lease_target = (
                    LeaseStatus.CANCELLED if cancel_lease else LeaseStatus.RELEASED
                )
                lease_version = int(lease["status_version"]) + 1
                cursor = conn.execute(
                    """
                    UPDATE task_runtime_lease
                    SET status = ?, released_ts_ms = ?,
                        status_version = status_version + 1
                    WHERE lease_id = ? AND status = 'ACTIVE' AND status_version = ?
                    """,
                    (
                        lease_target.value,
                        ts_ms,
                        lease_id,
                        int(lease["status_version"]),
                    ),
                )
                self._require_cas(cursor, subject=f"Lease {lease_id}")
                if lease_target is LeaseStatus.CANCELLED:
                    cancelled_leases.append(lease_id)
                    terminal_event_type = "TASK_LEASE_CANCELLED_RECOVERY"
                else:
                    released_leases.append(lease_id)
                    terminal_event_type = "TASK_LEASE_RELEASED_RECOVERY"
                self._insert_outbox(
                    conn,
                    OutboxMessage.create(
                        aggregate_type="TASK_LEASE",
                        aggregate_id=lease_id,
                        aggregate_version=lease_version,
                        event_type=terminal_event_type,
                        payload_ref=build_outbox_payload_ref(
                            aggregate_type="TASK_LEASE",
                            aggregate_id=lease_id,
                            aggregate_version=lease_version,
                        ),
                        idempotency_key=(
                            f"startup-terminal-lease:{recovery_ref}:{lease_id}:"
                            f"{lease_version}"
                        ),
                        attempt_id=attempt_id,
                    ),
                    ts_ms=ts_ms,
                    expected_aggregate=("TASK_LEASE", lease_id, lease_version),
                    expected_event_type=terminal_event_type,
                    expected_attempt_id=attempt_id,
                )
            rows = conn.execute(
                """
                SELECT a.*, l.lease_id, l.expires_ts_ms,
                       l.status_version AS lease_status_version,
                       n.status AS node_status
                FROM task_runtime_attempt AS a
                LEFT JOIN task_runtime_lease AS l
                  ON l.attempt_id = a.attempt_id AND l.status = 'ACTIVE'
                JOIN task_runtime_node AS n
                  ON n.graph_id = a.graph_id
                 AND n.graph_revision = a.graph_revision
                 AND n.node_id = a.node_id
                WHERE (
                    n.status = 'CANCEL_REQUESTED'
                    AND a.status IN (
                        'CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE', 'CANCEL_REQUESTED'
                    )
                ) OR (
                    a.status = 'CANCEL_REQUESTED'
                ) OR (
                    a.status IN ('LEASED', 'RUNNING')
                    AND (l.lease_id IS NULL OR l.expires_ts_ms <= ?)
                ) OR (
                    a.status = 'CREATED' AND l.lease_id IS NULL
                )
                ORDER BY a.attempt_id
                """,
                (ts_ms,),
            ).fetchall()
            for row in rows:
                current_status = AttemptStatus(str(row["status"]))
                node_cancel_requested = (
                    str(row["node_status"]) == TaskNodeStatus.CANCEL_REQUESTED.value
                )
                cancel_attempt = node_cancel_requested or current_status in {
                    AttemptStatus.CREATED,
                    AttemptStatus.CANCEL_REQUESTED,
                }
                lease_id = str(row["lease_id"] or "").strip()
                if lease_id:
                    lease_target = (
                        LeaseStatus.CANCELLED if cancel_attempt else LeaseStatus.EXPIRED
                    )
                    cursor = conn.execute(
                        """
                        UPDATE task_runtime_lease
                        SET status = ?, released_ts_ms = ?,
                            status_version = status_version + 1
                        WHERE lease_id = ? AND status = 'ACTIVE'
                          AND status_version = ?
                          AND (? = 'CANCELLED' OR expires_ts_ms <= ?)
                        """,
                        (
                            lease_target.value,
                            ts_ms,
                            lease_id,
                            int(row["lease_status_version"]),
                            lease_target.value,
                            ts_ms,
                        ),
                    )
                    self._require_cas(cursor, subject=f"Lease {lease_id}")
                    if lease_target is LeaseStatus.CANCELLED:
                        cancelled_leases.append(lease_id)
                    else:
                        expired.append(lease_id)
                    lease_version = int(row["lease_status_version"]) + 1
                    self._insert_outbox(
                        conn,
                        OutboxMessage.create(
                            aggregate_type="TASK_LEASE",
                            aggregate_id=lease_id,
                            aggregate_version=lease_version,
                            event_type=(
                                "TASK_LEASE_CANCELLED_RECOVERY"
                                if lease_target is LeaseStatus.CANCELLED
                                else "TASK_LEASE_EXPIRED"
                            ),
                            payload_ref=build_outbox_payload_ref(
                                aggregate_type="TASK_LEASE",
                                aggregate_id=lease_id,
                                aggregate_version=lease_version,
                            ),
                            idempotency_key=(
                                f"startup-lease-expired:{recovery_ref}:{lease_id}:{lease_version}"
                            ),
                            attempt_id=str(row["attempt_id"]),
                        ),
                        ts_ms=ts_ms,
                        expected_aggregate=("TASK_LEASE", lease_id, lease_version),
                        expected_event_type=(
                            "TASK_LEASE_CANCELLED_RECOVERY"
                            if lease_target is LeaseStatus.CANCELLED
                            else "TASK_LEASE_EXPIRED"
                        ),
                        expected_attempt_id=str(row["attempt_id"]),
                    )
                attempt_id = str(row["attempt_id"])
                if cancel_attempt:
                    reservation_rows = conn.execute(
                        """
                        SELECT * FROM task_runtime_idempotency_reservation
                        WHERE attempt_id = ? AND status = 'RESERVED'
                        ORDER BY reservation_id
                        """,
                        (attempt_id,),
                    ).fetchall()
                    for reservation in reservation_rows:
                        updated = self._mark_operation_recovery_required(
                            conn,
                            reservation,
                            reason_code=(
                                "NODE_CANCEL_REQUESTED"
                                if node_cancel_requested
                                else "ATTEMPT_CANCEL_REQUESTED"
                            ),
                            ts_ms=ts_ms,
                        )
                        recovery_required_reservations.add(
                            str(updated["reservation_id"])
                        )
                else:
                    recovery_required_reservations.update(
                        self._mark_attempt_reservations_recovery_required(
                            conn,
                            attempt_id=attempt_id,
                            ts_ms=ts_ms,
                        )
                    )
                next_status = (
                    AttemptStatus.CANCELLED
                    if cancel_attempt
                    else AttemptStatus.RECOVERABLE
                )
                cursor = conn.execute(
                    """
                    UPDATE task_runtime_attempt
                    SET status = ?, owner_id = NULL,
                        completed_ts_ms = CASE WHEN ? = 'CANCELLED' THEN ? ELSE completed_ts_ms END,
                        status_version = status_version + 1, updated_ts_ms = ?
                    WHERE attempt_id = ? AND status_version = ?
                      AND status = ?
                    """,
                    (
                        next_status.value,
                        next_status.value,
                        ts_ms,
                        ts_ms,
                        attempt_id,
                        int(row["status_version"]),
                        current_status.value,
                    ),
                )
                self._require_cas(cursor, subject=f"Attempt {row['attempt_id']}")
                new_version = int(row["status_version"]) + 1
                self._insert_outbox(
                    conn,
                    OutboxMessage.create(
                        aggregate_type="TASK_ATTEMPT",
                        aggregate_id=attempt_id,
                        aggregate_version=new_version,
                        event_type=(
                            "TASK_ATTEMPT_CANCELLED_RECOVERY"
                            if cancel_attempt
                            else "TASK_ATTEMPT_RECOVERABLE"
                        ),
                        payload_ref=build_outbox_payload_ref(
                            aggregate_type="TASK_ATTEMPT",
                            aggregate_id=attempt_id,
                            aggregate_version=new_version,
                        ),
                        idempotency_key=(
                            f"startup-recovery:{recovery_ref}:{attempt_id}:{new_version}"
                        ),
                        attempt_id=attempt_id,
                    ),
                    ts_ms=ts_ms,
                    expected_aggregate=("TASK_ATTEMPT", attempt_id, new_version),
                    expected_event_type=(
                        "TASK_ATTEMPT_CANCELLED_RECOVERY"
                        if cancel_attempt
                        else "TASK_ATTEMPT_RECOVERABLE"
                    ),
                    expected_attempt_id=attempt_id,
                )
                if cancel_attempt:
                    cancelled.append(attempt_id)
                else:
                    recovered.append(attempt_id)

            node_rows = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE status = 'CANCEL_REQUESTED'
                ORDER BY graph_id, graph_revision, node_id
                """
            ).fetchall()
            for node in node_rows:
                node_aggregate_id = (
                    f"{node['graph_id']}@{int(node['graph_revision'])}/{node['node_id']}"
                )
                unresolved = conn.execute(
                    """
                    SELECT
                      EXISTS(
                        SELECT 1 FROM task_runtime_attempt AS a
                        WHERE a.graph_id = ? AND a.graph_revision = ? AND a.node_id = ?
                          AND a.status IN (
                            'CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE', 'CANCEL_REQUESTED'
                          )
                      ),
                      EXISTS(
                        SELECT 1
                        FROM task_runtime_lease AS l
                        JOIN task_runtime_attempt AS a ON a.attempt_id = l.attempt_id
                        WHERE a.graph_id = ? AND a.graph_revision = ? AND a.node_id = ?
                          AND l.status = 'ACTIVE'
                      ),
                      EXISTS(
                        SELECT 1
                        FROM task_runtime_idempotency_reservation AS r
                        JOIN task_runtime_attempt AS a ON a.attempt_id = r.attempt_id
                        WHERE a.graph_id = ? AND a.graph_revision = ? AND a.node_id = ?
                          AND r.status IN ('RESERVED', 'RECOVERY_REQUIRED')
                      )
                    """,
                    (
                        str(node["graph_id"]),
                        int(node["graph_revision"]),
                        str(node["node_id"]),
                        str(node["graph_id"]),
                        int(node["graph_revision"]),
                        str(node["node_id"]),
                        str(node["graph_id"]),
                        int(node["graph_revision"]),
                        str(node["node_id"]),
                    ),
                ).fetchone()
                if unresolved is not None and any(bool(value) for value in unresolved):
                    waiting_reconciliation_nodes.append(node_aggregate_id)
                    continue
                node_payload = _validated_node_payload(node)
                node_payload["status"] = TaskNodeStatus.CANCELLED.value
                node_payload["status_version"] = int(node["status_version"]) + 1
                node_cursor = conn.execute(
                    """
                    UPDATE task_runtime_node
                    SET status = 'CANCELLED', status_version = status_version + 1,
                        node_json = ?, updated_ts_ms = ?
                    WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                      AND status = 'CANCEL_REQUESTED' AND status_version = ?
                    """,
                    (
                        _dumps(node_payload),
                        ts_ms,
                        str(node["graph_id"]),
                        int(node["graph_revision"]),
                        str(node["node_id"]),
                        int(node["status_version"]),
                    ),
                )
                self._require_cas(node_cursor, subject=f"TaskNode {node['node_id']}")
                node_version = int(node["status_version"]) + 1
                self._insert_outbox(
                    conn,
                    OutboxMessage.create(
                        aggregate_type="TASK_NODE",
                        aggregate_id=node_aggregate_id,
                        aggregate_version=node_version,
                        event_type="TASK_NODE_CANCELLED_RECOVERY",
                        payload_ref=build_outbox_payload_ref(
                            aggregate_type="TASK_NODE",
                            aggregate_id=node_aggregate_id,
                            aggregate_version=node_version,
                        ),
                        idempotency_key=(
                            f"startup-node-cancelled:{recovery_ref}:"
                            f"{node_aggregate_id}:{node_version}"
                        ),
                    ),
                    ts_ms=ts_ms,
                    expected_aggregate=("TASK_NODE", node_aggregate_id, node_version),
                    expected_event_type="TASK_NODE_CANCELLED_RECOVERY",
                    expected_attempt_id=None,
                )
                cancelled_nodes.append(node_aggregate_id)

            reservation_rows = conn.execute(
                """
                SELECT * FROM task_runtime_idempotency_reservation
                WHERE status = 'RESERVED'
                ORDER BY reservation_id
                """
            ).fetchall()
            for reservation in reservation_rows:
                reason = self._operation_recovery_reason(conn, reservation, ts_ms)
                if reason is None:
                    continue
                updated = self._mark_operation_recovery_required(
                    conn,
                    reservation,
                    reason_code=reason,
                    ts_ms=ts_ms,
                )
                recovery_required_reservations.add(str(updated["reservation_id"]))
        return RecoveryReport(
            recovery_id=recovery_ref,
            recovered_attempt_ids=tuple(recovered),
            expired_lease_ids=tuple(expired),
            cancelled_attempt_ids=tuple(cancelled),
            cancelled_lease_ids=tuple(cancelled_leases),
            released_lease_ids=tuple(released_leases),
            cancelled_node_ids=tuple(cancelled_nodes),
            waiting_reconciliation_node_ids=tuple(waiting_reconciliation_nodes),
            recovery_required_reservation_ids=tuple(sorted(recovery_required_reservations)),
        )

    def list_recoverable_attempts(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM task_runtime_attempt
                WHERE status = 'RECOVERABLE'
                ORDER BY updated_ts_ms, attempt_id
                """
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_operation_reservation(self, reservation_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            return self._operation_record(
                self._require_operation_reservation(conn, reservation_id)
            )
        finally:
            conn.close()

    def list_recovery_required_operations(self) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM task_runtime_idempotency_reservation
                WHERE status = 'RECOVERY_REQUIRED'
                ORDER BY recovery_ts_ms, reservation_id
                """
            ).fetchall()
            return [self._operation_record(row) for row in rows]
        finally:
            conn.close()

    # -- Business idempotency -------------------------------------------------------

    def reserve_operation(
        self,
        *,
        reservation_id: str,
        business_scope: str,
        operation_fingerprint: str,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        ttl_ms: int,
        outbox: OutboxMessage,
    ) -> OperationReservationResult:
        reservation_ref = str(reservation_id or "").strip()
        scope = str(business_scope or "").strip()
        fingerprint = str(operation_fingerprint or "").strip()
        attempt_ref = str(attempt_id or "").strip()
        lease_ref = str(lease_id or "").strip()
        owner = str(owner_id or "").strip()
        if (
            not reservation_ref
            or not scope
            or not fingerprint
            or not attempt_ref
            or not lease_ref
            or not owner
            or int(fencing_token) < 1
            or int(ttl_ms) <= 0
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "reservation identity, active attempt lease binding, and positive ttl_ms are required",
            )
        ts_ms = self._now()
        with self._transaction() as conn:
            attempt, lease = self._assert_attempt_lease_binding(
                conn,
                attempt_id=attempt_ref,
                lease_id=lease_ref,
                owner_id=owner,
                fencing_token=fencing_token,
                now_ts_ms=ts_ms,
            )
            self._assert_attempt_operation_identity(
                attempt,
                business_scope=scope,
                operation_fingerprint=fingerprint,
            )
            existing = conn.execute(
                """
                SELECT * FROM task_runtime_idempotency_reservation
                WHERE business_scope = ? AND operation_fingerprint = ?
                """,
                (scope, fingerprint),
            ).fetchone()
            if existing is not None:
                self._operation_record(existing)
                recovery_reason = self._operation_recovery_reason(conn, existing, ts_ms)
                if recovery_reason is not None:
                    existing = self._mark_operation_recovery_required(
                        conn,
                        existing,
                        reason_code=recovery_reason,
                        ts_ms=ts_ms,
                    )
                return OperationReservationResult(
                    acquired=False,
                    reservation=self._operation_record(existing),
                )
            expires_ts_ms = min(ts_ms + int(ttl_ms), int(lease["expires_ts_ms"]))
            self._require_outbox_attempt(outbox, attempt_ref)
            try:
                conn.execute(
                    """
                    INSERT INTO task_runtime_idempotency_reservation(
                        reservation_id, business_scope, operation_fingerprint,
                        attempt_id, lease_id, owner_id, fencing_token, expires_ts_ms,
                        status, status_version, receipt_ref, reconciliation_proof_ref,
                        reconciliation_proof_digest, reconciliation_proof_key_id,
                        reconciliation_proof_signature, reconciler_actor_id,
                        reconciliation_recovery_version, recovery_reason_code,
                        recovery_ts_ms, created_ts_ms, updated_ts_ms
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', 0,
                        NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?
                    )
                    """,
                    (
                        reservation_ref,
                        scope,
                        fingerprint,
                        attempt_ref,
                        lease_ref,
                        owner,
                        int(fencing_token),
                        expires_ts_ms,
                        ts_ms,
                        ts_ms,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    f"operation reservation identity conflict: {reservation_ref}",
                ) from exc
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=("OPERATION_RESERVATION", reservation_ref, 0),
                expected_event_type="OPERATION_RESERVED",
                expected_attempt_id=attempt_ref,
            )
            created = conn.execute(
                """
                SELECT * FROM task_runtime_idempotency_reservation
                WHERE reservation_id = ?
                """,
                (reservation_ref,),
            ).fetchone()
            return OperationReservationResult(
                acquired=True,
                reservation=self._operation_record(created),
            )

    def commit_operation(
        self,
        reservation_id: str,
        *,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        receipt_ref: str,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        receipt = self._validated_reference(receipt_ref, field_name="receipt_ref")
        ts_ms = self._now()
        recovery_required = False
        with self._transaction() as conn:
            row = self._require_operation_reservation(conn, reservation_id)
            self._operation_record(row)
            recovery_reason = self._operation_recovery_reason(conn, row, ts_ms)
            if recovery_reason is not None:
                self._mark_operation_recovery_required(
                    conn,
                    row,
                    reason_code=recovery_reason,
                    ts_ms=ts_ms,
                )
                recovery_required = True
            else:
                self._assert_operation_binding(
                    conn,
                    row,
                    attempt_id=attempt_id,
                    lease_id=lease_id,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                    now_ts_ms=ts_ms,
                )
            if recovery_required:
                updated = self._require_operation_reservation(conn, reservation_id)
            else:
                self._require_version(
                    row,
                    expected_version,
                    subject=f"OperationReservation {reservation_id}",
                )
                if str(row["status"]) != IdempotencyStatus.RESERVED.value:
                    raise TaskStoreError(
                        TASK_STORE_INVALID,
                        f"reservation {reservation_id} is not RESERVED",
                    )
                attempt_ref = str(attempt_id or "").strip()
                self._require_outbox_attempt(outbox, attempt_ref)
                cursor = conn.execute(
                    """
                    UPDATE task_runtime_idempotency_reservation
                    SET status = 'COMMITTED', receipt_ref = ?,
                        status_version = status_version + 1, updated_ts_ms = ?
                    WHERE reservation_id = ? AND status_version = ? AND status = 'RESERVED'
                      AND attempt_id = ? AND lease_id = ? AND owner_id = ?
                      AND fencing_token = ? AND expires_ts_ms > ?
                    """,
                    (
                        receipt,
                        ts_ms,
                        str(reservation_id),
                        int(expected_version),
                        attempt_ref,
                        str(lease_id or "").strip(),
                        str(owner_id or "").strip(),
                        int(fencing_token),
                        ts_ms,
                    ),
                )
                self._require_cas(cursor, subject=f"OperationReservation {reservation_id}")
                self._insert_outbox(
                    conn,
                    outbox,
                    ts_ms=ts_ms,
                    expected_aggregate=(
                        "OPERATION_RESERVATION",
                        str(reservation_id),
                        int(expected_version) + 1,
                    ),
                    expected_event_type="OPERATION_COMMITTED",
                    expected_attempt_id=attempt_ref,
                )
                updated = self._require_operation_reservation(conn, reservation_id)
        if recovery_required:
            raise TaskStoreError(
                TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
                f"reservation {reservation_id} expired or lost its owner lease; reconcile before commit",
            )
        return self._operation_record(updated)

    def renew_operation_reservation(
        self,
        reservation_id: str,
        *,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        ttl_ms: int,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        if int(ttl_ms) <= 0:
            raise TaskStoreError(TASK_STORE_INVALID, "positive ttl_ms is required")
        ts_ms = self._now()
        recovery_required = False
        with self._transaction() as conn:
            row = self._require_operation_reservation(conn, reservation_id)
            self._operation_record(row)
            recovery_reason = self._operation_recovery_reason(conn, row, ts_ms)
            if recovery_reason is not None:
                self._mark_operation_recovery_required(
                    conn,
                    row,
                    reason_code=recovery_reason,
                    ts_ms=ts_ms,
                )
                recovery_required = True
                updated = self._require_operation_reservation(conn, reservation_id)
            else:
                _attempt, lease = self._assert_operation_binding(
                    conn,
                    row,
                    attempt_id=attempt_id,
                    lease_id=lease_id,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                    now_ts_ms=ts_ms,
                )
                self._require_version(
                    row,
                    expected_version,
                    subject=f"OperationReservation {reservation_id}",
                )
                expires_ts_ms = min(ts_ms + int(ttl_ms), int(lease["expires_ts_ms"]))
                attempt_ref = str(attempt_id or "").strip()
                self._require_outbox_attempt(outbox, attempt_ref)
                cursor = conn.execute(
                    """
                    UPDATE task_runtime_idempotency_reservation
                    SET expires_ts_ms = ?, status_version = status_version + 1,
                        updated_ts_ms = ?
                    WHERE reservation_id = ? AND status = 'RESERVED'
                      AND status_version = ? AND attempt_id = ? AND lease_id = ?
                      AND owner_id = ? AND fencing_token = ? AND expires_ts_ms > ?
                    """,
                    (
                        expires_ts_ms,
                        ts_ms,
                        str(reservation_id),
                        int(expected_version),
                        attempt_ref,
                        str(lease_id or "").strip(),
                        str(owner_id or "").strip(),
                        int(fencing_token),
                        ts_ms,
                    ),
                )
                self._require_cas(cursor, subject=f"OperationReservation {reservation_id}")
                self._insert_outbox(
                    conn,
                    outbox,
                    ts_ms=ts_ms,
                    expected_aggregate=(
                        "OPERATION_RESERVATION",
                        str(reservation_id),
                        int(expected_version) + 1,
                    ),
                    expected_event_type="OPERATION_RENEWED",
                    expected_attempt_id=attempt_ref,
                )
                updated = self._require_operation_reservation(conn, reservation_id)
        if recovery_required:
            raise TaskStoreError(
                TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
                f"reservation {reservation_id} requires reconciliation before renewal",
            )
        return self._operation_record(updated)

    def recover_operation_reservation(
        self,
        reservation_id: str,
        *,
        action: IdempotencyRecoveryAction | str,
        attempt_id: str | None = None,
        lease_id: str | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
        expected_version: int,
        outbox: OutboxMessage,
        reconciler_actor_id: str,
        downstream_receipt_ref: str | None = None,
        attestation_ref: str | None = None,
        attestation_key_id: str | None = None,
        attestation_signature: str | None = None,
        ttl_ms: int | None = None,
    ) -> dict[str, Any]:
        try:
            recovery_action = IdempotencyRecoveryAction(str(action).strip().upper())
        except ValueError as exc:
            raise TaskStoreError(TASK_STORE_INVALID, f"unsupported recovery action: {action}") from exc
        reconciler_actor = str(reconciler_actor_id or "").strip()
        if not reconciler_actor:
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_PROOF_INVALID,
                "reconciler_actor_id is required for recovery",
            )
        receipt: str | None = None
        outcome = (
            "SIDE_EFFECT_CONFIRMED"
            if recovery_action is IdempotencyRecoveryAction.COMMIT
            else "NO_SIDE_EFFECT"
        )
        if recovery_action is IdempotencyRecoveryAction.COMMIT:
            receipt = self._validated_reference(
                downstream_receipt_ref,
                field_name="downstream_receipt_ref",
            )
        if not str(attestation_ref or "").strip():
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_PROOF_REQUIRED,
                f"{recovery_action.value} requires a signed reconciliation attestation",
            )
        proof = self._validated_reference(
            attestation_ref,
            field_name="attestation_ref",
        )
        proof_key_id = str(attestation_key_id or "").strip()
        proof_signature = str(attestation_signature or "").strip()
        if not proof_key_id or not re.fullmatch(
            r"ed25519:[A-Za-z0-9_-]{86}==",
            proof_signature,
        ):
            raise TaskStoreError(
                TASK_STORE_RECONCILIATION_PROOF_INVALID,
                "reconciliation attestation requires key_id and Ed25519 signature",
            )
        if recovery_action is IdempotencyRecoveryAction.REACQUIRE and int(ttl_ms or 0) <= 0:
            raise TaskStoreError(TASK_STORE_INVALID, "REACQUIRE requires positive ttl_ms")

        ts_ms = self._now()
        with self._transaction() as conn:
            row = self._require_operation_reservation(conn, reservation_id)
            self._operation_record(row)
            self._require_version(row, expected_version, subject=f"OperationReservation {reservation_id}")
            if str(row["status"]) != IdempotencyStatus.RECOVERY_REQUIRED.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"reservation {reservation_id} is not RECOVERY_REQUIRED",
                )
            proof_binding = build_reconciliation_attestation_binding(
                reservation_id=str(row["reservation_id"]),
                business_scope=str(row["business_scope"]),
                operation_fingerprint=str(row["operation_fingerprint"]),
                recovery_version=int(row["status_version"]),
                reconciler_actor_id=reconciler_actor,
                attestation_ref=proof,
                recovery_action=recovery_action.value,
                reconciliation_outcome=outcome,
                receipt_ref=receipt,
            )
            proof_digest = f"sha256:{hashlib.sha256(proof_binding).hexdigest()}"
            verifier = self._reconciliation_proof_verifier
            if verifier is None:
                raise TaskStoreError(
                    TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
                    "no reconciliation proof verifier is configured",
                )
            if not verifier.has_key(proof_key_id):
                raise TaskStoreError(
                    TASK_STORE_RECONCILIATION_PROOF_INVALID,
                    f"unknown reconciliation proof key_id: {proof_key_id}",
                )
            if not verifier.verify(
                key_id=proof_key_id,
                message=proof_binding,
                signature=proof_signature,
            ):
                raise TaskStoreError(
                    TASK_STORE_RECONCILIATION_PROOF_INVALID,
                    "reconciliation attestation signature is invalid or binding was tampered",
                )

            original_attempt_ref = str(row["attempt_id"] or "").strip() or None
            if recovery_action is IdempotencyRecoveryAction.REACQUIRE:
                attempt, lease = self._assert_attempt_lease_binding(
                    conn,
                    attempt_id=str(attempt_id or ""),
                    lease_id=str(lease_id or ""),
                    owner_id=str(owner_id or ""),
                    fencing_token=int(fencing_token or 0),
                    now_ts_ms=ts_ms,
                )
                self._assert_attempt_operation_identity(
                    attempt,
                    business_scope=str(row["business_scope"]),
                    operation_fingerprint=str(row["operation_fingerprint"]),
                )
                mutation_attempt_ref = str(attempt_id or "").strip()
                mutation_lease_ref = str(lease_id or "").strip()
                mutation_owner = str(owner_id or "").strip()
                mutation_fence = int(fencing_token or 0)
                expires_ts_ms = min(
                    ts_ms + int(ttl_ms or 0),
                    int(lease["expires_ts_ms"]),
                )
                outbox_attempt_ref: str | None = mutation_attempt_ref
            else:
                supplied_attempt_ref = str(attempt_id or "").strip() or None
                if supplied_attempt_ref is not None and supplied_attempt_ref != original_attempt_ref:
                    raise TaskStoreError(
                        TASK_STORE_OWNERSHIP_CONFLICT,
                        "terminal reconciliation attempt_id does not match the original reservation",
                    )
                mutation_attempt_ref = row["attempt_id"]
                mutation_lease_ref = row["lease_id"]
                mutation_owner = row["owner_id"]
                mutation_fence = row["fencing_token"]
                expires_ts_ms = int(row["expires_ts_ms"])
                outbox_attempt_ref = original_attempt_ref
            self._require_outbox_attempt(outbox, outbox_attempt_ref)
            next_status = {
                IdempotencyRecoveryAction.COMMIT: IdempotencyStatus.COMMITTED.value,
                IdempotencyRecoveryAction.RELEASE: IdempotencyStatus.RELEASED.value,
                IdempotencyRecoveryAction.REACQUIRE: IdempotencyStatus.RESERVED.value,
            }[recovery_action]
            cursor = conn.execute(
                """
                UPDATE task_runtime_idempotency_reservation
                SET attempt_id = ?, lease_id = ?, owner_id = ?, fencing_token = ?,
                    expires_ts_ms = ?, status = ?, receipt_ref = ?,
                    reconciliation_proof_ref = ?, reconciliation_proof_digest = ?,
                    reconciliation_proof_key_id = ?, reconciliation_proof_signature = ?,
                    reconciliation_action = ?, reconciliation_outcome = ?,
                    reconciler_actor_id = ?,
                    reconciliation_recovery_version = ?,
                    status_version = status_version + 1, updated_ts_ms = ?
                WHERE reservation_id = ? AND status_version = ?
                  AND status = 'RECOVERY_REQUIRED'
                  AND business_scope = ? AND operation_fingerprint = ?
                """,
                (
                    mutation_attempt_ref,
                    mutation_lease_ref,
                    mutation_owner,
                    mutation_fence,
                    expires_ts_ms,
                    next_status,
                    receipt,
                    proof,
                    proof_digest,
                    proof_key_id,
                    proof_signature,
                    recovery_action.value,
                    outcome,
                    reconciler_actor,
                    int(expected_version),
                    ts_ms,
                    str(reservation_id),
                    int(expected_version),
                    str(row["business_scope"]),
                    str(row["operation_fingerprint"]),
                ),
            )
            self._require_cas(cursor, subject=f"OperationReservation {reservation_id}")
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "OPERATION_RESERVATION",
                    str(reservation_id),
                    int(expected_version) + 1,
                ),
                expected_event_type=f"OPERATION_RECOVERY_{recovery_action.value}",
                expected_attempt_id=outbox_attempt_ref,
            )
            return self._operation_record(
                self._require_operation_reservation(conn, reservation_id)
            )

    # -- Budget reservation, usage, and reconciliation -----------------------------

    def create_budget_reservation(
        self,
        reservation: BudgetReservation,
        *,
        graph_id: str | None = None,
        graph_revision: int | None = None,
        node_id: str | None = None,
        attempt_id: str | None = None,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        if not isinstance(reservation, BudgetReservation):
            raise TaskStoreError(TASK_STORE_INVALID, "reservation must be a BudgetReservation")
        reservation = BudgetReservation.from_mapping(reservation.to_dict())
        ts_ms = self._now()
        with self._transaction() as conn:
            if attempt_id is not None:
                self._require_attempt(conn, attempt_id)
            consumed = reservation.consumed.to_dict()
            conn.execute(
                """
                INSERT INTO task_runtime_budget_reservation(
                    budget_id, scope, graph_id, graph_revision, node_id, attempt_id,
                    limits_json, reserved_json, consumed_json, attempted_consumed_json,
                    exceeded_dimensions_json, status, status_version,
                    created_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?)
                """,
                (
                    reservation.budget_id,
                    reservation.scope,
                    str(graph_id) if graph_id is not None else None,
                    int(graph_revision) if graph_revision is not None else None,
                    str(node_id) if node_id is not None else None,
                    str(attempt_id) if attempt_id is not None else None,
                    _dumps(reservation.limits.to_dict()),
                    _dumps(reservation.reserved.to_dict()),
                    _dumps(consumed),
                    _dumps(consumed),
                    reservation.status.value,
                    reservation.status_version,
                    ts_ms,
                    ts_ms,
                ),
            )
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "BUDGET_RESERVATION",
                    reservation.budget_id,
                    reservation.status_version,
                ),
                expected_event_type="BUDGET_RESERVATION_CREATED",
                expected_attempt_id=(str(attempt_id) if attempt_id is not None else None),
            )
            return self._budget_record(self._require_budget(conn, reservation.budget_id))

    def transition_budget(
        self,
        budget_id: str,
        *,
        expected_version: int,
        target_status: BudgetReservationStatus | str,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        ts_ms = self._now()
        with self._transaction() as conn:
            row = self._require_budget(conn, budget_id)
            self._require_version(row, expected_version, subject=f"BudgetReservation {budget_id}")
            target = validate_budget_transition(str(row["status"]), target_status)
            cursor = conn.execute(
                """
                UPDATE task_runtime_budget_reservation
                SET status = ?, status_version = status_version + 1, updated_ts_ms = ?
                WHERE budget_id = ? AND status_version = ?
                """,
                (target.value, ts_ms, str(budget_id), int(expected_version)),
            )
            self._require_cas(cursor, subject=f"BudgetReservation {budget_id}")
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "BUDGET_RESERVATION",
                    str(budget_id),
                    int(expected_version) + 1,
                ),
                expected_event_type=f"BUDGET_RESERVATION_{target.value}",
                expected_attempt_id=(str(row["attempt_id"]) if row["attempt_id"] is not None else None),
            )
            return self._budget_record(self._require_budget(conn, budget_id))

    def consume_budget(
        self,
        budget_id: str,
        *,
        delta: BudgetAmount,
        expected_version: int,
        outbox: OutboxMessage,
    ) -> BudgetConsumptionResult:
        if not isinstance(delta, BudgetAmount):
            raise TaskStoreError(TASK_STORE_INVALID, "delta must be a BudgetAmount")
        normalized_delta = BudgetAmount.from_mapping(delta.to_dict())
        ts_ms = self._now()
        with self._transaction() as conn:
            row = self._require_budget(conn, budget_id)
            self._require_version(row, expected_version, subject=f"BudgetReservation {budget_id}")
            if str(row["status"]) != BudgetReservationStatus.ACTIVE.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"budget {budget_id} must be ACTIVE before consumption",
                )
            consumed = _amount_from_json(row["consumed_json"], field_name="consumed_json")
            reserved = _amount_from_json(row["reserved_json"], field_name="reserved_json")
            attempted = _add_amounts(consumed, normalized_delta)
            exceeded_dimensions = tuple(
                key
                for key, value in attempted.to_dict().items()
                if value > reserved.to_dict()[key]
            )
            if exceeded_dimensions:
                target_status = BudgetReservationStatus.EXCEEDED.value
                next_consumed = consumed
            else:
                target_status = BudgetReservationStatus.ACTIVE.value
                next_consumed = attempted
            cursor = conn.execute(
                """
                UPDATE task_runtime_budget_reservation
                SET consumed_json = ?, attempted_consumed_json = ?,
                    exceeded_dimensions_json = ?, status = ?,
                    status_version = status_version + 1, updated_ts_ms = ?
                WHERE budget_id = ? AND status_version = ? AND status = 'ACTIVE'
                """,
                (
                    _dumps(next_consumed.to_dict()),
                    _dumps(attempted.to_dict()),
                    _dumps(list(exceeded_dimensions)),
                    target_status,
                    ts_ms,
                    str(budget_id),
                    int(expected_version),
                ),
            )
            self._require_cas(cursor, subject=f"BudgetReservation {budget_id}")
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "BUDGET_RESERVATION",
                    str(budget_id),
                    int(expected_version) + 1,
                ),
                expected_event_type=(
                    "BUDGET_EXCEEDED" if exceeded_dimensions else "BUDGET_CONSUMED"
                ),
                expected_attempt_id=(str(row["attempt_id"]) if row["attempt_id"] is not None else None),
            )
            updated = self._budget_record(self._require_budget(conn, budget_id))
            return BudgetConsumptionResult(
                accepted=not exceeded_dimensions,
                reason_code=TASK_STORE_BUDGET_EXCEEDED if exceeded_dimensions else None,
                reservation=updated,
                exceeded_dimensions=exceeded_dimensions,
            )

    def reconcile_budget(self, budget_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            record = self._budget_record(self._require_budget(conn, budget_id))
        finally:
            conn.close()
        reserved = BudgetAmount.from_mapping(record["reserved"])
        consumed = BudgetAmount.from_mapping(record["consumed"])
        remaining = {
            key: max(0, reserved.to_dict()[key] - consumed.to_dict()[key])
            for key in reserved.to_dict()
        }
        return {
            **record,
            "remaining": remaining,
            "balanced": not consumed.exceeds(reserved),
            "denied_usage_preserved": bool(record["exceeded_dimensions"]),
        }

    # -- Transactional outbox -------------------------------------------------------

    def list_pending_outbox(
        self,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        ts_ms = self._now()
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM task_runtime_outbox
                WHERE status = 'PENDING' AND available_ts_ms <= ?
                ORDER BY created_ts_ms, outbox_id
                LIMIT ?
                """,
                (ts_ms, max(1, int(limit))),
            ).fetchall()
            return [_outbox_record(row) for row in rows]
        finally:
            conn.close()

    def mark_outbox_published(
        self,
        outbox_id: str,
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        ts_ms = self._now()
        with self._transaction() as conn:
            row = self._require_outbox(conn, outbox_id)
            self._require_version(row, expected_version, subject=f"Outbox {outbox_id}")
            if str(row["status"]) != "PENDING":
                raise TaskStoreError(TASK_STORE_INVALID, f"outbox {outbox_id} is not PENDING")
            cursor = conn.execute(
                """
                UPDATE task_runtime_outbox
                SET status = 'PUBLISHED', published_ts_ms = ?,
                    status_version = status_version + 1
                WHERE outbox_id = ? AND status_version = ? AND status = 'PENDING'
                """,
                (ts_ms, str(outbox_id), int(expected_version)),
            )
            self._require_cas(cursor, subject=f"Outbox {outbox_id}")
            return _outbox_record(self._require_outbox(conn, outbox_id))

    # -- Legacy AgentJobRegistry shadow --------------------------------------------

    def mirror_legacy_job(
        self,
        job: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(job, Mapping):
            raise TaskStoreError(TASK_STORE_INVALID, "legacy job must be a mapping")
        raw_job = dict(job)
        job_id = str(raw_job.get("job_id") or "").strip()
        if not job_id:
            raise TaskStoreError(TASK_STORE_INVALID, "legacy job_id is required")
        snapshot = _legacy_job_projection(raw_job)
        ts_ms = self._now()
        graph_id = f"legacy-agent-job::{job_id}"
        write_outbox = True
        with self._transaction() as conn:
            existing = conn.execute(
                """
                SELECT * FROM task_runtime_graph
                WHERE source_kind = 'LEGACY_AGENT_JOB' AND source_ref = ?
                """,
                (job_id,),
            ).fetchone()
            if existing is None:
                work_order = raw_job.get("work_order")
                work_order_data = dict(work_order) if isinstance(work_order, Mapping) else {}
                root_work_order_id = str(work_order_data.get("work_order_id") or job_id).strip()
                node = TaskNode(
                    node_id="legacy-job",
                    dependencies=(),
                    route="legacy:agent-job",
                    input_refs=(),
                    output_contract={"type": "legacy_shadow"},
                    retry_policy={},
                    risk_level=1,
                    acceptance_criteria=(),
                    status=TaskNodeStatus.CREATED,
                    status_version=0,
                )
                graph = TaskGraph(
                    graph_id=graph_id,
                    root_work_order_id=root_work_order_id,
                    revision=1,
                    planner_ref="legacy://AgentJobRegistry",
                    status=TaskGraphStatus.CREATED,
                    created_at=int(raw_job.get("created_at_ms") or ts_ms),
                    nodes=(node,),
                )
                graph.validate()
                conn.execute(
                    """
                    INSERT INTO task_runtime_graph(
                        graph_id, revision, root_work_order_id, planner_ref, status,
                        status_version, graph_json, source_kind, source_ref,
                        legacy_snapshot_json, created_ts_ms, updated_ts_ms
                    ) VALUES (?, 1, ?, ?, 'CREATED', 0, ?, 'LEGACY_AGENT_JOB', ?, ?, ?, ?)
                    """,
                    (
                        graph_id,
                        root_work_order_id,
                        graph.planner_ref,
                        _dumps(graph.to_dict()),
                        job_id,
                        _dumps(snapshot),
                        ts_ms,
                        ts_ms,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO task_runtime_node(
                        graph_id, graph_revision, node_id, status, status_version,
                        node_json, cancellation_requested_ts_ms, created_ts_ms, updated_ts_ms
                    ) VALUES (?, 1, 'legacy-job', 'CREATED', 0, ?, NULL, ?, ?)
                    """,
                    (graph_id, _dumps(node.to_dict()), ts_ms, ts_ms),
                )
                next_version = 0
                event_type = "LEGACY_JOB_SHADOW_CREATED"
            else:
                node_rows = conn.execute(
                    """
                    SELECT * FROM task_runtime_node
                    WHERE graph_id = ? AND graph_revision = 1
                    ORDER BY node_id
                    """,
                    (graph_id,),
                ).fetchall()
                _graph_record(existing, node_rows)
                durable_snapshot = _validated_legacy_snapshot(
                    existing["legacy_snapshot_json"],
                    expected_job_id=job_id,
                )
                incoming_updated_at = int(snapshot["updated_at_ms"])
                durable_updated_at = int(durable_snapshot["updated_at_ms"])
                if incoming_updated_at < durable_updated_at:
                    raise TaskStoreError(
                        TASK_STORE_CAS_CONFLICT,
                        "legacy shadow rejected an out-of-order older snapshot: "
                        f"incoming={incoming_updated_at}, durable={durable_updated_at}",
                    )
                if incoming_updated_at == durable_updated_at:
                    if _payload_digest(snapshot) != _payload_digest(durable_snapshot):
                        raise TaskStoreError(
                            TASK_STORE_CAS_CONFLICT,
                            "legacy shadow has conflicting content at the same updated_at_ms",
                        )
                    write_outbox = False
                    next_version = int(existing["status_version"])
                    event_type = ""
                else:
                    expected_version = int(existing["status_version"])
                    cursor = conn.execute(
                        """
                        UPDATE task_runtime_graph
                        SET legacy_snapshot_json = ?, status_version = status_version + 1,
                            updated_ts_ms = ?
                        WHERE graph_id = ? AND revision = 1 AND status_version = ?
                        """,
                        (_dumps(snapshot), ts_ms, graph_id, expected_version),
                    )
                    self._require_cas(cursor, subject=f"legacy shadow {job_id}")
                    next_version = expected_version + 1
                    event_type = "LEGACY_JOB_SHADOW_UPDATED"
            if write_outbox:
                self._insert_outbox(
                    conn,
                    OutboxMessage.create(
                        aggregate_type="LEGACY_AGENT_JOB_SHADOW",
                        aggregate_id=job_id,
                        aggregate_version=next_version,
                        event_type=event_type,
                        payload_ref=build_outbox_payload_ref(
                            aggregate_type="LEGACY_AGENT_JOB_SHADOW",
                            aggregate_id=job_id,
                            aggregate_version=next_version,
                        ),
                        idempotency_key=f"legacy-shadow:{job_id}:{next_version}",
                    ),
                    ts_ms=ts_ms,
                    expected_aggregate=("LEGACY_AGENT_JOB_SHADOW", job_id, next_version),
                    expected_event_type=event_type,
                    expected_attempt_id=None,
                )
        shadow = self.get_legacy_job_shadow(job_id)
        assert shadow is not None
        return shadow

    def get_legacy_job_shadow(self, job_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM task_runtime_graph
                WHERE source_kind = 'LEGACY_AGENT_JOB' AND source_ref = ?
                """,
                (str(job_id),),
            ).fetchone()
            if row is None:
                return None
            return {
                "job": _validated_legacy_snapshot(
                    row["legacy_snapshot_json"],
                    expected_job_id=str(row["source_ref"] or ""),
                ),
                "graph_id": str(row["graph_id"]),
                "revision": int(row["revision"]),
                "shadow_version": int(row["status_version"]),
                "updated_at_ms": int(row["updated_ts_ms"]),
            }
        finally:
            conn.close()

    def reconcile_legacy_job(
        self,
        job_id: str,
        legacy_job: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if legacy_job is not None and not isinstance(legacy_job, Mapping):
            raise TaskStoreError(TASK_STORE_INVALID, "legacy job must be a mapping or null")
        legacy = _legacy_job_projection(legacy_job) if isinstance(legacy_job, Mapping) else None
        shadow = self.get_legacy_job_shadow(job_id)
        durable = dict(shadow["job"]) if shadow is not None else None
        if legacy is None and durable is None:
            classification = "BOTH_MISSING"
            differences: list[str] = []
        elif legacy is None:
            classification = "LEGACY_MISSING"
            differences = ["job"]
        elif durable is None:
            classification = "DURABLE_SHADOW_MISSING"
            differences = ["job"]
        else:
            keys = sorted(set(legacy) | set(durable))
            differences = [key for key in keys if legacy.get(key) != durable.get(key)]
            classification = "MATCH" if not differences else "FIELD_MISMATCH"
        return {
            "job_id": str(job_id),
            "authority": "LEGACY",
            "classification": classification,
            "match": classification == "MATCH",
            "differences": differences,
            "legacy": legacy,
            "durable_shadow": durable,
            "shadow_version": shadow.get("shadow_version") if shadow else None,
        }

    # -- Internal row helpers -------------------------------------------------------

    def _insert_outbox(
        self,
        conn: sqlite3.Connection,
        message: OutboxMessage,
        *,
        ts_ms: int,
        expected_aggregate: tuple[str, str, int],
        expected_event_type: str,
        expected_attempt_id: str | None,
    ) -> None:
        if not isinstance(message, OutboxMessage):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "every durable state mutation requires an OutboxMessage",
            )
        message.validate()
        if message.available_ts_ms is not None:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "mutation outbox availability is assigned only by the trusted store clock",
            )
        actual_aggregate = (
            message.aggregate_type,
            message.aggregate_id,
            int(message.aggregate_version),
        )
        normalized_expected = (
            str(expected_aggregate[0]),
            str(expected_aggregate[1]),
            int(expected_aggregate[2]),
        )
        if actual_aggregate != normalized_expected:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "outbox aggregate must identify the state version committed in this transaction: "
                f"expected {normalized_expected}, got {actual_aggregate}",
            )
        normalized_event_type = str(expected_event_type or "").strip()
        if not normalized_event_type or message.event_type != normalized_event_type:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "outbox event_type does not describe the committed mutation: "
                f"expected {normalized_event_type}, got {message.event_type}",
            )
        normalized_attempt_id = str(expected_attempt_id or "").strip() or None
        if message.attempt_id != normalized_attempt_id:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "outbox attempt_id does not match the committed mutation: "
                f"expected {normalized_attempt_id}, got {message.attempt_id}",
            )
        try:
            conn.execute(
                """
                INSERT INTO task_runtime_outbox(
                    outbox_id, aggregate_type, aggregate_id, aggregate_version,
                    event_type, payload_ref, evidence_refs_json, attempt_id,
                    idempotency_key, status, available_ts_ms, created_ts_ms,
                    published_ts_ms, status_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, NULL, 0)
                """,
                (
                    message.outbox_id,
                    message.aggregate_type,
                    message.aggregate_id,
                    message.aggregate_version,
                    message.event_type,
                    message.payload_ref,
                    _dumps(list(message.evidence_refs)),
                    message.attempt_id,
                    message.idempotency_key,
                    ts_ms,
                    ts_ms,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise TaskStoreError(
                TASK_STORE_OUTBOX_CONFLICT,
                f"outbox identity/idempotency conflict: {message.idempotency_key}",
            ) from exc

    @staticmethod
    def _validated_reference(value: Any, *, field_name: str) -> str:
        reference = str(value or "").strip()
        if not _DURABLE_REFERENCE_PATTERN.fullmatch(reference):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                f"{field_name} must be a durable scheme-qualified reference",
            )
        return reference

    @staticmethod
    def _require_outbox_attempt(
        message: OutboxMessage,
        attempt_id: str | None,
    ) -> None:
        expected_attempt_id = str(attempt_id or "").strip() or None
        if not isinstance(message, OutboxMessage) or message.attempt_id != expected_attempt_id:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "operation reservation outbox must bind the same attempt_id",
            )

    def _assert_attempt_lease_binding(
        self,
        conn: sqlite3.Connection,
        *,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        now_ts_ms: int,
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        attempt_ref = str(attempt_id or "").strip()
        lease_ref = str(lease_id or "").strip()
        owner = str(owner_id or "").strip()
        attempt = self._require_attempt(conn, attempt_ref)
        self._assert_node_not_cancelled_for_attempt(conn, attempt)
        lease = self._require_lease(conn, lease_ref)
        active = self._active_lease(conn, attempt_ref)
        if (
            str(lease["attempt_id"]) != attempt_ref
            or active is None
            or str(active["lease_id"]) != lease_ref
            or str(attempt["owner_id"] or "") != owner
            or str(attempt["status"])
            not in {AttemptStatus.LEASED.value, AttemptStatus.RUNNING.value}
        ):
            raise TaskStoreError(
                TASK_STORE_OWNERSHIP_CONFLICT,
                f"attempt {attempt_ref} is not owned by active lease {lease_ref}",
            )
        self._assert_lease_owner(
            lease,
            owner_id=owner,
            fencing_token=fencing_token,
            now_ts_ms=now_ts_ms,
        )
        return attempt, lease

    def _assert_node_not_cancelled_for_attempt(
        self,
        conn: sqlite3.Connection,
        attempt: sqlite3.Row,
    ) -> None:
        node = self._require_node(
            conn,
            str(attempt["graph_id"]),
            int(attempt["graph_revision"]),
            str(attempt["node_id"]),
        )
        if str(node["status"]) in {
            TaskNodeStatus.CANCEL_REQUESTED.value,
            TaskNodeStatus.CANCELLED.value,
        }:
            raise TaskStoreError(
                TASK_STORE_CANCELLATION_CONFLICT,
                f"node {attempt['node_id']} is cancelling or cancelled",
            )

    @staticmethod
    def _assert_attempt_operation_identity(
        attempt: sqlite3.Row,
        *,
        business_scope: str,
        operation_fingerprint: str,
    ) -> None:
        if (
            str(attempt["operation_scope"] or "") != str(business_scope)
            or str(attempt["operation_fingerprint"] or "")
            != str(operation_fingerprint)
        ):
            raise TaskStoreError(
                TASK_STORE_OWNERSHIP_CONFLICT,
                "attempt operation scope/fingerprint does not match reservation",
            )

    def _assert_operation_binding(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        now_ts_ms: int,
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        if (
            str(row["attempt_id"]) != str(attempt_id or "").strip()
            or str(row["lease_id"]) != str(lease_id or "").strip()
            or str(row["owner_id"]) != str(owner_id or "").strip()
            or int(row["fencing_token"]) != int(fencing_token)
        ):
            raise TaskStoreError(
                TASK_STORE_OWNERSHIP_CONFLICT,
                "reservation attempt/lease owner/fencing binding does not match",
            )
        return self._assert_attempt_lease_binding(
            conn,
            attempt_id=attempt_id,
            lease_id=lease_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            now_ts_ms=now_ts_ms,
        )

    @staticmethod
    def _operation_recovery_reason(
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        now_ts_ms: int,
    ) -> str | None:
        if str(row["status"]) != IdempotencyStatus.RESERVED.value:
            return None
        if int(row["expires_ts_ms"]) <= int(now_ts_ms):
            return "IDEMPOTENCY_RESERVATION_EXPIRED"
        lease = conn.execute(
            "SELECT * FROM task_runtime_lease WHERE lease_id = ?",
            (str(row["lease_id"]),),
        ).fetchone()
        if (
            lease is None
            or str(lease["status"]) != LeaseStatus.ACTIVE.value
            or int(lease["expires_ts_ms"]) <= int(now_ts_ms)
        ):
            return "IDEMPOTENCY_OWNER_LEASE_INACTIVE"
        if (
            str(lease["attempt_id"]) != str(row["attempt_id"])
            or str(lease["owner_id"]) != str(row["owner_id"])
            or int(lease["fencing_token"]) != int(row["fencing_token"])
        ):
            return "IDEMPOTENCY_FENCE_MISMATCH"
        return None

    def _mark_operation_recovery_required(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        reason_code: str,
        ts_ms: int,
    ) -> sqlite3.Row:
        if str(row["status"]) != IdempotencyStatus.RESERVED.value:
            return row
        reason = str(reason_code or "").strip()
        if not reason:
            raise TaskStoreError(TASK_STORE_INVALID, "idempotency recovery reason is required")
        current_version = int(row["status_version"])
        cursor = conn.execute(
            """
            UPDATE task_runtime_idempotency_reservation
            SET status = 'RECOVERY_REQUIRED', recovery_reason_code = ?,
                recovery_ts_ms = ?, status_version = status_version + 1,
                updated_ts_ms = ?
            WHERE reservation_id = ? AND status = 'RESERVED' AND status_version = ?
            """,
            (
                reason,
                int(ts_ms),
                int(ts_ms),
                str(row["reservation_id"]),
                current_version,
            ),
        )
        self._require_cas(
            cursor,
            subject=f"OperationReservation {row['reservation_id']}",
        )
        next_version = current_version + 1
        reservation_id = str(row["reservation_id"])
        attempt_id = str(row["attempt_id"])
        self._insert_outbox(
            conn,
            OutboxMessage.create(
                aggregate_type="OPERATION_RESERVATION",
                aggregate_id=reservation_id,
                aggregate_version=next_version,
                event_type="OPERATION_RECOVERY_REQUIRED",
                payload_ref=build_outbox_payload_ref(
                    aggregate_type="OPERATION_RESERVATION",
                    aggregate_id=reservation_id,
                    aggregate_version=next_version,
                ),
                idempotency_key=(
                    f"operation-recovery-required:{reservation_id}:{next_version}"
                ),
                attempt_id=attempt_id,
            ),
            ts_ms=ts_ms,
            expected_aggregate=(
                "OPERATION_RESERVATION",
                reservation_id,
                next_version,
            ),
            expected_event_type="OPERATION_RECOVERY_REQUIRED",
            expected_attempt_id=attempt_id,
        )
        return self._require_operation_reservation(conn, reservation_id)

    def _mark_attempt_reservations_recovery_required(
        self,
        conn: sqlite3.Connection,
        *,
        attempt_id: str,
        ts_ms: int,
    ) -> tuple[str, ...]:
        rows = conn.execute(
            """
            SELECT * FROM task_runtime_idempotency_reservation
            WHERE attempt_id = ? AND status = 'RESERVED'
            ORDER BY reservation_id
            """,
            (str(attempt_id),),
        ).fetchall()
        marked: list[str] = []
        for row in rows:
            reason = self._operation_recovery_reason(conn, row, ts_ms)
            if reason is None:
                continue
            updated = self._mark_operation_recovery_required(
                conn,
                row,
                reason_code=reason,
                ts_ms=ts_ms,
            )
            marked.append(str(updated["reservation_id"]))
        return tuple(marked)

    @staticmethod
    def _require_cas(cursor: sqlite3.Cursor, *, subject: str) -> None:
        if cursor.rowcount != 1:
            raise TaskStoreError(TASK_STORE_CAS_CONFLICT, f"stale expected version for {subject}")

    @staticmethod
    def _require_version(row: sqlite3.Row, expected: int, *, subject: str) -> None:
        actual = int(row["status_version"])
        if actual != int(expected):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                f"stale expected version for {subject}: expected {expected}, actual {actual}",
            )

    @staticmethod
    def _require_graph(conn: sqlite3.Connection, graph_id: str, revision: int) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM task_runtime_graph WHERE graph_id = ? AND revision = ?",
            (str(graph_id), int(revision)),
        ).fetchone()
        if row is None:
            raise TaskStoreError(TASK_STORE_NOT_FOUND, f"TaskGraph not found: {graph_id}@{revision}")
        return row

    @staticmethod
    def _require_node(
        conn: sqlite3.Connection,
        graph_id: str,
        graph_revision: int,
        node_id: str,
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT * FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
            """,
            (str(graph_id), int(graph_revision), str(node_id)),
        ).fetchone()
        if row is None:
            raise TaskStoreError(TASK_STORE_NOT_FOUND, f"TaskNode not found: {node_id}")
        return row

    @staticmethod
    def _require_attempt(conn: sqlite3.Connection, attempt_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM task_runtime_attempt WHERE attempt_id = ?",
            (str(attempt_id),),
        ).fetchone()
        if row is None:
            raise TaskStoreError(TASK_STORE_NOT_FOUND, f"Attempt not found: {attempt_id}")
        return row

    @staticmethod
    def _require_lease(conn: sqlite3.Connection, lease_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM task_runtime_lease WHERE lease_id = ?",
            (str(lease_id),),
        ).fetchone()
        if row is None:
            raise TaskStoreError(TASK_STORE_NOT_FOUND, f"Lease not found: {lease_id}")
        return row

    @staticmethod
    def _active_lease(conn: sqlite3.Connection, attempt_id: str) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT * FROM task_runtime_lease
            WHERE attempt_id = ? AND status = 'ACTIVE'
            """,
            (str(attempt_id),),
        ).fetchone()

    @staticmethod
    def _require_operation_reservation(
        conn: sqlite3.Connection,
        reservation_id: str,
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT * FROM task_runtime_idempotency_reservation
            WHERE reservation_id = ?
            """,
            (str(reservation_id),),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_NOT_FOUND,
                f"OperationReservation not found: {reservation_id}",
            )
        return row

    @staticmethod
    def _require_budget(conn: sqlite3.Connection, budget_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM task_runtime_budget_reservation WHERE budget_id = ?",
            (str(budget_id),),
        ).fetchone()
        if row is None:
            raise TaskStoreError(TASK_STORE_NOT_FOUND, f"BudgetReservation not found: {budget_id}")
        return row

    @staticmethod
    def _require_outbox(conn: sqlite3.Connection, outbox_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
            (str(outbox_id),),
        ).fetchone()
        if row is None:
            raise TaskStoreError(TASK_STORE_NOT_FOUND, f"Outbox not found: {outbox_id}")
        return row

    @staticmethod
    def _assert_lease_owner(
        lease: sqlite3.Row,
        *,
        owner_id: str,
        fencing_token: int,
        now_ts_ms: int,
    ) -> None:
        if str(lease["status"]) != LeaseStatus.ACTIVE.value:
            raise TaskStoreError(TASK_STORE_OWNERSHIP_CONFLICT, "lease is not active")
        if str(lease["owner_id"]) != str(owner_id) or int(lease["fencing_token"]) != int(fencing_token):
            raise TaskStoreError(
                TASK_STORE_OWNERSHIP_CONFLICT,
                "lease owner or fencing token does not match",
            )
        if int(lease["expires_ts_ms"]) <= int(now_ts_ms):
            raise TaskStoreError(TASK_STORE_LEASE_CONFLICT, "lease has expired")

    def _operation_record(self, row: sqlite3.Row) -> dict[str, Any]:
        if row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "operation reservation row is missing",
            )
        record = dict(row)
        try:
            status = IdempotencyStatus(str(record["status"]))
            expires_ts_ms = int(record["expires_ts_ms"])
            created_ts_ms = int(record["created_ts_ms"])
            updated_ts_ms = int(record["updated_ts_ms"])
            status_version = int(record["status_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"operation reservation row is malformed: {exc}",
            ) from exc
        for field_name in ("reservation_id", "business_scope", "operation_fingerprint"):
            if not str(record.get(field_name) or "").strip():
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"operation reservation {field_name} is required",
                )
        if (
            status_version < 0
            or created_ts_ms < 0
            or updated_ts_ms < created_ts_ms
            or expires_ts_ms < created_ts_ms
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "operation reservation fence/version/timestamps are invalid",
            )
        binding_values = {
            "attempt_id": str(record.get("attempt_id") or "").strip(),
            "lease_id": str(record.get("lease_id") or "").strip(),
            "owner_id": str(record.get("owner_id") or "").strip(),
            "fencing_token": record.get("fencing_token"),
        }
        binding_complete = all(
            binding_values[field_name]
            for field_name in ("attempt_id", "lease_id", "owner_id")
        ) and binding_values["fencing_token"] is not None
        recovery_reason = str(record.get("recovery_reason_code") or "").strip()
        legacy_unbound_reasons = {
            "LEGACY_0052_UNBOUND_RESERVATION",
            "LEGACY_0052_COMMITTED",
            "LEGACY_0052_RELEASED",
            "LEGACY_0052_CANCELLED",
        }
        if binding_complete:
            try:
                if int(binding_values["fencing_token"]) < 1:
                    raise ValueError("fencing token must be positive")
            except (TypeError, ValueError) as exc:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"operation reservation fencing_token is invalid: {exc}",
                ) from exc
        elif recovery_reason not in legacy_unbound_reasons:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "operation reservation lease binding is incomplete",
            )
        receipt_ref = str(record.get("receipt_ref") or "").strip()
        legacy_receipt_raw = record.pop("legacy_receipt_raw", None)
        proof_ref = str(record.get("reconciliation_proof_ref") or "").strip()
        proof_digest = str(record.get("reconciliation_proof_digest") or "").strip().lower()
        proof_key_id = str(record.get("reconciliation_proof_key_id") or "").strip()
        proof_signature = str(record.get("reconciliation_proof_signature") or "").strip()
        reconciliation_action = str(record.get("reconciliation_action") or "").strip()
        reconciliation_outcome = str(record.get("reconciliation_outcome") or "").strip()
        reconciler_actor = str(record.get("reconciler_actor_id") or "").strip()
        reconciliation_version = record.get("reconciliation_recovery_version")
        recovery_ts = record.get("recovery_ts_ms")
        legacy_migration_row = recovery_reason in legacy_unbound_reasons
        if legacy_migration_row:
            record["legacy_receipt_digest"] = _legacy_receipt_digest(legacy_receipt_raw)
            record["legacy_receipt_was_null"] = legacy_receipt_raw is None
        elif legacy_receipt_raw is not None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "non-legacy operation reservation contains raw legacy receipt data",
            )
        if (
            recovery_reason == "LEGACY_0052_COMMITTED"
            and not proof_ref
            and not _LEGACY_RECEIPT_REF_PATTERN.fullmatch(receipt_ref)
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "migrated 0052 COMMITTED reservation requires its safe legacy receipt_ref",
            )
        if status is IdempotencyStatus.COMMITTED and not _DURABLE_REFERENCE_PATTERN.fullmatch(
            receipt_ref
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "COMMITTED operation reservation requires a durable receipt_ref",
            )
        if status is not IdempotencyStatus.COMMITTED and receipt_ref:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"{status.value} operation reservation cannot contain receipt_ref",
            )
        if (
            status is IdempotencyStatus.RELEASED
            and not _DURABLE_REFERENCE_PATTERN.fullmatch(proof_ref)
            and recovery_reason != "LEGACY_0052_RELEASED"
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "RELEASED operation reservation requires a reconciliation proof",
            )
        proof_fields_present = any(
            (
                proof_ref,
                proof_digest,
                proof_key_id,
                proof_signature,
                reconciliation_action,
                reconciliation_outcome,
                reconciler_actor,
            )
        ) or reconciliation_version is not None
        if proof_ref:
            if not all(
                (
                    proof_digest,
                    proof_key_id,
                    proof_signature,
                    reconciliation_action,
                    reconciliation_outcome,
                    reconciler_actor,
                )
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "persisted reconciliation proof fields are incomplete",
                )
            try:
                proof_version = int(reconciliation_version)
            except (TypeError, ValueError) as exc:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "operation reconciliation recovery version is invalid",
                ) from exc
            try:
                proof_binding = build_reconciliation_attestation_binding(
                    reservation_id=str(record["reservation_id"]),
                    business_scope=str(record["business_scope"]),
                    operation_fingerprint=str(record["operation_fingerprint"]),
                    recovery_version=proof_version,
                    reconciler_actor_id=reconciler_actor,
                    attestation_ref=proof_ref,
                    recovery_action=reconciliation_action,
                    reconciliation_outcome=reconciliation_outcome,
                    receipt_ref=(
                        receipt_ref
                        if reconciliation_outcome == "SIDE_EFFECT_CONFIRMED"
                        else None
                    ),
                )
            except TaskStoreError as exc:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"persisted reconciliation proof binding is malformed: {exc.detail}",
                ) from exc
            expected_digest = f"sha256:{hashlib.sha256(proof_binding).hexdigest()}"
            if proof_digest != expected_digest:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "persisted reconciliation attestation digest does not match",
                )
            verifier = self._reconciliation_proof_verifier
            if verifier is None:
                raise TaskStoreError(
                    TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED,
                    "persisted proof cannot be trusted without a configured verifier",
                )
            if not verifier.has_key(proof_key_id):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"persisted reconciliation proof has unknown key_id: {proof_key_id}",
                )
            if not verifier.verify(
                key_id=proof_key_id,
                message=proof_binding,
                signature=proof_signature,
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "persisted reconciliation proof signature is invalid",
                )
            if (
                reconciliation_outcome == "SIDE_EFFECT_CONFIRMED"
                and status is not IdempotencyStatus.COMMITTED
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "SIDE_EFFECT_CONFIRMED attestation requires COMMITTED status",
                )
            allowed_statuses = {
                "COMMIT": {IdempotencyStatus.COMMITTED},
                "RELEASE": {IdempotencyStatus.RELEASED},
                "REACQUIRE": {
                    IdempotencyStatus.RESERVED,
                    IdempotencyStatus.RECOVERY_REQUIRED,
                    IdempotencyStatus.COMMITTED,
                },
            }.get(reconciliation_action, set())
            if status not in allowed_statuses:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "persisted reconciliation action/status matrix is invalid: "
                    f"{reconciliation_action}->{status.value}",
                )
        elif proof_fields_present:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "reconciliation fields exist without a complete signed attestation",
            )
        if recovery_reason in legacy_unbound_reasons and recovery_ts is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "migrated legacy reservation lacks its migration timestamp",
            )
        if status is IdempotencyStatus.RECOVERY_REQUIRED:
            if not recovery_reason or recovery_ts is None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "RECOVERY_REQUIRED reservation lacks recovery reason/timestamp",
                )
            try:
                if int(recovery_ts) < created_ts_ms:
                    raise ValueError("recovery timestamp precedes creation")
            except (TypeError, ValueError) as exc:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"operation reservation recovery timestamp is invalid: {exc}",
                ) from exc
        return record

    @staticmethod
    def _budget_record(row: sqlite3.Row) -> dict[str, Any]:
        return _validated_budget_record(row)


def _validate_attempt_transition(
    current: AttemptStatus | str,
    target: AttemptStatus | str,
) -> AttemptStatus:
    try:
        current_status = AttemptStatus(str(current))
        target_status = AttemptStatus(str(target))
    except ValueError as exc:
        raise TaskStoreError(TASK_STORE_INVALID, "unknown attempt status") from exc
    if target_status not in _ATTEMPT_TRANSITIONS[current_status]:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"illegal Attempt transition: {current_status.value}->{target_status.value}",
        )
    return target_status


def _dumps(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            f"value is not canonical finite JSON: {exc}",
        ) from exc


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _reject_duplicate_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key is forbidden: {key}")
        result[key] = value
    return result


def _assert_finite_json(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number is forbidden")
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_finite_json(key)
            _assert_finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _assert_finite_json(item)


def _loads_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} must contain a non-empty JSON object",
        )
    try:
        parsed = json.loads(
            value,
            parse_constant=_reject_non_finite_json_constant,
            object_pairs_hook=_reject_duplicate_json_object,
        )
        _assert_finite_json(parsed)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} contains invalid JSON",
        ) from exc
    if not isinstance(parsed, dict):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} must decode to an object",
        )
    return dict(parsed)


def _loads_list(value: Any, *, field_name: str) -> list[Any]:
    if not isinstance(value, str) or not value.strip():
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} must contain a non-empty JSON array",
        )
    try:
        parsed = json.loads(
            value,
            parse_constant=_reject_non_finite_json_constant,
            object_pairs_hook=_reject_duplicate_json_object,
        )
        _assert_finite_json(parsed)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} contains invalid JSON",
        ) from exc
    if not isinstance(parsed, list):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} must decode to an array",
        )
    return list(parsed)


_BUDGET_AMOUNT_FIELDS = tuple(BudgetAmount().to_dict())


def _validated_budget_amount(payload: dict[str, Any], *, field_name: str) -> BudgetAmount:
    if set(payload) != set(_BUDGET_AMOUNT_FIELDS):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} must contain exactly {sorted(_BUDGET_AMOUNT_FIELDS)}",
        )
    if any(
        isinstance(payload[field], bool) or not isinstance(payload[field], int)
        for field in _BUDGET_AMOUNT_FIELDS
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} values must be JSON integers",
        )
    try:
        return BudgetAmount.from_mapping(payload)
    except (ContractViolation, TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} violates BudgetAmount contract: {exc}",
        ) from exc


def _amount_from_json(value: Any, *, field_name: str) -> BudgetAmount:
    return _validated_budget_amount(
        _loads_object(value, field_name=field_name),
        field_name=field_name,
    )


def _add_amounts(left: BudgetAmount, right: BudgetAmount) -> BudgetAmount:
    left_values = left.to_dict()
    right_values = right.to_dict()
    return BudgetAmount.from_mapping(
        {key: left_values[key] + right_values[key] for key in left_values}
    )


_NODE_ARRAY_FIELDS = ("dependencies", "input_refs", "acceptance_criteria")
_NODE_OBJECT_FIELDS = ("output_contract", "retry_policy")
_NODE_IMMUTABLE_FIELDS = (
    "schema_version",
    "node_id",
    "dependencies",
    "route",
    "input_refs",
    "output_contract",
    "retry_policy",
    "risk_level",
    "acceptance_criteria",
)


def _validated_node_mapping(payload: dict[str, Any], *, field_name: str) -> TaskNode:
    for key in _NODE_ARRAY_FIELDS:
        if not isinstance(payload.get(key), list):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"{field_name}.{key} must be an array",
            )
    for key in _NODE_OBJECT_FIELDS:
        if not isinstance(payload.get(key), dict):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"{field_name}.{key} must be an object",
            )
    if (
        isinstance(payload.get("risk_level"), bool)
        or not isinstance(payload.get("risk_level"), int)
        or isinstance(payload.get("status_version"), bool)
        or not isinstance(payload.get("status_version"), int)
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} risk_level/status_version must be integers",
        )
    try:
        return TaskNode.from_mapping(payload)
    except (ContractViolation, TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"{field_name} violates TaskNode contract: {exc}",
        ) from exc


def _validated_node_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = _loads_object(row["node_json"], field_name="task_runtime_node.node_json")
    node = _validated_node_mapping(payload, field_name="task_runtime_node.node_json")
    expected = {
        "node_id": str(row["node_id"]),
        "status": str(row["status"]),
        "status_version": int(row["status_version"]),
    }
    actual = {
        "node_id": node.node_id,
        "status": node.status.value,
        "status_version": node.status_version,
    }
    if actual != expected:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"task_runtime_node SQL/JSON identity mismatch: expected {expected}, got {actual}",
        )
    return payload


def _validated_graph_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = _loads_object(row["graph_json"], field_name="task_runtime_graph.graph_json")
    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list) or any(not isinstance(item, dict) for item in raw_nodes):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "task_runtime_graph.graph_json.nodes must be an array of objects",
        )
    for index, node_payload in enumerate(raw_nodes):
        _validated_node_mapping(
            node_payload,
            field_name=f"task_runtime_graph.graph_json.nodes[{index}]",
        )
    if (
        isinstance(payload.get("revision"), bool)
        or not isinstance(payload.get("revision"), int)
        or isinstance(payload.get("created_at"), bool)
        or not isinstance(payload.get("created_at"), int)
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "task_runtime_graph revision/created_at must be integers",
        )
    try:
        graph = TaskGraph.from_mapping(payload)
    except (ContractViolation, TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"task_runtime_graph.graph_json violates TaskGraph contract: {exc}",
        ) from exc
    expected = {
        "graph_id": str(row["graph_id"]),
        "revision": int(row["revision"]),
        "root_work_order_id": str(row["root_work_order_id"]),
        "planner_ref": str(row["planner_ref"]),
        "status": str(row["status"]),
    }
    actual = {
        "graph_id": graph.graph_id,
        "revision": graph.revision,
        "root_work_order_id": graph.root_work_order_id,
        "planner_ref": graph.planner_ref,
        "status": graph.status.value,
    }
    if actual != expected:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"task_runtime_graph SQL/JSON identity mismatch: expected {expected}, got {actual}",
        )
    return payload


def _node_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    record["node"] = _validated_node_payload(row)
    record.pop("node_json")
    return record


def _graph_record(graph_row: sqlite3.Row, node_rows: list[sqlite3.Row]) -> dict[str, Any]:
    record = dict(graph_row)
    graph_payload = _validated_graph_payload(graph_row)
    node_records = [_node_record(row) for row in node_rows]
    embedded_nodes = {
        str(node["node_id"]): node
        for node in graph_payload["nodes"]
    }
    durable_nodes = {
        str(node_record["node_id"]): node_record["node"]
        for node_record in node_records
    }
    if set(embedded_nodes) != set(durable_nodes):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "TaskGraph embedded node identities do not match task_runtime_node rows",
        )
    for node_id, embedded in embedded_nodes.items():
        durable = durable_nodes[node_id]
        if any(embedded.get(key) != durable.get(key) for key in _NODE_IMMUTABLE_FIELDS):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"TaskNode immutable contract drift for {node_id}",
            )
    source_kind = str(graph_row["source_kind"])
    raw_legacy_snapshot = graph_row["legacy_snapshot_json"]
    if source_kind == "CANONICAL":
        if raw_legacy_snapshot is not None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "canonical TaskGraph cannot contain a legacy snapshot",
            )
        legacy_snapshot = None
    elif source_kind == "LEGACY_AGENT_JOB":
        legacy_snapshot = _validated_legacy_snapshot(
            raw_legacy_snapshot,
            expected_job_id=str(graph_row["source_ref"] or ""),
        )
    else:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"unsupported durable graph source_kind: {source_kind}",
        )
    record.pop("graph_json")
    record.pop("legacy_snapshot_json")
    record["graph"] = graph_payload
    record["legacy_snapshot"] = legacy_snapshot
    record["nodes"] = node_records
    return record


def _validated_budget_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    amounts = {
        "limits": _amount_from_json(record.pop("limits_json"), field_name="limits_json"),
        "reserved": _amount_from_json(record.pop("reserved_json"), field_name="reserved_json"),
        "consumed": _amount_from_json(record.pop("consumed_json"), field_name="consumed_json"),
        "attempted_consumed": _amount_from_json(
            record.pop("attempted_consumed_json"),
            field_name="attempted_consumed_json",
        ),
    }
    raw_dimensions = _loads_list(
        record.pop("exceeded_dimensions_json"),
        field_name="exceeded_dimensions_json",
    )
    if (
        any(not isinstance(item, str) or not item for item in raw_dimensions)
        or len(set(raw_dimensions)) != len(raw_dimensions)
        or any(item not in _BUDGET_AMOUNT_FIELDS for item in raw_dimensions)
    ):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "exceeded_dimensions_json contains invalid or duplicate dimensions",
        )
    try:
        status = BudgetReservationStatus(str(record["status"]))
        reservation = BudgetReservation(
            budget_id=str(record["budget_id"]),
            scope=str(record["scope"]),
            limits=amounts["limits"],
            reserved=amounts["reserved"],
            consumed=amounts["consumed"],
            status=status,
            status_version=int(record["status_version"]),
        )
        reservation.validate()
    except (ContractViolation, TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"durable BudgetReservation violates contract: {exc}",
        ) from exc
    consumed_values = amounts["consumed"].to_dict()
    attempted_values = amounts["attempted_consumed"].to_dict()
    if any(attempted_values[key] < consumed_values[key] for key in _BUDGET_AMOUNT_FIELDS):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "attempted budget consumption cannot be lower than committed consumption",
        )
    if status is BudgetReservationStatus.EXCEEDED and not raw_dimensions:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "EXCEEDED budget reservation requires exceeded dimensions",
        )
    record.update({key: value.to_dict() for key, value in amounts.items()})
    record["exceeded_dimensions"] = list(raw_dimensions)
    return record


def _outbox_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    evidence_refs = _loads_list(
        record.pop("evidence_refs_json"),
        field_name="task_runtime_outbox.evidence_refs_json",
    )
    if any(not isinstance(reference, str) or not reference.strip() for reference in evidence_refs):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "task_runtime_outbox evidence refs must be non-empty strings",
        )
    try:
        OutboxMessage.create(
            outbox_id=str(record["outbox_id"]),
            aggregate_type=str(record["aggregate_type"]),
            aggregate_id=str(record["aggregate_id"]),
            aggregate_version=int(record["aggregate_version"]),
            event_type=str(record["event_type"]),
            payload_ref=str(record["payload_ref"]),
            idempotency_key=str(record["idempotency_key"]),
            attempt_id=record["attempt_id"],
            evidence_refs=tuple(evidence_refs),
            available_ts_ms=int(record["available_ts_ms"]),
        ).validate()
    except (TaskStoreError, TypeError, ValueError) as exc:
        if isinstance(exc, TaskStoreError) and exc.reason_code == TASK_STORE_DURABLE_DATA_INVALID:
            raise
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"durable OutboxMessage violates contract: {exc}",
        ) from exc
    record["evidence_refs"] = evidence_refs
    return record


_LEGACY_SHADOW_SCHEMA_VERSION = "Natsume.agent.legacy_job_shadow.v1"
_SHA256_REF_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _legacy_job_projection(job: Mapping[str, Any]) -> dict[str, Any]:
    """Project legacy jobs without persisting task bodies or private reasoning.

    Shadow reconciliation needs stable facts, not a second copy of user input,
    prompts, result bodies, or chain-of-thought-like fields.  Payloads are
    represented by deterministic digests only.
    """

    if not isinstance(job, Mapping):
        raise TaskStoreError(TASK_STORE_INVALID, "legacy job must be a mapping")
    data = dict(job)
    work_order = data.get("work_order")
    if not isinstance(work_order, Mapping):
        raise TaskStoreError(TASK_STORE_INVALID, "legacy work_order must be a mapping")
    work_order_data = dict(work_order)
    result = data.get("result")
    if result is not None and not isinstance(result, Mapping):
        raise TaskStoreError(TASK_STORE_INVALID, "legacy result must be a mapping or null")
    result_data = dict(result) if isinstance(result, Mapping) else {}
    try:
        created_at_ms = int(data["created_at_ms"])
        updated_at_ms = int(data["updated_at_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "legacy created_at_ms and updated_at_ms must be integers",
        ) from exc
    job_id = str(data.get("job_id") or "").strip()
    kind = str(data.get("kind") or "").strip()
    status = str(data.get("status") or "").strip()
    if not job_id or not kind or not status or created_at_ms < 0 or updated_at_ms < created_at_ms:
        raise TaskStoreError(
            TASK_STORE_INVALID,
            "legacy job identity/status/timestamps are invalid",
        )
    projection = {
        "schema_version": _LEGACY_SHADOW_SCHEMA_VERSION,
        "job_id": job_id,
        "kind": kind,
        "status": status,
        "work_order_id": str(work_order_data.get("work_order_id") or ""),
        "work_order_schema_version": str(work_order_data.get("schema_version") or ""),
        "work_order_digest": _payload_digest(work_order_data),
        "result_status": str(result_data.get("status") or ""),
        "result_reason_code": str(result_data.get("reason_code") or ""),
        "result_digest": _payload_digest(result_data) if result is not None else "",
        "created_at_ms": created_at_ms,
        "updated_at_ms": updated_at_ms,
    }
    return _validate_legacy_projection(projection)


def _validate_legacy_projection(
    projection: dict[str, Any],
    *,
    expected_job_id: str | None = None,
) -> dict[str, Any]:
    required_keys = {
        "schema_version",
        "job_id",
        "kind",
        "status",
        "work_order_id",
        "work_order_schema_version",
        "work_order_digest",
        "result_status",
        "result_reason_code",
        "result_digest",
        "created_at_ms",
        "updated_at_ms",
    }
    if set(projection) != required_keys:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "legacy shadow projection fields do not match its frozen schema",
        )
    if projection.get("schema_version") != _LEGACY_SHADOW_SCHEMA_VERSION:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "legacy shadow schema_version is invalid",
        )
    for key in (
        "job_id",
        "kind",
        "status",
        "work_order_id",
        "work_order_schema_version",
        "result_status",
        "result_reason_code",
        "result_digest",
    ):
        if not isinstance(projection.get(key), str):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"legacy shadow {key} must be a string",
            )
    if not projection["job_id"] or not projection["kind"] or not projection["status"]:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "legacy shadow job_id, kind, and status are required",
        )
    if expected_job_id is not None and projection["job_id"] != str(expected_job_id):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "legacy shadow job_id does not match source_ref",
        )
    if not _SHA256_REF_PATTERN.fullmatch(str(projection["work_order_digest"])):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "legacy shadow work_order_digest is invalid",
        )
    result_digest = str(projection["result_digest"])
    if result_digest and not _SHA256_REF_PATTERN.fullmatch(result_digest):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "legacy shadow result_digest is invalid",
        )
    for key in ("created_at_ms", "updated_at_ms"):
        value = projection.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"legacy shadow {key} must be a non-negative integer",
            )
    if int(projection["updated_at_ms"]) < int(projection["created_at_ms"]):
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            "legacy shadow updated_at_ms cannot precede created_at_ms",
        )
    return dict(projection)


def _validated_legacy_snapshot(value: Any, *, expected_job_id: str) -> dict[str, Any]:
    return _validate_legacy_projection(
        _loads_object(value, field_name="task_runtime_graph.legacy_snapshot_json"),
        expected_job_id=expected_job_id,
    )


def _payload_digest(value: Mapping[str, Any]) -> str:
    canonical = _dumps(dict(value or {}))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _legacy_receipt_digest(value: Any) -> str:
    if value is None:
        payload = b"null:"
    elif isinstance(value, (bytes, bytearray, memoryview)):
        payload = b"blob:" + bytes(value)
    elif isinstance(value, str):
        payload = b"text:" + value.encode("utf-8", errors="surrogatepass")
    else:
        raise TaskStoreError(
            TASK_STORE_DURABLE_DATA_INVALID,
            f"unsupported migrated receipt storage type: {type(value).__name__}",
        )
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "AttemptStatus",
    "BudgetConsumptionResult",
    "DurableTaskStore",
    "Ed25519ReconciliationProofVerifier",
    "IdempotencyRecoveryAction",
    "IdempotencyStatus",
    "LeaseStatus",
    "OperationReservationResult",
    "OutboxMessage",
    "RecoveryReport",
    "ReconciliationProofVerifier",
    "TASK_RUNTIME_TABLES",
    "TASK_STORE_BUDGET_EXCEEDED",
    "TASK_STORE_CANCELLATION_CONFLICT",
    "TASK_STORE_CAS_CONFLICT",
    "TASK_STORE_DURABLE_DATA_INVALID",
    "TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED",
    "TASK_STORE_INVALID",
    "TASK_STORE_LEASE_CONFLICT",
    "TASK_STORE_NOT_FOUND",
    "TASK_STORE_OUTBOX_CONFLICT",
    "TASK_STORE_OWNERSHIP_CONFLICT",
    "TASK_STORE_RECONCILIATION_PROOF_REQUIRED",
    "TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED",
    "TASK_STORE_SCHEMA_NOT_READY",
    "TaskStoreError",
    "build_operation_fingerprint",
    "build_outbox_payload_ref",
    "build_reconciliation_attestation_binding",
    "TASK_STORE_RECONCILIATION_PROOF_INVALID",
]
