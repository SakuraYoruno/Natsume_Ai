"""Durable persistence primitives for the canonical task runtime.

The store is deliberately not a scheduler.  It persists the contracts frozen by
TASK-0466 and provides transactional compare-and-swap, attempt ownership,
leases, recovery, business idempotency, budgets, and an outbox.  The canonical
TaskGraph repository is the sole task state authority.

Schema creation is owned by the central audit migration catalog.  This module
never creates tables inline and fails closed unless the 0052 base and 0054
forward recovery migration have both been applied to the selected database.
"""

from __future__ import annotations

from contextlib import contextmanager
import base64
import binascii
import hashlib
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Iterator, Mapping, NoReturn, Protocol, Sequence
from urllib.parse import quote
import uuid

try:
    from cryptography.exceptions import InvalidSignature as _Ed25519InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey as _Ed25519PublicKey,
    )
except ImportError:  # pragma: no cover - exercised through dependency fail-closed test
    _Ed25519InvalidSignature = None
    _Ed25519PublicKey = None

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
    validate_budget_transition,
)
from host_brain.contracts.agent_runtime.acceptance_judge import (
    AcceptanceGate,
    AcceptanceJudgement,
)
from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphCompletionPolicy,
    TaskGraphStatus,
    TaskNodeStatus,
    validate_task_graph_transition,
    validate_task_node_transition,
)
from host_brain.contracts.agent_runtime.verification_contract import (
    VerificationGate,
    VerificationResult,
)
from host_brain.core.ids import now_ms
from host_brain.contracts.agent_runtime.parallel_settlement import (
    CanonicalParallelSettlementDecision,
    PARALLEL_SETTLEMENT_SCHEMA_VERSION,
    ParallelSettlementContractError,
    normalize_parallel_settlement_decision,
)
from host_brain.contracts.agent_runtime.parallel_success import (
    CanonicalParallelSuccessDecision,
    PARALLEL_SUCCESS_SCHEMA_VERSION,
    ParallelSuccessContractError,
    normalize_parallel_success_decision,
)
from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
    GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
    GraphCancellationContractError,
    normalize_graph_cancellation_decision,
    normalize_graph_cancellation_intent,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_record,
)
from host_brain.persistence.task_store_types import (
    AcceptanceEffectCommitScope,
    AcceptanceEffectIntentSpec,
    AcceptanceEffectStatus,
    AttemptStatus,
    BudgetConsumptionResult,
    IdempotencyRecoveryAction,
    IdempotencyStatus,
    LeaseStatus,
    OperationReservationResult,
    OutboxMessage,
    RecoveryReport,
    TASK_STORE_BUDGET_EXCEEDED,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_CANCELLATION_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_IDEMPOTENCY_RECOVERY_REQUIRED,
    TASK_STORE_LEASE_CONFLICT,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_OUTBOX_CONFLICT,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    _DURABLE_REFERENCE_PATTERN,
    _PARALLEL_SETTLEMENT_DECISION_REQUIRED_COLUMNS,
    _PARALLEL_SUCCESS_DECISION_REQUIRED_COLUMNS,
    build_outbox_payload_ref,
)
from host_brain.persistence.task_record_codec import (
    _ACCEPTANCE_EFFECT_TYPE_PATTERN,
    _MAX_ACCEPTANCE_EFFECT_PAYLOAD_BYTES,
    _SHA256_DIGEST_PATTERN,
    _acceptance_effect_fence,
    _acceptance_effect_record,
    _acceptance_effect_text,
    _add_amounts,
    _amount_from_json,
    _budget_usage_record,
    _dumps,
    _graph_cancellation_decision_record,
    _graph_cancellation_intent_record,
    _graph_record,
    _legacy_receipt_digest,
    _loads_list,
    _loads_object,
    _node_record,
    _outbox_record,
    _parallel_settlement_decision_record,
    _parallel_success_decision_record,
    _validate_attempt_transition,
    _validated_budget_record,
    _validated_node_payload,
    build_acceptance_effect_payload_digest,
)
from host_brain.persistence.task_decision_integrity import (
    _assert_graph_cancellation_decision_bindings,
    _assert_parallel_budget_usage_lineage,
    _assert_parallel_settlement_decision_outbox_binding,
    _assert_parallel_success_decision_bindings,
    _find_invalid_declared_parallel_settlement_decision_ref,
)
from host_brain.persistence.task_acceptance_effect_delivery import (
    TaskAcceptanceEffectDeliveryMixin,
)
from host_brain.persistence.task_acceptance_effect_staging import (
    TaskAcceptanceEffectStagingMixin,
)
from host_brain.persistence.task_parallel_success_store import (
    TaskParallelSuccessStoreMixin,
)
from host_brain.persistence.task_parallel_settlement_store import (
    TaskParallelSettlementStoreMixin,
)
from host_brain.persistence.task_parallel_state import (
    _assert_parallel_node_states,
    _normalize_parallel_node_states,
)
from host_brain.persistence.task_graph_cancellation_request import (
    TaskGraphCancellationRequestMixin,
)
from host_brain.persistence.task_graph_cancellation_finalization import (
    TaskGraphCancellationFinalizationMixin,
)
from host_brain.persistence.task_retry_mutation_store import (
    TaskRetryMutationStoreMixin,
)
from host_brain.persistence.task_retry_replay_store import (
    TaskRetryReplayStoreMixin,
)
from host_brain.persistence.task_atomic_failure_store import (
    TaskAtomicFailureStoreMixin,
)


TASK_STORE_RECONCILIATION_PROOF_REQUIRED = "TASK_STORE_RECONCILIATION_PROOF_REQUIRED"
TASK_STORE_RECONCILIATION_PROOF_INVALID = "TASK_STORE_RECONCILIATION_PROOF_INVALID"
TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED = "TASK_STORE_RECONCILIATION_AUTHORITY_REQUIRED"
TASK_STORE_SCHEDULER_CAPACITY = "TASK_STORE_SCHEDULER_CAPACITY"

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
_DAG_SCHEDULING_REQUIRED_COLUMNS = frozenset(
    {"retry_not_before_ms"}
)
_BUDGET_USAGE_REQUIRED_COLUMNS = frozenset(
    {
        "budget_id", "usage_id", "graph_id", "graph_revision", "node_id",
        "attempt_id", "charge_kind", "delta_json", "delta_digest", "accepted",
        "reason_code", "exceeded_dimensions_json", "reservation_version_before",
        "reservation_version_after", "outbox_id", "created_ts_ms",
    }
)
_GRAPH_CANCELLATION_INTENT_REQUIRED_COLUMNS = frozenset(
    {
        "intent_ref",
        "schema_version",
        "request_id",
        "request_identity",
        "source",
        "reason_code",
        "graph_id",
        "graph_revision",
        "graph_version_before",
        "graph_version_after",
        "budget_id",
        "budget_version_at_request",
        "payload_json",
        "payload_sha256",
        "graph_outbox_id",
        "status",
        "status_version",
        "requested_ts_ms",
        "finalized_ts_ms",
    }
)
_GRAPH_CANCELLATION_DECISION_REQUIRED_COLUMNS = frozenset(
    {
        "decision_ref",
        "schema_version",
        "intent_ref",
        "graph_id",
        "graph_revision",
        "graph_version_before",
        "graph_version_after",
        "budget_id",
        "budget_version_before",
        "budget_version_after",
        "payload_json",
        "payload_sha256",
        "graph_outbox_id",
        "budget_outbox_id",
        "created_ts_ms",
    }
)
_GRAPH_CANCELLATION_EFFECT_FENCE_REQUIRED_COLUMNS = frozenset(
    {
        "effect_id",
        "intent_ref",
        "observed_status",
        "resolution_status",
        "observed_status_version",
        "created_ts_ms",
        "resolved_ts_ms",
    }
)
_LEGACY_RECEIPT_REF_PATTERN = re.compile(r"^legacy-receipt://0052/rowid--?[0-9]+$")


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


class DurableTaskStore(
    TaskAcceptanceEffectStagingMixin,
    TaskAcceptanceEffectDeliveryMixin,
    TaskParallelSuccessStoreMixin,
    TaskParallelSettlementStoreMixin,
    TaskGraphCancellationRequestMixin,
    TaskGraphCancellationFinalizationMixin,
    TaskRetryMutationStoreMixin,
    TaskRetryReplayStoreMixin,
    TaskAtomicFailureStoreMixin,
):
    """SQLite-backed canonical TaskStore with transaction-scoped outbox writes."""

    _normalize_parallel_node_states = staticmethod(_normalize_parallel_node_states)
    _assert_parallel_node_states = staticmethod(_assert_parallel_node_states)

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

    def assert_budget_usage_ready(self) -> None:
        """Require the forward budget-usage journal before governed execution."""
        conn = self._connect()
        try:
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_budget_usage)"
                ).fetchall()
            }
        finally:
            conn.close()
        missing = sorted(_BUDGET_USAGE_REQUIRED_COLUMNS - columns)
        if missing:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                f"task runtime budget usage migration 0085 is not applied: {missing}",
            )

    def assert_dag_scheduling_ready(self) -> None:
        """Require the NE-01 B1 durable scheduling columns."""

        conn = self._connect()
        try:
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_node)"
                ).fetchall()
            }
            invalid_retry_row = (
                conn.execute(
                    """
                    SELECT node_id, status, retry_not_before_ms
                    FROM task_runtime_node
                    WHERE (status = 'RETRY_PENDING' AND retry_not_before_ms IS NULL)
                       OR (status <> 'RETRY_PENDING' AND retry_not_before_ms IS NOT NULL)
                    ORDER BY graph_id, graph_revision, node_id
                    LIMIT 1
                    """
                ).fetchone()
                if _DAG_SCHEDULING_REQUIRED_COLUMNS <= columns
                else None
            )
        finally:
            conn.close()
        missing = sorted(_DAG_SCHEDULING_REQUIRED_COLUMNS - columns)
        if missing:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime DAG scheduling migration 0089 is not applied: "
                f"{missing}",
            )
        if invalid_retry_row is not None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "task runtime retry deadline invariant is invalid: "
                f"{invalid_retry_row['node_id']}:{invalid_retry_row['status']}",
            )


    def assert_graph_cancellation_ready(self) -> None:
        """Require the NE-01 B5C graph-cancellation authority journal."""

        conn = self._connect()
        try:
            intent_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_graph_cancellation_intent)"
                ).fetchall()
            }
            decision_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_graph_cancellation_decision)"
                ).fetchall()
            }
            effect_fence_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_graph_cancellation_effect_fence)"
                ).fetchall()
            }
        finally:
            conn.close()
        missing_intent = sorted(
            _GRAPH_CANCELLATION_INTENT_REQUIRED_COLUMNS - intent_columns
        )
        missing_decision = sorted(
            _GRAPH_CANCELLATION_DECISION_REQUIRED_COLUMNS - decision_columns
        )
        missing_effect_fence = sorted(
            _GRAPH_CANCELLATION_EFFECT_FENCE_REQUIRED_COLUMNS
            - effect_fence_columns
        )
        if missing_intent or missing_decision or missing_effect_fence:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime graph cancellation migration 0092 is not applied: "
                f"intent={missing_intent}, decision={missing_decision}, "
                f"effect_fence={missing_effect_fence}",
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
        observed_ts_ms = self._now()
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
            usage_ready = conn.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type = 'table' AND name = 'task_runtime_budget_usage'"""
            ).fetchone()
            budget_usage_rows = (
                conn.execute(
                    """SELECT usage.* FROM task_runtime_budget_usage AS usage
                       JOIN task_runtime_budget_reservation AS budget
                         ON budget.budget_id = usage.budget_id
                       WHERE budget.graph_id = ? AND budget.graph_revision = ?
                       ORDER BY usage.created_ts_ms, usage.budget_id, usage.usage_id""",
                    (str(graph_id), int(revision)),
                ).fetchall()
                if usage_ready is not None
                else []
            )
            snapshot = {
                "observed_ts_ms": observed_ts_ms,
                "graph_record": _graph_record(graph_row, node_rows),
                "attempts": [dict(row) for row in attempt_rows],
                "active_leases": [dict(row) for row in lease_rows],
                "budgets": [self._budget_record(row) for row in budget_rows],
                "budget_usage": [_budget_usage_record(row) for row in budget_usage_rows],
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
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_id,
                graph_revision=revision,
            )
            return self._transition_graph_in_transaction(
                conn,
                graph_id=graph_id,
                revision=revision,
                expected_version=expected_version,
                target_status=target_status,
                outbox=outbox,
                ts_ms=ts_ms,
            )

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
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_id,
                graph_revision=graph_revision,
            )
            return self._transition_node_in_transaction(
                conn,
                graph_id=graph_id,
                graph_revision=graph_revision,
                node_id=node_id,
                expected_version=expected_version,
                target_status=target_status,
                outbox=outbox,
                verification_result=verification_result,
                acceptance_judgement=acceptance_judgement,
                ts_ms=ts_ms,
            )

    def _transition_graph_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        revision: int,
        expected_version: int,
        target_status: TaskGraphStatus | str,
        outbox: OutboxMessage,
        ts_ms: int,
        attempt_id: str | None = None,
    ) -> dict[str, Any]:
        row = self._require_graph(conn, graph_id, revision)
        self._require_version(
            row,
            expected_version,
            subject=f"TaskGraph {graph_id}@{revision}",
        )
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
                int(ts_ms),
                str(graph_id),
                int(revision),
                int(expected_version),
            ),
        )
        self._require_cas(cursor, subject=f"TaskGraph {graph_id}@{revision}")
        self._insert_outbox(
            conn,
            outbox,
            ts_ms=int(ts_ms),
            expected_aggregate=(
                "TASK_GRAPH",
                str(graph_id),
                int(expected_version) + 1,
            ),
            expected_event_type=f"TASK_GRAPH_{target.value}",
            expected_attempt_id=(str(attempt_id) if attempt_id is not None else None),
        )
        updated_graph = self._require_graph(conn, graph_id, revision)
        return _graph_record(updated_graph, node_rows)

    def _transition_node_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        expected_version: int,
        target_status: TaskNodeStatus | str,
        outbox: OutboxMessage,
        verification_result: VerificationResult | Mapping[str, Any] | None,
        acceptance_judgement: AcceptanceJudgement | Mapping[str, Any] | None,
        ts_ms: int,
    ) -> dict[str, Any]:
        row = self._require_node(conn, graph_id, graph_revision, node_id)
        self._require_version(row, expected_version, subject=f"TaskNode {node_id}")
        target = validate_task_node_transition(str(row["status"]), target_status)
        if (
            target is TaskNodeStatus.RETRY_PENDING
            or str(row["status"]) == TaskNodeStatus.RETRY_PENDING.value
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "retry transitions must use schedule_retry/promote_retry_due",
            )
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
                    node_acceptance_criteria=tuple(
                        node_payload.get("acceptance_criteria") or ()
                    ),
                    outbox_evidence_refs=outbox.evidence_refs,
                )
            except ContractViolation as exc:
                raise TaskStoreError(exc.reason_code, exc.detail) from exc
        node_payload["status"] = target.value
        node_payload["status_version"] = int(expected_version) + 1
        cancel_ts = (
            int(ts_ms)
            if target is TaskNodeStatus.CANCEL_REQUESTED
            else row["cancellation_requested_ts_ms"]
        )
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
                int(ts_ms),
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
            ts_ms=int(ts_ms),
            expected_aggregate=(
                "TASK_NODE",
                f"{graph_id}@{int(graph_revision)}/{node_id}",
                int(expected_version) + 1,
            ),
            expected_event_type=f"TASK_NODE_{target.value}",
            expected_attempt_id=None,
        )
        return _node_record(
            self._require_node(conn, graph_id, graph_revision, node_id)
        )

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
                if str(node["status"]) == TaskNodeStatus.RETRY_PENDING.value:
                    if "retry_not_before_ms" not in node.keys():
                        raise TaskStoreError(
                            TASK_STORE_SCHEMA_NOT_READY,
                            "RETRY_PENDING cancellation requires migration 0089",
                        )
                    cursor = conn.execute(
                        """
                        UPDATE task_runtime_node
                        SET status = ?, status_version = status_version + 1,
                            node_json = ?, retry_not_before_ms = NULL,
                            cancellation_requested_ts_ms = ?, updated_ts_ms = ?
                        WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                          AND status = 'RETRY_PENDING' AND status_version = ?
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
                else:
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

    def _assert_dependencies_accepted(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_payload: Mapping[str, Any],
    ) -> None:
        dependencies = tuple(str(item) for item in node_payload.get("dependencies") or ())
        if not dependencies:
            return
        dependency_rows = conn.execute(
            """
            SELECT node_id, status
            FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ?
            """,
            (str(graph_id), int(graph_revision)),
        ).fetchall()
        statuses = {
            str(row["node_id"]): str(row["status"])
            for row in dependency_rows
        }
        missing = sorted(set(dependencies) - set(statuses))
        if missing:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "attempt dependency rows are missing: " + ",".join(missing),
            )
        unaccepted = sorted(
            dependency
            for dependency in dependencies
            if statuses[dependency] != TaskNodeStatus.ACCEPTED.value
        )
        if unaccepted:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "attempt dependencies are not ACCEPTED: " + ",".join(unaccepted),
            )

    @staticmethod
    def _require_active_graph_budget(
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
    ) -> sqlite3.Row:
        rows = conn.execute(
            """
            SELECT *
            FROM task_runtime_budget_reservation
            WHERE graph_id = ? AND graph_revision = ?
              AND node_id IS NULL AND attempt_id IS NULL
              AND status = 'ACTIVE'
            ORDER BY budget_id
            """,
            (str(graph_id), int(graph_revision)),
        ).fetchall()
        if len(rows) != 1:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "DAG scheduling requires exactly one ACTIVE graph-level budget",
            )
        return rows[0]

    @staticmethod
    def _assert_graph_not_cancelling(
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
    ) -> None:
        """Fence new execution work after a durable graph cancellation request.

        Older migration snapshots remain readable; callers that opt into B5C
        separately require 0092 through ``assert_graph_cancellation_ready``.
        """

        ready = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table'
              AND name = 'task_runtime_graph_cancellation_intent'
            """
        ).fetchone()
        if ready is None:
            return
        row = conn.execute(
            """
            SELECT intent_ref FROM task_runtime_graph_cancellation_intent
            WHERE graph_id = ? AND graph_revision = ?
            """,
            (str(graph_id), int(graph_revision)),
        ).fetchone()
        if row is not None:
            raise TaskStoreError(
                TASK_STORE_CANCELLATION_CONFLICT,
                "durable Graph cancellation fences new execution authority: "
                f"{row['intent_ref']}",
            )


    def claim_ready_node(
        self,
        *,
        attempt_id: str,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        expected_node_version: int,
        expected_attempt_count: int,
        owner_id: str,
        lease_ttl_ms: int,
        budget_id: str,
        operation_scope: str | None,
        operation_fingerprint: str | None,
        attempt_created_outbox: OutboxMessage,
        attempt_leased_outbox: OutboxMessage,
    ) -> dict[str, Any]:
        """Atomically create the next attempt and lease one bounded READY node.

        This closes the historical crash gap between ``create_attempt`` and
        ``acquire_lease``.  The caller may preview capacity with the pure B0
        scheduler, but this transaction rechecks dependency and concurrency
        invariants under ``BEGIN IMMEDIATE``.
        """

        self.assert_dag_scheduling_ready()
        attempt_ref = str(attempt_id or "").strip()
        owner = str(owner_id or "").strip()
        budget_ref = str(budget_id or "").strip()
        scope = str(operation_scope or "").strip() or None
        fingerprint = str(operation_fingerprint or "").strip() or None
        if (
            not attempt_ref
            or not owner
            or not budget_ref
            or int(lease_ttl_ms) <= 0
            or int(expected_attempt_count) < 0
            or ((scope is None) != (fingerprint is None))
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "claim requires attempt/owner/budget, positive lease, "
                "non-negative attempt count, and paired operation identity",
            )
        ts_ms = self._now()
        expires_ts_ms = ts_ms + int(lease_ttl_ms)
        lease_id = f"lease::{uuid.uuid4().hex}"
        heartbeat_id = f"heartbeat::{uuid.uuid4().hex}"
        with self._transaction() as conn:
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_id,
                graph_revision=graph_revision,
            )
            graph = self._require_graph(conn, graph_id, graph_revision)
            if str(graph["status"]) != TaskGraphStatus.ACTIVE.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"TaskGraph {graph_id}@{graph_revision} must be ACTIVE before claim",
                )
            budget_row = conn.execute(
                """
                SELECT * FROM task_runtime_budget_reservation
                WHERE budget_id = ?
                """,
                (budget_ref,),
            ).fetchone()
            if (
                budget_row is None
                or str(budget_row["status"]) != BudgetReservationStatus.ACTIVE.value
                or str(budget_row["graph_id"]) != str(graph_id)
                or int(budget_row["graph_revision"] or 0) != int(graph_revision)
                or budget_row["node_id"] is not None
                or budget_row["attempt_id"] is not None
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "READY claim requires the ACTIVE graph-level budget reservation",
                )
            concurrency_limit = _amount_from_json(
                budget_row["limits_json"],
                field_name="task_runtime_budget_reservation.limits_json",
            ).concurrency
            if concurrency_limit < 1:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "READY claim budget has no positive concurrency limit",
                )
            node = self._require_node(conn, graph_id, graph_revision, node_id)
            existing_attempt = conn.execute(
                "SELECT * FROM task_runtime_attempt WHERE attempt_id = ?",
                (attempt_ref,),
            ).fetchone()
            if existing_attempt is not None:
                return self._replay_ready_node_claim(
                    conn,
                    attempt=existing_attempt,
                    node=node,
                    graph_id=str(graph_id),
                    graph_revision=int(graph_revision),
                    node_id=str(node_id),
                    expected_node_version=int(expected_node_version),
                    expected_attempt_count=int(expected_attempt_count),
                    owner_id=owner,
                    lease_ttl_ms=int(lease_ttl_ms),
                    operation_scope=scope,
                    operation_fingerprint=fingerprint,
                    attempt_created_outbox=attempt_created_outbox,
                    attempt_leased_outbox=attempt_leased_outbox,
                    observed_ts_ms=ts_ms,
                )
            self._require_version(
                node,
                expected_node_version,
                subject=f"TaskNode {node_id}",
            )
            if str(node["status"]) != TaskNodeStatus.READY.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"claim requires READY node, got {node['status']}",
                )
            if node["retry_not_before_ms"] is not None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"READY TaskNode {node_id} retains a retry deadline",
                )
            attempt_stats = conn.execute(
                """
                SELECT COUNT(*) AS attempt_rows,
                       COALESCE(MAX(attempt_no), 0) AS max_attempt_no
                FROM task_runtime_attempt
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                """,
                (str(graph_id), int(graph_revision), str(node_id)),
            ).fetchone()
            durable_attempt_count = int(attempt_stats["max_attempt_no"])
            if (
                int(attempt_stats["attempt_rows"]) != durable_attempt_count
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    f"TaskNode {node_id} attempt journal is not contiguous",
                )
            if durable_attempt_count != int(expected_attempt_count):
                raise TaskStoreError(
                    TASK_STORE_CAS_CONFLICT,
                    f"TaskNode {node_id} attempt count changed",
                )
            node_payload = _validated_node_payload(node)
            self._assert_dependencies_accepted(
                conn,
                graph_id=graph_id,
                graph_revision=graph_revision,
                node_payload=node_payload,
            )
            retry_policy = dict(node_payload.get("retry_policy") or {})
            attempt_no = durable_attempt_count + 1
            if attempt_no > int(retry_policy["max_attempts"]):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"TaskNode {node_id} exhausted max_attempts",
                )
            occupied = conn.execute(
                """
                SELECT COUNT(*) AS occupied
                FROM task_runtime_lease AS lease
                JOIN task_runtime_attempt AS attempt
                  ON attempt.attempt_id = lease.attempt_id
                WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
                  AND lease.status = 'ACTIVE' AND lease.expires_ts_ms > ?
                  AND attempt.status IN ('LEASED', 'RUNNING')
                """,
                (str(graph_id), int(graph_revision), ts_ms),
            ).fetchone()
            if int(occupied["occupied"]) >= concurrency_limit:
                raise TaskStoreError(
                    TASK_STORE_SCHEDULER_CAPACITY,
                    f"TaskGraph {graph_id}@{graph_revision} has no free execution slot",
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
                        attempt_no,
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
                attempt_created_outbox,
                ts_ms=ts_ms,
                expected_aggregate=("TASK_ATTEMPT", attempt_ref, 0),
                expected_event_type="TASK_ATTEMPT_CREATED",
                expected_attempt_id=attempt_ref,
            )

            attempt_cursor = conn.execute(
                """
                UPDATE task_runtime_attempt
                SET status = 'LEASED', owner_id = ?, acquired_ts_ms = ?,
                    status_version = 1, updated_ts_ms = ?
                WHERE attempt_id = ? AND status = 'CREATED' AND status_version = 0
                """,
                (owner, ts_ms, ts_ms, attempt_ref),
            )
            self._require_cas(attempt_cursor, subject=f"Attempt {attempt_ref}")
            validate_task_node_transition(TaskNodeStatus.READY, TaskNodeStatus.LEASED)
            node_payload["status"] = TaskNodeStatus.LEASED.value
            node_payload["status_version"] = int(node["status_version"]) + 1
            node_cursor = conn.execute(
                """
                UPDATE task_runtime_node
                SET status = 'LEASED', status_version = status_version + 1,
                    node_json = ?, retry_not_before_ms = NULL, updated_ts_ms = ?
                WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                  AND status = 'READY' AND status_version = ?
                """,
                (
                    _dumps(node_payload),
                    ts_ms,
                    str(graph_id),
                    int(graph_revision),
                    str(node_id),
                    int(expected_node_version),
                ),
            )
            self._require_cas(node_cursor, subject=f"TaskNode {node_id}")
            node_aggregate_id = f"{graph_id}@{int(graph_revision)}/{node_id}"
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
                    idempotency_key=f"task-node-leased:{node_aggregate_id}:{node_version}",
                    outbox_id=(
                        f"outbox::task-node-leased:{node_aggregate_id}:{node_version}"
                    ),
                    attempt_id=attempt_ref,
                ),
                ts_ms=ts_ms,
                expected_aggregate=("TASK_NODE", node_aggregate_id, node_version),
                expected_event_type="TASK_NODE_LEASED",
                expected_attempt_id=attempt_ref,
            )
            conn.execute(
                """
                INSERT INTO task_runtime_lease(
                    lease_id, attempt_id, owner_id, fencing_token, acquired_ts_ms,
                    heartbeat_ts_ms, expires_ts_ms, released_ts_ms, status, status_version
                ) VALUES (?, ?, ?, 1, ?, ?, ?, NULL, 'ACTIVE', 0)
                """,
                (lease_id, attempt_ref, owner, ts_ms, ts_ms, expires_ts_ms),
            )
            conn.execute(
                """
                INSERT INTO task_runtime_heartbeat(
                    heartbeat_id, lease_id, attempt_id, owner_id, fencing_token,
                    observed_ts_ms, expires_ts_ms
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    heartbeat_id,
                    lease_id,
                    attempt_ref,
                    owner,
                    ts_ms,
                    expires_ts_ms,
                ),
            )
            self._insert_outbox(
                conn,
                attempt_leased_outbox,
                ts_ms=ts_ms,
                expected_aggregate=("TASK_ATTEMPT", attempt_ref, 1),
                expected_event_type="TASK_ATTEMPT_LEASED",
                expected_attempt_id=attempt_ref,
            )
            return {
                "attempt": dict(self._require_attempt(conn, attempt_ref)),
                "lease": dict(self._require_lease(conn, lease_id)),
                "node": _node_record(
                    self._require_node(conn, graph_id, graph_revision, node_id)
                ),
                "replayed": False,
            }

    def _replay_ready_node_claim(
        self,
        conn: sqlite3.Connection,
        *,
        attempt: sqlite3.Row,
        node: sqlite3.Row,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        expected_node_version: int,
        expected_attempt_count: int,
        owner_id: str,
        lease_ttl_ms: int,
        operation_scope: str | None,
        operation_fingerprint: str | None,
        attempt_created_outbox: OutboxMessage,
        attempt_leased_outbox: OutboxMessage,
        observed_ts_ms: int,
    ) -> dict[str, Any]:
        """Return an exact, still-live replay of a committed READY claim.

        A caller may lose the response after SQLite commits.  Reusing the
        deterministic attempt and outbox identities must not create another
        attempt or consume another concurrency slot.  Any ownership, binding,
        TTL, outbox, or post-claim state drift is rejected instead of handing
        stale execution authority back to the caller.
        """

        expected_attempt_no = int(expected_attempt_count) + 1
        attempt_binding = (
            str(attempt["graph_id"]),
            int(attempt["graph_revision"]),
            str(attempt["node_id"]),
            int(attempt["attempt_no"]),
        )
        if attempt_binding != (
            str(graph_id),
            int(graph_revision),
            str(node_id),
            expected_attempt_no,
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "READY claim replay attempt binding drift",
            )
        if (
            str(attempt["status"]) != AttemptStatus.LEASED.value
            or int(attempt["status_version"]) != 1
            or str(attempt["owner_id"] or "") != owner_id
            or (str(attempt["operation_scope"] or "").strip() or None)
            != operation_scope
            or (str(attempt["operation_fingerprint"] or "").strip() or None)
            != operation_fingerprint
            or attempt["started_ts_ms"] is not None
            or attempt["completed_ts_ms"] is not None
            or attempt["error_ref"] is not None
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "READY claim replay attempt state drift",
            )
        if (
            str(node["status"]) != TaskNodeStatus.LEASED.value
            or int(node["status_version"]) != int(expected_node_version) + 1
            or node["retry_not_before_ms"] is not None
        ):
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "READY claim replay node state drift",
            )

        lease_rows = conn.execute(
            """
            SELECT * FROM task_runtime_lease
            WHERE attempt_id = ?
            ORDER BY acquired_ts_ms, lease_id
            """,
            (str(attempt["attempt_id"]),),
        ).fetchall()
        if len(lease_rows) != 1:
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                "READY claim replay requires exactly one durable lease",
            )
        lease = lease_rows[0]
        if (
            str(lease["status"]) != LeaseStatus.ACTIVE.value
            or int(lease["status_version"]) != 0
            or str(lease["owner_id"]) != owner_id
            or int(lease["fencing_token"]) != 1
            or int(lease["expires_ts_ms"]) <= int(observed_ts_ms)
            or int(lease["expires_ts_ms"]) - int(lease["acquired_ts_ms"])
            != int(lease_ttl_ms)
            or int(attempt["acquired_ts_ms"] or -1)
            != int(lease["acquired_ts_ms"])
        ):
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                "READY claim replay lease authority drift",
            )
        heartbeat_rows = conn.execute(
            """
            SELECT * FROM task_runtime_heartbeat
            WHERE lease_id = ?
            ORDER BY observed_ts_ms, heartbeat_id
            """,
            (str(lease["lease_id"]),),
        ).fetchall()
        if len(heartbeat_rows) != 1:
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                "READY claim replay requires the initial durable heartbeat",
            )
        heartbeat = heartbeat_rows[0]
        if (
            str(heartbeat["attempt_id"]) != str(attempt["attempt_id"])
            or str(heartbeat["owner_id"]) != owner_id
            or int(heartbeat["fencing_token"]) != 1
            or int(heartbeat["observed_ts_ms"]) != int(lease["acquired_ts_ms"])
            or int(heartbeat["expires_ts_ms"]) != int(lease["expires_ts_ms"])
        ):
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                "READY claim replay heartbeat authority drift",
            )

        attempt_ref = str(attempt["attempt_id"])
        self._assert_mutation_outbox_replay(
            conn,
            attempt_created_outbox,
            expected_aggregate=("TASK_ATTEMPT", attempt_ref, 0),
            expected_event_type="TASK_ATTEMPT_CREATED",
            expected_attempt_id=attempt_ref,
        )
        self._assert_mutation_outbox_replay(
            conn,
            attempt_leased_outbox,
            expected_aggregate=("TASK_ATTEMPT", attempt_ref, 1),
            expected_event_type="TASK_ATTEMPT_LEASED",
            expected_attempt_id=attempt_ref,
        )
        node_aggregate_id = f"{graph_id}@{int(graph_revision)}/{node_id}"
        node_version = int(expected_node_version) + 1
        node_outbox = OutboxMessage.create(
            aggregate_type="TASK_NODE",
            aggregate_id=node_aggregate_id,
            aggregate_version=node_version,
            event_type="TASK_NODE_LEASED",
            payload_ref=build_outbox_payload_ref(
                aggregate_type="TASK_NODE",
                aggregate_id=node_aggregate_id,
                aggregate_version=node_version,
            ),
            idempotency_key=f"task-node-leased:{node_aggregate_id}:{node_version}",
            outbox_id=f"outbox::task-node-leased:{node_aggregate_id}:{node_version}",
            attempt_id=attempt_ref,
        )
        self._assert_mutation_outbox_replay(
            conn,
            node_outbox,
            expected_aggregate=("TASK_NODE", node_aggregate_id, node_version),
            expected_event_type="TASK_NODE_LEASED",
            expected_attempt_id=attempt_ref,
        )
        return {
            "attempt": dict(attempt),
            "lease": dict(lease),
            "node": _node_record(node),
            "replayed": True,
        }










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
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_id,
                graph_revision=graph_revision,
            )
            node = self._require_node(conn, graph_id, graph_revision, node_id)
            self._require_version(node, expected_node_version, subject=f"TaskNode {node_id}")
            if str(node["status"]) != TaskNodeStatus.READY.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"attempt requires READY node, got {node['status']}",
                )
            node_payload = _validated_node_payload(node)
            dependencies = tuple(str(item) for item in node_payload.get("dependencies") or ())
            if dependencies:
                dependency_rows = conn.execute(
                    """
                    SELECT node_id, status
                    FROM task_runtime_node
                    WHERE graph_id = ? AND graph_revision = ?
                    """,
                    (str(graph_id), int(graph_revision)),
                ).fetchall()
                dependency_statuses = {
                    str(row["node_id"]): str(row["status"])
                    for row in dependency_rows
                }
                missing = sorted(set(dependencies) - set(dependency_statuses))
                if missing:
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "attempt dependency rows are missing: " + ",".join(missing),
                    )
                unaccepted = sorted(
                    dependency
                    for dependency in dependencies
                    if dependency_statuses[dependency] != TaskNodeStatus.ACCEPTED.value
                )
                if unaccepted:
                    raise TaskStoreError(
                        TASK_STORE_INVALID,
                        "attempt dependencies are not ACCEPTED: " + ",".join(unaccepted),
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

    def get_active_lease(self, attempt_id: str) -> dict[str, Any] | None:
        """Return the exact ACTIVE lease row for one attempt, if present.

        This is intentionally a read-only identity lookup. Callers must still
        compare ``expires_ts_ms`` with their trusted clock before authorizing
        an operation; an expired row may remain ACTIVE until recovery marks it.
        """

        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM task_runtime_lease
                WHERE attempt_id = ? AND status = 'ACTIVE'
                """,
                (str(attempt_id),),
            ).fetchone()
            return dict(row) if row is not None else None
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
            self._assert_graph_not_cancelling(
                conn,
                graph_id=str(attempt["graph_id"]),
                graph_revision=int(attempt["graph_revision"]),
            )
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
            if int(lease["status_version"]) == int(expected_lease_version) + 1:
                attempt = self._require_attempt(conn, str(lease["attempt_id"]))
                self._assert_node_not_cancelled_for_attempt(conn, attempt)
                self._assert_lease_owner(
                    lease,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                    now_ts_ms=ts_ms,
                )
                heartbeat_ts_ms = int(lease["heartbeat_ts_ms"])
                if int(lease["expires_ts_ms"]) != heartbeat_ts_ms + int(ttl_ms):
                    raise TaskStoreError(
                        TASK_STORE_CAS_CONFLICT,
                        f"lease heartbeat replay TTL drift for {lease_id}",
                    )
                heartbeat = conn.execute(
                    """
                    SELECT 1 FROM task_runtime_heartbeat
                    WHERE lease_id = ? AND attempt_id = ? AND owner_id = ?
                      AND fencing_token = ? AND observed_ts_ms = ?
                      AND expires_ts_ms = ?
                    LIMIT 1
                    """,
                    (
                        str(lease_id),
                        str(lease["attempt_id"]),
                        str(owner_id),
                        int(fencing_token),
                        heartbeat_ts_ms,
                        int(lease["expires_ts_ms"]),
                    ),
                ).fetchone()
                if heartbeat is None:
                    raise TaskStoreError(
                        TASK_STORE_CAS_CONFLICT,
                        f"lease heartbeat replay journal is missing for {lease_id}",
                    )
                self._assert_mutation_outbox_replay(
                    conn,
                    outbox,
                    expected_aggregate=(
                        "TASK_LEASE",
                        str(lease_id),
                        int(expected_lease_version) + 1,
                    ),
                    expected_event_type="TASK_LEASE_HEARTBEAT",
                    expected_attempt_id=str(lease["attempt_id"]),
                )
                return dict(lease)
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
            self._assert_graph_not_cancelling(
                conn,
                graph_id=str(attempt["graph_id"]),
                graph_revision=int(attempt["graph_revision"]),
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
            bound_attempt = self._require_attempt(conn, str(row["attempt_id"]))
            self._assert_graph_not_cancelling(
                conn,
                graph_id=str(bound_attempt["graph_id"]),
                graph_revision=int(bound_attempt["graph_revision"]),
            )
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
            if (
                str(row["status"]) == IdempotencyStatus.RESERVED.value
                and int(row["status_version"]) == int(expected_version) + 1
            ):
                _attempt, lease = self._assert_operation_binding(
                    conn,
                    row,
                    attempt_id=attempt_id,
                    lease_id=lease_id,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                    now_ts_ms=ts_ms,
                )
                expected_expiry = min(
                    int(row["updated_ts_ms"]) + int(ttl_ms),
                    int(lease["expires_ts_ms"]),
                )
                if int(row["expires_ts_ms"]) != expected_expiry:
                    raise TaskStoreError(
                        TASK_STORE_CAS_CONFLICT,
                        f"operation renewal replay TTL drift for {reservation_id}",
                    )
                attempt_ref = str(attempt_id or "").strip()
                self._require_outbox_attempt(outbox, attempt_ref)
                self._assert_mutation_outbox_replay(
                    conn,
                    outbox,
                    expected_aggregate=(
                        "OPERATION_RESERVATION",
                        str(reservation_id),
                        int(expected_version) + 1,
                    ),
                    expected_event_type="OPERATION_RENEWED",
                    expected_attempt_id=attempt_ref,
                )
                return self._operation_record(row)
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
                if original_attempt_ref is not None:
                    original_attempt = self._require_attempt(conn, original_attempt_ref)
                    self._assert_graph_not_cancelling(
                        conn,
                        graph_id=str(original_attempt["graph_id"]),
                        graph_revision=int(original_attempt["graph_revision"]),
                    )
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
            if graph_id is not None and graph_revision is not None:
                self._assert_graph_not_cancelling(
                    conn,
                    graph_id=str(graph_id),
                    graph_revision=int(graph_revision),
                )
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
            if row["graph_id"] is not None and row["graph_revision"] is not None:
                self._assert_graph_not_cancelling(
                    conn,
                    graph_id=str(row["graph_id"]),
                    graph_revision=int(row["graph_revision"]),
                )
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

    def get_graph_cancellation_intent(self, intent_ref: str) -> dict[str, Any]:
        """Return one integrity-checked graph cancellation intent."""

        self.assert_graph_cancellation_ready()
        reference = str(intent_ref or "").strip()
        if not reference:
            raise TaskStoreError(TASK_STORE_INVALID, "cancellation intent_ref is required")
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM task_runtime_graph_cancellation_intent WHERE intent_ref = ?",
                (reference,),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    f"graph cancellation intent not found: {reference}",
                )
            return self._validated_graph_cancellation_intent_record(conn, row)
        finally:
            conn.close()

    def get_graph_cancellation_for_graph(
        self,
        graph_id: str,
        revision: int,
    ) -> dict[str, Any] | None:
        self.assert_graph_cancellation_ready()
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM task_runtime_graph_cancellation_intent
                WHERE graph_id = ? AND graph_revision = ?
                """,
                (str(graph_id), int(revision)),
            ).fetchone()
            return (
                self._validated_graph_cancellation_intent_record(conn, row)
                if row is not None
                else None
            )
        finally:
            conn.close()

    def _validated_graph_cancellation_intent_record(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        """Validate terminal authority, including signed operation evidence."""

        record = _graph_cancellation_intent_record(row)
        if str(record["status"]) != "FINALIZED":
            return record
        decision_row = conn.execute(
            """
            SELECT * FROM task_runtime_graph_cancellation_decision
            WHERE intent_ref = ?
            """,
            (str(record["intent_ref"]),),
        ).fetchone()
        if decision_row is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "finalized cancellation intent is missing its decision",
            )
        decision = _graph_cancellation_decision_record(decision_row)
        _assert_graph_cancellation_decision_bindings(
            conn,
            decision,
            evidence_refs_builder=self._graph_cancellation_evidence_refs,
        )
        return record

    def list_pending_graph_cancellations(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Enumerate REQUESTED intents for startup/re-entry continuation."""

        self.assert_graph_cancellation_ready()
        size = max(1, min(int(limit), 500))
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM task_runtime_graph_cancellation_intent
                WHERE status = 'REQUESTED'
                ORDER BY requested_ts_ms, graph_id, graph_revision
                LIMIT ?
                """,
                (size,),
            ).fetchall()
            return [_graph_cancellation_intent_record(row) for row in rows]
        finally:
            conn.close()

    def get_graph_cancellation_decision(self, decision_ref: str) -> dict[str, Any]:
        self.assert_graph_cancellation_ready()
        reference = str(decision_ref or "").strip()
        if not reference:
            raise TaskStoreError(TASK_STORE_INVALID, "cancellation decision_ref is required")
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM task_runtime_graph_cancellation_decision WHERE decision_ref = ?",
                (reference,),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    f"graph cancellation decision not found: {reference}",
                )
            record = _graph_cancellation_decision_record(row)
            _assert_graph_cancellation_decision_bindings(
                conn,
                record,
                evidence_refs_builder=self._graph_cancellation_evidence_refs,
            )
            return record
        finally:
            conn.close()

    def get_graph_cancellation_decision_for_intent(
        self,
        intent_ref: str,
    ) -> dict[str, Any] | None:
        self.assert_graph_cancellation_ready()
        reference = str(intent_ref or "").strip()
        if not reference:
            raise TaskStoreError(TASK_STORE_INVALID, "cancellation intent_ref is required")
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM task_runtime_graph_cancellation_decision
                WHERE intent_ref = ?
                """,
                (reference,),
            ).fetchone()
            if row is None:
                return None
            record = _graph_cancellation_decision_record(row)
            _assert_graph_cancellation_decision_bindings(
                conn,
                record,
                evidence_refs_builder=self._graph_cancellation_evidence_refs,
            )
            return record
        finally:
            conn.close()

    @staticmethod
    def _cancellation_outbox(
        *,
        intent_ref: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
        attempt_id: str | None = None,
    ) -> OutboxMessage:
        material = (
            f"{intent_ref}\n{aggregate_type}\n{aggregate_id}\n"
            f"{aggregate_version}\n{event_type}"
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return OutboxMessage.create(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            event_type=event_type,
            payload_ref=build_outbox_payload_ref(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                aggregate_version=aggregate_version,
            ),
            idempotency_key=f"graph-cancellation:{digest}",
            outbox_id=f"outbox::graph-cancellation:{digest}",
            attempt_id=attempt_id,
            evidence_refs=(intent_ref,),
        )


    def _graph_cancellation_evidence_refs(
        self,
        conn: sqlite3.Connection,
        *,
        intent_ref: str,
        graph_id: str,
        revision: int,
    ) -> tuple[str, ...]:
        refs: set[str] = {str(intent_ref)}
        operations = conn.execute(
            """
            SELECT reservation.*
            FROM task_runtime_idempotency_reservation AS reservation
            JOIN task_runtime_attempt AS attempt
              ON attempt.attempt_id = reservation.attempt_id
            WHERE attempt.graph_id = ? AND attempt.graph_revision = ?
            ORDER BY reservation.reservation_id
            """,
            (str(graph_id), int(revision)),
        ).fetchall()
        for row in operations:
            operation = self._operation_record(row)
            durable = next(
                (
                    str(operation[key])
                    for key in ("receipt_ref", "reconciliation_proof_ref")
                    if operation.get(key) is not None
                    and _DURABLE_REFERENCE_PATTERN.fullmatch(str(operation[key]))
                ),
                None,
            )
            if durable is not None:
                refs.add(durable)
            else:
                material = _dumps(
                    {
                        "reservation_id": str(operation["reservation_id"]),
                        "status": str(operation["status"]),
                        "status_version": int(operation["status_version"]),
                    }
                )
                refs.add(
                    "taskcancel-operation://sha256/"
                    + hashlib.sha256(material.encode("utf-8")).hexdigest()
                )
        effect_rows = conn.execute(
            """
            SELECT effect.effect_id, effect.status, effect.status_version,
                   fence.resolution_status
            FROM task_runtime_acceptance_effect_intent AS effect
            LEFT JOIN task_runtime_graph_cancellation_effect_fence AS fence
              ON fence.effect_id = effect.effect_id AND fence.intent_ref = ?
            WHERE effect.graph_id = ? AND effect.graph_revision = ?
            ORDER BY effect.effect_id
            """,
            (str(intent_ref), str(graph_id), int(revision)),
        ).fetchall()
        for row in effect_rows:
            refs.add(str(row["effect_id"]))
            if row["resolution_status"] is None:
                # Authorized NODE effects remain deliverable after Graph
                # cancellation; bind only their immutable effect identity.
                continue
            material = _dumps(
                {
                    "effect_id": str(row["effect_id"]),
                    "status": str(row["status"]),
                    "status_version": int(row["status_version"]),
                    "resolution_status": row["resolution_status"],
                }
            )
            refs.add(
                "taskcancel-effect://sha256/"
                + hashlib.sha256(material.encode("utf-8")).hexdigest()
            )
        prefix = f"task-node://{quote(graph_id, safe='')}/{int(revision)}/"
        reviews = conn.execute(
            """
            SELECT review_id, status, status_version, token_ref
            FROM agent_runtime_review
            WHERE task_graph_id = ? AND substr(subject_ref, 1, length(?)) = ?
            ORDER BY review_id
            """,
            (str(graph_id), prefix, prefix),
        ).fetchall()
        for row in reviews:
            material = _dumps(
                {
                    "review_id": str(row["review_id"]),
                    "status": str(row["status"]),
                    "status_version": int(row["status_version"]),
                }
            )
            refs.add(
                "taskcancel-review://sha256/"
                + hashlib.sha256(material.encode("utf-8")).hexdigest()
            )
            token_ref = str(row["token_ref"] or "").strip()
            if _DURABLE_REFERENCE_PATTERN.fullmatch(token_ref):
                refs.add(token_ref)
        return tuple(sorted(refs))


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
            if row["graph_id"] is not None and row["graph_revision"] is not None:
                self._assert_graph_not_cancelling(
                    conn,
                    graph_id=str(row["graph_id"]),
                    graph_revision=int(row["graph_revision"]),
                )
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

    def consume_budget_once(
        self,
        budget_id: str,
        *,
        usage_id: str,
        charge_kind: str,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        delta: BudgetAmount,
        expected_version: int,
        outbox: OutboxMessage,
    ) -> BudgetConsumptionResult:
        """Atomically journal and apply one stable budget usage charge.

        An exact ``(budget_id, usage_id)`` replay returns the original outcome
        without changing the reservation or inserting another outbox row.  Any
        reuse with different binding material fails closed.
        """

        budget_ref = str(budget_id or "").strip()
        usage_ref = str(usage_id or "").strip()
        kind = str(charge_kind or "").strip().upper()
        graph_ref = str(graph_id or "").strip()
        node_ref = str(node_id or "").strip()
        attempt_ref = str(attempt_id or "").strip()
        if (
            not budget_ref
            or not _DURABLE_REFERENCE_PATTERN.fullmatch(usage_ref)
            or not kind
            or len(kind) > 64
            or not graph_ref
            or not node_ref
            or not attempt_ref
            or isinstance(graph_revision, bool)
            or not isinstance(graph_revision, int)
            or graph_revision < 1
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "budget usage requires a durable usage_id and complete graph/attempt binding",
            )
        if not isinstance(delta, BudgetAmount):
            raise TaskStoreError(TASK_STORE_INVALID, "delta must be a BudgetAmount")
        normalized_delta = BudgetAmount.from_mapping(delta.to_dict())
        delta_json = _dumps(normalized_delta.to_dict())
        delta_digest = "sha256:" + hashlib.sha256(delta_json.encode("utf-8")).hexdigest()
        ts_ms = self._now()

        with self._transaction() as conn:
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_ref,
                graph_revision=graph_revision,
            )
            existing = conn.execute(
                """
                SELECT * FROM task_runtime_budget_usage
                WHERE budget_id = ? AND usage_id = ?
                """,
                (budget_ref, usage_ref),
            ).fetchone()
            if existing is not None:
                drift = (
                    str(existing["charge_kind"]) != kind
                    or str(existing["graph_id"]) != graph_ref
                    or int(existing["graph_revision"]) != graph_revision
                    or str(existing["node_id"]) != node_ref
                    or str(existing["attempt_id"]) != attempt_ref
                    or str(existing["delta_digest"]) != delta_digest
                    or str(existing["delta_json"]) != delta_json
                )
                if drift:
                    raise TaskStoreError(
                        TASK_STORE_OUTBOX_CONFLICT,
                        f"budget usage replay drift: {usage_ref}",
                    )
                reservation = self._budget_record(self._require_budget(conn, budget_ref))
                dimensions = tuple(
                    str(item)
                    for item in _loads_list(
                        existing["exceeded_dimensions_json"],
                        field_name="budget_usage.exceeded_dimensions_json",
                    )
                )
                usage = _budget_usage_record(existing)
                return BudgetConsumptionResult(
                    accepted=bool(existing["accepted"]),
                    reason_code=(str(existing["reason_code"]) if existing["reason_code"] else None),
                    reservation=reservation,
                    exceeded_dimensions=dimensions,
                    usage_id=usage_ref,
                    replayed=True,
                    usage=usage,
                )

            row = self._require_budget(conn, budget_ref)
            self._require_version(row, expected_version, subject=f"BudgetReservation {budget_ref}")
            if str(row["status"]) != BudgetReservationStatus.ACTIVE.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    f"budget {budget_ref} must be ACTIVE before consumption",
                )
            if (
                str(row["graph_id"] or "") != graph_ref
                or int(row["graph_revision"] or 0) != graph_revision
                or row["node_id"] is not None
                or row["attempt_id"] is not None
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "shared budget reservation graph binding drifted",
                )
            attempt = self._require_attempt(conn, attempt_ref)
            if (
                str(attempt["graph_id"]) != graph_ref
                or int(attempt["graph_revision"]) != graph_revision
                or str(attempt["node_id"]) != node_ref
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "budget usage attempt is outside the reservation graph revision",
                )

            consumed = _amount_from_json(row["consumed_json"], field_name="consumed_json")
            reserved = _amount_from_json(row["reserved_json"], field_name="reserved_json")
            attempted = _add_amounts(consumed, normalized_delta)
            exceeded_dimensions = tuple(
                key
                for key, value in attempted.to_dict().items()
                if value > reserved.to_dict()[key]
            )
            accepted = not exceeded_dimensions
            target_status = (
                BudgetReservationStatus.ACTIVE.value
                if accepted
                else BudgetReservationStatus.EXCEEDED.value
            )
            next_consumed = attempted if accepted else consumed
            next_version = int(expected_version) + 1
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
                    budget_ref,
                    int(expected_version),
                ),
            )
            self._require_cas(cursor, subject=f"BudgetReservation {budget_ref}")
            reason_code = TASK_STORE_BUDGET_EXCEEDED if exceeded_dimensions else None
            conn.execute(
                """
                INSERT INTO task_runtime_budget_usage(
                    budget_id, usage_id, graph_id, graph_revision, node_id,
                    attempt_id, charge_kind, delta_json, delta_digest, accepted,
                    reason_code, exceeded_dimensions_json,
                    reservation_version_before, reservation_version_after,
                    outbox_id, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    budget_ref,
                    usage_ref,
                    graph_ref,
                    graph_revision,
                    node_ref,
                    attempt_ref,
                    kind,
                    delta_json,
                    delta_digest,
                    1 if accepted else 0,
                    reason_code,
                    _dumps(list(exceeded_dimensions)),
                    int(expected_version),
                    next_version,
                    outbox.outbox_id,
                    ts_ms,
                ),
            )
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=("BUDGET_RESERVATION", budget_ref, next_version),
                expected_event_type=("BUDGET_CONSUMED" if accepted else "BUDGET_EXCEEDED"),
                expected_attempt_id=attempt_ref,
            )
            updated = self._budget_record(self._require_budget(conn, budget_ref))
            usage_row = conn.execute(
                """
                SELECT * FROM task_runtime_budget_usage
                WHERE budget_id = ? AND usage_id = ?
                """,
                (budget_ref, usage_ref),
            ).fetchone()
            return BudgetConsumptionResult(
                accepted=accepted,
                reason_code=reason_code,
                reservation=updated,
                exceeded_dimensions=exceeded_dimensions,
                usage_id=usage_ref,
                replayed=False,
                usage=_budget_usage_record(usage_row),
            )

    def get_budget_usage(self, budget_id: str, usage_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM task_runtime_budget_usage
                WHERE budget_id = ? AND usage_id = ?
                """,
                (str(budget_id), str(usage_id)),
            ).fetchone()
            if row is None:
                raise TaskStoreError(
                    TASK_STORE_NOT_FOUND,
                    f"BudgetUsage not found: {usage_id}",
                )
            return _budget_usage_record(row)
        finally:
            conn.close()

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

    # -- Acceptance-bound effect intents ------------------------------------------


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

    def _assert_mutation_outbox_replay(
        self,
        conn: sqlite3.Connection,
        message: OutboxMessage,
        *,
        expected_aggregate: tuple[str, str, int],
        expected_event_type: str,
        expected_attempt_id: str | None,
    ) -> None:
        if not isinstance(message, OutboxMessage):
            raise TaskStoreError(TASK_STORE_INVALID, "atomic replay requires an OutboxMessage")
        message.validate()
        if message.available_ts_ms is not None:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "mutation outbox availability is assigned only by the trusted store clock",
            )
        expected = (
            str(expected_aggregate[0]),
            str(expected_aggregate[1]),
            int(expected_aggregate[2]),
        )
        if (
            (message.aggregate_type, message.aggregate_id, message.aggregate_version) != expected
            or message.event_type != str(expected_event_type)
            or message.attempt_id != (str(expected_attempt_id or "").strip() or None)
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "atomic replay outbox does not bind the committed mutation",
            )
        by_id = conn.execute(
            "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
            (message.outbox_id,),
        ).fetchone()
        by_key = conn.execute(
            "SELECT * FROM task_runtime_outbox WHERE idempotency_key = ?",
            (message.idempotency_key,),
        ).fetchone()
        if (
            by_id is None
            or by_key is None
            or str(by_id["outbox_id"]) != str(by_key["outbox_id"])
        ):
            raise TaskStoreError(
                TASK_STORE_OUTBOX_CONFLICT,
                "atomic acceptance replay is missing its exact state outbox",
            )
        durable = _outbox_record(by_id)
        drift = (
            str(durable["outbox_id"]) != message.outbox_id
            or str(durable["aggregate_type"]) != message.aggregate_type
            or str(durable["aggregate_id"]) != message.aggregate_id
            or int(durable["aggregate_version"]) != message.aggregate_version
            or str(durable["event_type"]) != message.event_type
            or str(durable["payload_ref"]) != message.payload_ref
            or str(durable["idempotency_key"]) != message.idempotency_key
            or durable["attempt_id"] != message.attempt_id
            or tuple(durable["evidence_refs"]) != tuple(message.evidence_refs)
        )
        if drift:
            raise TaskStoreError(
                TASK_STORE_OUTBOX_CONFLICT,
                "atomic acceptance state outbox replay drift",
            )

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
        self._assert_graph_not_cancelling(
            conn,
            graph_id=str(attempt["graph_id"]),
            graph_revision=int(attempt["graph_revision"]),
        )
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


__all__ = [
    "AcceptanceEffectCommitScope",
    "AcceptanceEffectIntentSpec",
    "AcceptanceEffectStatus",
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
    "TASK_STORE_SCHEDULER_CAPACITY",
    "TaskStoreError",
    "build_acceptance_effect_payload_digest",
    "build_operation_fingerprint",
    "build_outbox_payload_ref",
    "build_reconciliation_attestation_binding",
    "TASK_STORE_RECONCILIATION_PROOF_INVALID",
]
