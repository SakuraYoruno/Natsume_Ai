"""Resolve an existing Operation before any worker may run."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import logging
from typing import Any, Literal

from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext
from .coordinator_records import (
    _exception_reason,
    _required_record_text,
    _status,
    _text,
)
from .errors import (
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GovernedRuntimeError,
)
from .internal_agent_sealed_checkpoint_recovery import (
    recover_internal_agent_sealed_checkpoint,
)
from .operation_recovery_runtime import (
    CommittedOperationCheckpoint,
    DurableOperationRecoveryRuntime,
)
from .ports import (
    GovernedWorkerResolverPort,
    InternalAgentCheckpointUnitOfWorkPort,
    TaskStorePort,
)
from .runtime_outbox import RuntimeOutbox


_logger = logging.getLogger("Natsume.task_runtime.existing_operation_resolution")


@dataclass(frozen=True, slots=True)
class ExistingOperationResolution:
    kind: Literal["BLOCKED", "COMMITTED", "REACQUIRED"]
    reservation: dict[str, Any]
    latest_attempt: dict[str, Any]
    checkpoint: CommittedOperationCheckpoint | None = None
    lease: dict[str, Any] | None = None
    execution_segment: str | None = None
    blocked_projection: dict[str, Any] | None = None


async def resolve_existing_operation(
    *,
    task_store: TaskStorePort,
    checkpoint_uow: InternalAgentCheckpointUnitOfWorkPort,
    worker_resolver: GovernedWorkerResolverPort,
    operation_recovery: DurableOperationRecoveryRuntime,
    runtime_outbox: RuntimeOutbox,
    owner_id: str,
    admitted: AdmittedExecution,
    command: HostCommand,
    work_order: Any,
    budget: BudgetRuntimeContext,
    graph: Mapping[str, Any],
    node: Mapping[str, Any],
    reservation: Mapping[str, Any],
    latest_attempt: Mapping[str, Any],
    node_grant_ref: str | None,
    allow_multinode_checkpoint_continuation: bool,
) -> ExistingOperationResolution:
    """Return one explicit hold, committed checkpoint, or signed rerun claim."""

    operation_recovery.assert_existing_lineage(
        admitted=admitted,
        reservation=reservation,
        latest_attempt=latest_attempt,
    )
    refreshed = await operation_recovery.reconcile_reservation_state(
        admitted=admitted,
        reservation=reservation,
        latest_attempt=latest_attempt,
    )
    attempt = dict(latest_attempt)
    status = _status(refreshed)
    sealed_detail: str | None = None
    sealed_elapsed_ms: int | None = None
    if status in {"RECOVERY_REQUIRED", "COMMITTED"}:
        try:
            recovered = await recover_internal_agent_sealed_checkpoint(
                task_store=task_store,
                checkpoint_uow=checkpoint_uow,
                worker_resolver=worker_resolver,
                operation_recovery=operation_recovery,
                runtime_outbox=runtime_outbox,
                admitted=admitted,
                command=command,
                work_order=work_order,
                budget=budget,
                graph=graph,
                node=node,
                reservation=refreshed,
                latest_attempt=attempt,
                node_grant_ref=node_grant_ref,
            )
        except Exception as exc:
            recovered = None
            _logger.warning(
                "Internal Agent sealed checkpoint recovery was rejected: %s",
                exc,
            )
            sealed_detail = _exception_reason(
                exc,
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
            )
        if recovered is not None:
            refreshed, sealed_elapsed_ms = recovered
            status = _status(refreshed)

    if status == "RESERVED" and is_signed_reacquired_operation(
        refreshed,
        owner_id=owner_id,
    ):
        lease = await _maybe_await(
            task_store.get_active_lease(_required_record_text(attempt, "attempt_id"))
        )
        lease_record = dict(lease) if isinstance(lease, Mapping) else None
        _assert_reacquired_execution_state(
            graph=graph,
            node=node,
            attempt=attempt,
            reservation=refreshed,
            lease=lease_record,
            owner_id=owner_id,
        )
        return ExistingOperationResolution(
            kind="REACQUIRED",
            reservation=dict(refreshed),
            latest_attempt=attempt,
            lease=lease_record,
            execution_segment=_reacquisition_segment(refreshed),
        )

    if status != "COMMITTED":
        active_lease = await _maybe_await(
            task_store.get_active_lease(_required_record_text(attempt, "attempt_id"))
        )
        projection = {
            **operation_recovery.unresolved_projection(refreshed),
            "online_takeover_state": (
                "WAITING_FOR_LEASE_EXPIRY"
                if status == "RESERVED" and active_lease is not None
                else "SIGNED_RECONCILIATION_REQUIRED"
            ),
            "budget": budget.execution_payload(),
        }
        if sealed_detail:
            projection["sealed_checkpoint_state"] = "INVALID"
            projection["sealed_checkpoint_detail"] = sealed_detail
        return ExistingOperationResolution(
            kind="BLOCKED",
            reservation=dict(refreshed),
            latest_attempt=attempt,
            blocked_projection=projection,
        )

    if _status(graph) != "ACTIVE" or _status(node) not in {
        "LEASED",
        "RUNNING",
        "EXECUTED",
        "VERIFYING",
        "VERIFIED",
    }:
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "committed checkpoint durable Graph/Node state is not continuable",
        )
    try:
        checkpoint = await operation_recovery.takeover_committed(
            admitted=admitted,
            command=command,
            reservation=refreshed,
            latest_attempt=attempt,
            node_grant_ref=node_grant_ref,
            budget_id=budget.budget_id,
            durable_elapsed_ms_override=sealed_elapsed_ms,
            allow_multinode_checkpoint_continuation=(
                allow_multinode_checkpoint_continuation
            ),
        )
    except GovernedRuntimeError as exc:
        _logger.warning("Committed checkpoint takeover was rejected: %s", exc)
        return ExistingOperationResolution(
            kind="BLOCKED",
            reservation=dict(refreshed),
            latest_attempt=attempt,
            blocked_projection={
                "online_takeover_state": (
                    "BATCH_RECOVERY_PENDING"
                    if len(admitted.nodes) != 1
                    else "CHECKPOINT_INCOMPLETE"
                ),
                "checkpoint_detail": _exception_reason(
                    exc,
                    GOVERNED_OPERATION_RECOVERY_REQUIRED,
                ),
                "worker_replayed": False,
                "budget": budget.execution_payload(),
            },
        )
    return ExistingOperationResolution(
        kind="COMMITTED",
        reservation=dict(refreshed),
        latest_attempt=attempt,
        checkpoint=checkpoint,
    )


def is_signed_reacquired_operation(
    reservation: Mapping[str, Any],
    *,
    owner_id: str | None = None,
) -> bool:
    recovery_version = reservation.get("reconciliation_recovery_version")
    status_version = reservation.get("status_version")
    return (
        _status(reservation) == "RESERVED"
        and (
            owner_id is None
            or _text(reservation.get("owner_id")) == _text(owner_id)
        )
        and _text(reservation.get("reconciliation_action")).upper() == "REACQUIRE"
        and _text(reservation.get("reconciliation_outcome")).upper()
        == "NO_SIDE_EFFECT"
        and bool(_text(reservation.get("reconciliation_proof_ref")))
        and bool(_text(reservation.get("reconciliation_proof_digest")))
        and bool(_text(reservation.get("reconciliation_proof_key_id")))
        and bool(_text(reservation.get("reconciliation_proof_signature")))
        and type(recovery_version) is int
        and recovery_version >= 0
        and type(status_version) is int
        and status_version == recovery_version + 1
        and not _text(reservation.get("receipt_ref"))
    )


def is_signed_reacquired_live_lease(
    reservation: Mapping[str, Any],
    lease: Mapping[str, Any],
) -> bool:
    """Return whether one live Lease is the signed replacement fence."""

    return (
        is_signed_reacquired_operation(reservation)
        and _status(lease) == "ACTIVE"
        and _text(lease.get("attempt_id"))
        == _text(reservation.get("attempt_id"))
        and _text(lease.get("lease_id")) == _text(reservation.get("lease_id"))
        and _text(lease.get("owner_id")) == _text(reservation.get("owner_id"))
        and lease.get("fencing_token") == reservation.get("fencing_token")
    )


def has_blocking_live_recovery_lease(
    recovery_bindings: Sequence[tuple[Any, Mapping[str, Any]]],
    active_leases: Sequence[Any],
    *,
    observed_ts_ms: int,
) -> bool:
    """Exclude only the exact signed replacement fence from scheduler holds."""

    reservations = {
        _required_record_text(reservation, "attempt_id"): reservation
        for _, reservation in recovery_bindings
    }
    for lease in active_leases:
        if not isinstance(lease, Mapping) or _status(lease) != "ACTIVE":
            continue
        attempt_id = _text(lease.get("attempt_id"))
        expires_ts_ms = lease.get("expires_ts_ms")
        if (
            attempt_id in reservations
            and type(expires_ts_ms) is int
            and expires_ts_ms > observed_ts_ms
            and not is_signed_reacquired_live_lease(
                reservations[attempt_id],
                lease,
            )
        ):
            return True
    return False


def _assert_reacquired_execution_state(
    *,
    graph: Mapping[str, Any],
    node: Mapping[str, Any],
    attempt: Mapping[str, Any],
    reservation: Mapping[str, Any],
    lease: Mapping[str, Any] | None,
    owner_id: str,
) -> None:
    if (
        _status(graph) != "ACTIVE"
        or _status(node) not in {"LEASED", "RUNNING"}
        or _status(attempt) not in {"LEASED", "RUNNING"}
        or lease is None
        or _status(lease) != "ACTIVE"
        or _required_record_text(attempt, "attempt_id")
        != _text(reservation.get("attempt_id"))
        or _text(lease.get("attempt_id")) != _text(reservation.get("attempt_id"))
        or _text(lease.get("lease_id")) != _text(reservation.get("lease_id"))
        or _text(lease.get("owner_id")) != _text(owner_id)
        or _text(reservation.get("owner_id")) != _text(owner_id)
        or lease.get("fencing_token") != reservation.get("fencing_token")
    ):
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "signed reacquisition does not own the exact live execution fence",
        )


def _reacquisition_segment(reservation: Mapping[str, Any]) -> str:
    material = "\n".join(
        (
            _required_record_text(reservation, "reservation_id"),
            str(reservation["reconciliation_recovery_version"]),
            _required_record_text(reservation, "lease_id"),
            str(reservation["fencing_token"]),
        )
    )
    return "reacquire-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


async def _maybe_await(value: Any) -> Any:
    return await value if hasattr(value, "__await__") else value


__all__ = [
    "ExistingOperationResolution",
    "has_blocking_live_recovery_lease",
    "is_signed_reacquired_live_lease",
    "is_signed_reacquired_operation",
    "resolve_existing_operation",
]
