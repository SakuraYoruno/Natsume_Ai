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
    GOVERNED_CHECKPOINT_BUDGET_CONFLICT,
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
from .operation_recovery_hold import persist_operation_recovery_hold
from .ports import (
    GovernedWorkerResolverPort,
    InternalAgentCheckpointUnitOfWorkPort,
    TaskStorePort,
)
from .runtime_outbox import RuntimeOutbox


_logger = logging.getLogger("Natsume.task_runtime.existing_operation_resolution")


# 解析结果强制分三类：持久阻塞、已提交检查点续跑、或有签名证明的无副作用重新取得。
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

    # 先闭合预约与 Attempt 血缘；相同 reservation_id 但不同图/业务指纹也不能被恢复代码接纳。
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
        # 内部 Agent 的密封检查点可补齐部分恢复证据，但任何校验失败仍保持阻塞而非降级重放。
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
        # 只有“无副作用”的签名对账和精确新租约同时成立，才允许重新进入 worker 执行段。
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
        # RESERVED/RECOVERY_REQUIRED 没有可验证回执时一律创建恢复 hold，等待 COMMIT、RELEASE 或 REACQUIRE 权威动作。
        active_lease = await _maybe_await(
            task_store.get_active_lease(_required_record_text(attempt, "attempt_id"))
        )
        recovery_case: dict[str, Any] | None = None
        if status in {"RESERVED", "RECOVERY_REQUIRED"} and not (
            status == "RESERVED" and active_lease is not None
        ):
            recovery_case = await persist_operation_recovery_hold(
                task_store=task_store,
                admitted=admitted,
                budget=budget,
                attempt_id=_required_record_text(attempt, "attempt_id"),
                reason_code=(
                    _text(refreshed.get("recovery_reason_code"))
                    or GOVERNED_OPERATION_RECOVERY_REQUIRED
                ),
            )
        projection = {
            **operation_recovery.unresolved_projection(refreshed),
            "online_takeover_state": (
                "WAITING_FOR_LEASE_EXPIRY"
                if status == "RESERVED" and active_lease is not None
                else "SIGNED_RECONCILIATION_REQUIRED"
            ),
            "budget": budget.execution_payload(),
            "recovery_case_ref": (
                recovery_case.get("case_ref") if recovery_case else None
            ),
            "recovery_case_digest": (
                recovery_case.get("subject_sha256") if recovery_case else None
            ),
            "recovery_case_kind": (
                recovery_case.get("case_kind") if recovery_case else None
            ),
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
        # COMMITTED 表示 worker 已跨边界，因此接管走续验/验收路径，而不是再次调用 worker。
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
        checkpoint_state = (
            "CONFLICT"
            if exc.reason_code == GOVERNED_CHECKPOINT_BUDGET_CONFLICT
            else "INVALID"
        )
        recovery_case = await persist_operation_recovery_hold(
            task_store=task_store,
            admitted=admitted,
            budget=budget,
            attempt_id=_required_record_text(attempt, "attempt_id"),
            reason_code=exc.reason_code,
            checkpoint_state=checkpoint_state,
        )
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
                "recovery_case_ref": recovery_case["case_ref"],
                "recovery_case_digest": recovery_case["subject_sha256"],
                "recovery_case_kind": recovery_case["case_kind"],
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
    # REACQUIRE 必须同时证明无副作用、证明材料完整且状态版本恰好由对账动作推进一格。
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

    # 除精确的签名替代租约外，任何未到期活跃租约都阻止调度器接管，避免双 owner。
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
