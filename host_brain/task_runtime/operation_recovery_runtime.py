"""Fenced online continuation from one durable Operation checkpoint.

This runtime never decides that an uncertain external side effect did not
happen.  ``RESERVED`` and ``RECOVERY_REQUIRED`` therefore remain blocked until
an authority-signed COMMIT/RELEASE/REACQUIRE is recorded elsewhere.  The only
automatic continuation accepted here starts from an already ``COMMITTED``
receipt and reuses the same Attempt after its former lease has expired.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedExecution
from .coordinator_records import _status, _text
from .errors import (
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GovernedRuntimeError,
)
from .operation_commit_reconciliation import commit_or_reconcile_exact
from .operation_checkpoint_validation import WorkerBudgetUsageCheckpointState
from .operation_recovery_checkpoint_loader import (
    _maybe_await,
    _required_text,
    assert_existing_operation_lineage,
    takeover_committed_checkpoint,
)
from .ports import ExecutionProjectionReaderPort, TaskStorePort
from .runtime_outbox import RuntimeOutbox


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
        durable_elapsed_ms_override: int | None = None,
    ) -> CommittedOperationCheckpoint:
        return await takeover_committed_checkpoint(
            task_store=self._store,
            projection_reader=self._projection_reader,
            runtime_outbox=self._runtime_outbox,
            owner_id=self._owner_id,
            lease_ttl_ms=self._lease_ttl_ms,
            checkpoint_factory=CommittedOperationCheckpoint,
            admitted=admitted,
            command=command,
            reservation=reservation,
            latest_attempt=latest_attempt,
            node_grant_ref=node_grant_ref,
            budget_id=budget_id,
            durable_elapsed_ms_override=durable_elapsed_ms_override,
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
        del cls
        assert_existing_operation_lineage(
            admitted=admitted,
            reservation=reservation,
            latest_attempt=latest_attempt,
        )

    @staticmethod
    def _blocked(detail: str) -> None:
        raise GovernedRuntimeError(GOVERNED_OPERATION_RECOVERY_REQUIRED, detail)



__all__ = [
    "CommittedOperationCheckpoint",
    "DurableOperationRecoveryRuntime",
]
