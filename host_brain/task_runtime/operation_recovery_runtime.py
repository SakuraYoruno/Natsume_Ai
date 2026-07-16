"""Fenced online continuation from one durable Operation checkpoint.

This runtime never decides that an uncertain external side effect did not
happen.  ``RESERVED`` and ``RECOVERY_REQUIRED`` therefore remain blocked until
an authority-signed COMMIT/RELEASE/REACQUIRE is recorded elsewhere.  The only
automatic continuation accepted here starts from an already ``COMMITTED``
receipt and reuses the same Attempt after its former lease has expired.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from natsume_contracts.task.v1 import HostCommand

from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
)

from .admission import AdmittedExecution
from .coordinator_receipts import _digest_from_receipt_ref, _worker_receipt_ref
from .coordinator_records import _status, _text
from .errors import (
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GovernedRuntimeError,
)
from .node_execution_records import (
    _is_terminal_commit,
)
from .operation_commit_reconciliation import commit_or_reconcile_exact
from .operation_checkpoint_validation import (
    WorkerBudgetUsageCheckpointState,
    assert_active_checkpoint_budget,
    assert_canonical_projection_identity,
    assert_exact_committed_checkpoint,
    classify_worker_budget_usage,
    expected_route_authority,
)
from .ports import ExecutionProjectionReaderPort, TaskStorePort
from .runtime_outbox import RuntimeOutbox
from .operation_takeover_lease import acquire_checkpoint_takeover_lease


@dataclass(frozen=True, slots=True)
class CommittedOperationCheckpoint:
    reservation: dict[str, Any]
    attempt: dict[str, Any]
    worker_commit: dict[str, Any]
    receipt_ref: str
    payload_digest: str
    payload_digest_source: str
    budget_usage_state: WorkerBudgetUsageCheckpointState
    budget_usage: dict[str, Any] | None
    durable_elapsed_ms: int | None
    lease: dict[str, Any] | None

    @property
    def attempt_id(self) -> str:
        return _text(self.attempt.get("attempt_id"))

    @property
    def lease_id(self) -> str | None:
        return _text((self.lease or {}).get("lease_id")) or None

    @property
    def fencing_token(self) -> int | None:
        raw = (self.lease or {}).get("fencing_token")
        return int(raw) if type(raw) is int and raw > 0 else None


class DurableOperationRecoveryRuntime:
    """Reconcile ambiguous COMMIT and claim a committed checkpoint online."""

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        projection_reader: ExecutionProjectionReaderPort | None,
        runtime_outbox: RuntimeOutbox,
        owner_id: str,
        lease_ttl_ms: int,
    ) -> None:
        self._store = task_store
        self._projection_reader = projection_reader
        self._runtime_outbox = runtime_outbox
        self._owner_id = _required_text(owner_id, "owner_id")
        self._lease_ttl_ms = max(1, int(lease_ttl_ms))

    async def commit_or_reconcile(
        self,
        *,
        commit: Callable[[], Any],
        reservation_id: str,
        business_scope: str,
        operation_fingerprint: str,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        receipt_ref: str,
        checkpoint_digest_ref: str,
    ) -> dict[str, Any]:
        """Accept response loss only when the exact COMMITTED row is visible."""

        return await commit_or_reconcile_exact(
            task_store=self._store,
            commit=commit,
            reservation_id=reservation_id,
            business_scope=business_scope,
            operation_fingerprint=operation_fingerprint,
            attempt_id=attempt_id,
            lease_id=lease_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            expected_version=expected_version,
            receipt_ref=receipt_ref,
            checkpoint_digest_ref=checkpoint_digest_ref,
        )

    async def takeover_committed(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        reservation: Mapping[str, Any],
        latest_attempt: Mapping[str, Any],
        node_grant_ref: str | None,
        budget_id: str,
    ) -> CommittedOperationCheckpoint:
        """Fence the former owner and load, but never replay, its worker result."""

        self.assert_existing_lineage(
            admitted=admitted,
            reservation=reservation,
            latest_attempt=latest_attempt,
        )
        if len(admitted.nodes) != 1:
            self._blocked(
                "multi-node committed checkpoint requires batch recovery"
            )
        if _status(reservation) != "COMMITTED" or not _text(
            reservation.get("receipt_ref")
        ):
            self._blocked("operation has no committed continuation checkpoint")
        receipt_ref = _required_text(reservation.get("receipt_ref"), "receipt_ref")
        receipt_digest = _digest_from_receipt_ref(receipt_ref)
        if receipt_digest is None:
            self._blocked(
                "committed checkpoint receipt has no canonical hash identity"
            )
        checkpoint_digest_ref = (
            f"sha256://execution/{receipt_digest.split(':', 1)[1]}"
        )
        checkpoint = assert_exact_committed_checkpoint(
            await _maybe_await(
                self._store.get_committed_operation_checkpoint(
                    admitted.reservation_id
                )
            ),
            reservation_id=admitted.reservation_id,
            business_scope=admitted.business_scope,
            operation_fingerprint=admitted.operation_fingerprint,
            attempt_id=_required_text(latest_attempt.get("attempt_id"), "attempt_id"),
            lease_id=_required_text(reservation.get("lease_id"), "lease_id"),
            owner_id=_required_text(reservation.get("owner_id"), "owner_id"),
            fencing_token=_required_positive_int(
                reservation.get("fencing_token"),
                "fencing_token",
            ),
            expected_version=(
                _required_positive_int(
                    reservation.get("status_version"),
                    "status_version",
                )
                - 1
            ),
            receipt_ref=receipt_ref,
            checkpoint_digest_ref=checkpoint_digest_ref,
        )
        reservation = checkpoint["reservation"]
        latest_attempt = checkpoint["attempt"]
        historical_lease = checkpoint["lease"]
        self.assert_existing_lineage(
            admitted=admitted,
            reservation=reservation,
            latest_attempt=latest_attempt,
        )
        if self._projection_reader is None:
            self._blocked("committed operation has no durable projection reader")
        loaded = await _maybe_await(self._projection_reader.load(receipt_ref))
        if not isinstance(loaded, Mapping):
            self._blocked("committed operation receipt has no durable worker projection")
        immutable_commit = dict(loaded)
        if not _is_terminal_commit(immutable_commit):
            self._blocked("committed worker projection is not one exact COMMIT checkpoint")

        expected_route, expected_target = expected_route_authority(
            admitted=admitted,
            command=command,
            node_grant_ref=node_grant_ref,
        )

        direct_receipt = _worker_receipt_ref(immutable_commit)
        projection_receipt = _text(immutable_commit.get("projection_id"))
        projected_receipt = direct_receipt or projection_receipt
        if not projected_receipt:
            self._blocked(
                "durable worker projection has no receipt binding for its checkpoint"
            )
        if projected_receipt != receipt_ref:
            self._blocked("durable worker projection differs from the committed receipt")
        if projection_receipt:
            try:
                canonical_projection = validate_worker_projection_payload(
                    immutable_commit,
                    expected_projection_id=receipt_ref,
                )
            except WorkerProjectionStoreError as exc:
                raise GovernedRuntimeError(
                    GOVERNED_OPERATION_RECOVERY_REQUIRED,
                    "committed worker projection failed its durable digest binding",
                ) from exc
            if _text(canonical_projection.get("payload_digest")) != receipt_digest:
                self._blocked(
                    "worker projection digest differs from its receipt identity"
                )
            payload_digest = receipt_digest
            payload_digest_source = "receipt_identity_digest"
            assert_canonical_projection_identity(
                canonical_projection,
                admitted=admitted,
                command=command,
                latest_attempt=latest_attempt,
                expected_route=expected_route,
                expected_target=expected_target,
                node_grant_ref=node_grant_ref,
            )
            # The canonical persisted projection intentionally has no
            # ``receipt_ref`` field.  Add it only to the verifier-facing copy;
            # never hash this augmented wrapper as if it were the original
            # worker COMMIT payload.
            worker_commit = {**canonical_projection, "receipt_ref": receipt_ref}
        else:
            self._blocked(
                "non-canonical public projection cannot prove frozen Attempt lineage"
            )

        snapshot = await _maybe_await(
            self._store.read_scheduler_snapshot(
                admitted.graph_id,
                admitted.graph_revision,
            )
        )
        assert_active_checkpoint_budget(
            snapshot,
            graph_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
            budget_id=_required_text(budget_id, "budget_id"),
        )
        budget_usage = classify_worker_budget_usage(
            snapshot,
            attempt_id=_required_text(latest_attempt.get("attempt_id"), "attempt_id"),
            budget_id=_required_text(budget_id, "budget_id"),
            worker_commit=worker_commit,
            graph_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
            node_id=admitted.node_id,
        )
        if budget_usage.state is WorkerBudgetUsageCheckpointState.CONFLICT:
            self._blocked("committed checkpoint worker BudgetUsage conflicts")
        durable_elapsed_ms = (
            _durable_checkpoint_elapsed_ms(
                reservation=reservation,
                attempt=latest_attempt,
            )
            if budget_usage.state is WorkerBudgetUsageCheckpointState.MISSING
            else None
        )

        attempt, lease = await acquire_checkpoint_takeover_lease(
            task_store=self._store,
            runtime_outbox=self._runtime_outbox,
            admitted=admitted,
            attempt=latest_attempt,
            historical_lease=historical_lease,
            owner_id=self._owner_id,
            lease_ttl_ms=self._lease_ttl_ms,
        )
        if (
            budget_usage.state is WorkerBudgetUsageCheckpointState.MISSING
            and lease is None
        ):
            self._blocked(
                "missing worker BudgetUsage requires a new fenced takeover Lease"
            )

        return CommittedOperationCheckpoint(
            reservation=dict(reservation),
            attempt=attempt,
            worker_commit=worker_commit,
            receipt_ref=receipt_ref,
            payload_digest=payload_digest,
            payload_digest_source=payload_digest_source,
            budget_usage_state=budget_usage.state,
            budget_usage=(
                dict(budget_usage.usage)
                if budget_usage.usage is not None
                else None
            ),
            durable_elapsed_ms=durable_elapsed_ms,
            lease=lease,
        )

    async def reconcile_reservation_state(
        self,
        *,
        admitted: AdmittedExecution,
        reservation: Mapping[str, Any],
        latest_attempt: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply only Store-owned RESERVED expiry tightening online."""

        refreshed = await _maybe_await(
            self._store.reconcile_operation_reservation_state(
                admitted.reservation_id
            )
        )
        if not isinstance(refreshed, Mapping):
            self._blocked("operation reconciliation returned no durable reservation")
        self.assert_existing_lineage(
            admitted=admitted,
            reservation=refreshed,
            latest_attempt=latest_attempt,
        )
        if _text(refreshed.get("reservation_id")) != _text(
            reservation.get("reservation_id")
        ):
            self._blocked("operation reconciliation changed reservation identity")
        return dict(refreshed)

    @staticmethod
    def unresolved_projection(reservation: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "reservation_id": _text(reservation.get("reservation_id")),
            "reservation_status": _status(reservation),
            "side_effect_state": "UNKNOWN",
            "blocking_side_effect_reversible": False,
            "required_signed_reconciliation_actions": [
                "COMMIT",
                "RELEASE",
                "REACQUIRE",
            ],
        }

    @classmethod
    def assert_existing_lineage(
        cls,
        *,
        admitted: AdmittedExecution,
        reservation: Mapping[str, Any],
        latest_attempt: Mapping[str, Any],
    ) -> None:
        if (
            _text(reservation.get("reservation_id")) != admitted.reservation_id
            or _text(reservation.get("business_scope")) != admitted.business_scope
            or _text(reservation.get("operation_fingerprint"))
            != admitted.operation_fingerprint
            or _text(reservation.get("attempt_id"))
            != _text(latest_attempt.get("attempt_id"))
            or _text(latest_attempt.get("graph_id")) != admitted.graph_id
            or int(latest_attempt.get("graph_revision") or 0)
            != admitted.graph_revision
            or _text(latest_attempt.get("node_id")) != admitted.node_id
            or _text(latest_attempt.get("operation_scope"))
            != admitted.business_scope
            or _text(latest_attempt.get("operation_fingerprint"))
            != admitted.operation_fingerprint
        ):
            cls._blocked("existing operation reservation lineage is invalid")

    @staticmethod
    def _blocked(detail: str) -> None:
        raise GovernedRuntimeError(GOVERNED_OPERATION_RECOVERY_REQUIRED, detail)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _required_text(value: Any, field_name: str) -> str:
    normalized = _text(value)
    if not normalized:
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            f"committed checkpoint has no {field_name}",
        )
    return normalized


def _required_positive_int(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            f"committed checkpoint has invalid {field_name}",
        )
    return value


def _durable_checkpoint_elapsed_ms(
    *,
    reservation: Mapping[str, Any],
    attempt: Mapping[str, Any],
) -> int:
    started_ts_ms = attempt.get("started_ts_ms")
    committed_ts_ms = reservation.get("updated_ts_ms")
    if (
        type(started_ts_ms) is not int
        or type(committed_ts_ms) is not int
        or started_ts_ms < 0
        or committed_ts_ms < started_ts_ms
    ):
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "committed checkpoint durable elapsed time is invalid",
        )
    elapsed_ms = committed_ts_ms - started_ts_ms
    if elapsed_ms == 0:
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "committed checkpoint durable elapsed time cannot be zero",
        )
    return elapsed_ms


__all__ = [
    "CommittedOperationCheckpoint",
    "DurableOperationRecoveryRuntime",
]
