"""Composition-owned canonical UoW for Internal Agent checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
from typing import Any

from .task_repository import DurableTaskStore
from .task_store_types import (
    OutboxMessage,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TaskStoreError,
)
from .worker_projection_repository import WorkerProjectionStore


def _canonical_path(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve()))


class DurableInternalAgentCheckpointUnitOfWork:
    """Own the only public checkpoint/rejection mutation boundary.

    The adapter binds one exact Task ledger to one exact WorkerProjection
    repository.  Both must address the same physical SQLite database so the
    recovered projection and Operation commit can share one transaction.
    """

    __slots__ = ("_task_store", "_worker_projection_store", "db_path")

    def __init__(
        self,
        *,
        task_store: DurableTaskStore,
        worker_projection_store: WorkerProjectionStore,
    ) -> None:
        if type(task_store) is not DurableTaskStore:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Internal Agent checkpoint UoW requires the exact DurableTaskStore",
            )
        if type(worker_projection_store) is not WorkerProjectionStore:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Internal Agent checkpoint UoW requires the exact WorkerProjectionStore",
            )
        task_path = _canonical_path(task_store.db_path)
        projection_path = _canonical_path(worker_projection_store.db_path)
        if task_path != projection_path:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Internal Agent checkpoint UoW repositories use different databases",
            )
        self._task_store = task_store
        self._worker_projection_store = worker_projection_store
        self.db_path = task_path

    def commit_internal_agent_recovered_checkpoint(
        self,
        reservation_id: str,
        *,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        projection: Mapping[str, Any],
        stage_ref: str,
        seal_digest: str,
        outbox: OutboxMessage,
    ) -> Mapping[str, Any]:
        return self._task_store._commit_internal_agent_recovered_checkpoint_uow(  # noqa: SLF001
            reservation_id,
            worker_projection_store=self._worker_projection_store,
            attempt_id=attempt_id,
            lease_id=lease_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            expected_version=expected_version,
            projection=projection,
            stage_ref=stage_ref,
            seal_digest=seal_digest,
            outbox=outbox,
        )

    def reject_internal_agent_checkpoint_budget_bundle(
        self,
        attempt_id: str,
        *,
        reservation_id: str,
        budget_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_node_version: int,
        expected_graph_version: int,
        expected_budget_version: int,
        expected_lease_version: int,
        error_ref: str,
        usage_id: str,
        attempt_outbox: OutboxMessage,
    ) -> Mapping[str, Any]:
        return self._task_store._reject_internal_agent_checkpoint_budget_bundle_uow(  # noqa: SLF001
            attempt_id,
            reservation_id=reservation_id,
            budget_id=budget_id,
            lease_id=lease_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            expected_attempt_version=expected_attempt_version,
            expected_node_version=expected_node_version,
            expected_graph_version=expected_graph_version,
            expected_budget_version=expected_budget_version,
            expected_lease_version=expected_lease_version,
            error_ref=error_ref,
            usage_id=usage_id,
            attempt_outbox=attempt_outbox,
        )

    def reopen_internal_agent_budget_rejection_terminal(
        self,
        attempt_id: str,
        *,
        reservation_id: str,
        budget_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_attempt_version: int,
        expected_node_version: int,
        expected_graph_version: int,
        expected_budget_version: int,
        expected_lease_version: int,
        error_ref: str,
        usage_id: str,
        attempt_outbox: OutboxMessage,
    ) -> Mapping[str, Any]:
        """Read and verify the exact original mutation after response loss."""

        reopened = dict(
            self._task_store._reopen_internal_agent_budget_rejection_mutation(  # noqa: SLF001
                attempt_id,
                reservation_id=reservation_id,
                budget_id=budget_id,
                lease_id=lease_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                expected_attempt_version=expected_attempt_version,
                expected_node_version=expected_node_version,
                expected_graph_version=expected_graph_version,
                expected_budget_version=expected_budget_version,
                expected_lease_version=expected_lease_version,
                error_ref=error_ref,
                usage_id=usage_id,
                attempt_outbox=attempt_outbox,
            )
        )
        reopened_usage_id = str(reopened.get("usage_id") or "").strip()
        if reopened_usage_id != str(usage_id):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent budget terminal usage identity drifted",
            )
        return {
            **reopened,
            "usage_id": reopened_usage_id,
            "replayed": True,
        }


__all__ = ["DurableInternalAgentCheckpointUnitOfWork"]
