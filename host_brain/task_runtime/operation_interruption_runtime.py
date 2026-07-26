"""Fail-closed classification of known post-reservation interruptions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
from typing import Any

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext, DurableBudgetRuntime
from .coordinator_receipts import build_execution_receipt
from .coordinator_records import (
    _exception_reason,
    _mapping,
    _required_record_text,
    _status,
    _version,
)
from .errors import (
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GovernedRuntimeError,
)
from .heartbeat import DurableExecutionHeartbeat
from .operation_interruption_case import (
    interruption_recovery_case_projection,
    require_interruption_recovery_case,
)
from .ports import TaskStorePort
from .runtime_outbox import RuntimeOutbox


_DURABLE_INTERRUPTION_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,119}")
_logger = logging.getLogger("Natsume.task_runtime.operation_interruption_runtime")


@dataclass(frozen=True, slots=True)
class OperationInterruptionClassification:
    """Safe projection of one attempted durable interruption marker."""

    record: dict[str, Any] | None
    recovery_case: dict[str, Any] | None
    marker_failure_reason_code: str | None
    marker_exception_type: str | None

    def projection(self) -> dict[str, Any]:
        values: dict[str, Any] = {
            "reservation_status": (
                _status(self.record)
                if self.record is not None
                else "UNKNOWN"
            ),
        }
        if self.record is not None:
            values["operation_recovery_reason_code"] = _required_record_text(
                self.record,
                "recovery_reason_code",
            )
        values.update(interruption_recovery_case_projection(self.recovery_case))
        if self.marker_failure_reason_code is not None:
            values["recovery_marker_reason_code"] = (
                self.marker_failure_reason_code
            )
        return values


def stable_interruption_reason(exc: BaseException, default: str) -> str:
    """Return only a bounded machine reason code, never exception detail."""

    candidate = _exception_reason(exc, default)
    if _DURABLE_INTERRUPTION_REASON.fullmatch(candidate):
        return candidate
    return default


def classify_interrupted_operation(
    *,
    store: TaskStorePort,
    admitted: AdmittedExecution,
    heartbeat: DurableExecutionHeartbeat,
    attempt_id: str,
    lease_id: str,
    owner_id: str,
    fencing_token: int,
    reason_code: str,
) -> OperationInterruptionClassification:
    """Persist a known interruption without deciding its external effect."""

    try:
        reservation_version = heartbeat.latest_reservation_version
        if reservation_version is None:
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                "interrupted operation lost its attached reservation version",
            )
        marked = _mapping(
            store.mark_worker_interruption_recovery_required(
                admitted.reservation_id,
                attempt_id=attempt_id,
                lease_id=lease_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                expected_version=reservation_version,
                reason_code=reason_code,
            ),
            field_name="interrupted operation recovery marker",
        )
        if (
            _required_record_text(marked, "reservation_id")
            != admitted.reservation_id
            or _status(marked) != "RECOVERY_REQUIRED"
            or _version(marked) != reservation_version + 1
            or _required_record_text(marked, "recovery_reason_code")
            != reason_code
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "interrupted operation recovery marker returned a drifting identity",
            )
        recovery_case = require_interruption_recovery_case(
            store=store,
            reservation_id=admitted.reservation_id,
            expected_version=reservation_version + 1,
            reason_code=reason_code,
        )
    except Exception as exc:
        return OperationInterruptionClassification(
            record=None,
            recovery_case=None,
            marker_failure_reason_code=stable_interruption_reason(
                exc,
                GOVERNED_DURABLE_STATE_CONFLICT,
            ),
            marker_exception_type=type(exc).__name__,
        )
    return OperationInterruptionClassification(
        record=marked,
        recovery_case=recovery_case,
        marker_failure_reason_code=None,
        marker_exception_type=None,
    )


def classify_caller_cancellation(
    *,
    store: TaskStorePort,
    admitted: AdmittedExecution,
    heartbeat: DurableExecutionHeartbeat,
    attempt_id: str,
    lease_id: str,
    owner_id: str,
    fencing_token: int,
    reason_code: str,
    request_id: str,
) -> None:
    """Best-effort classification that never replaces caller cancellation."""

    outcome = classify_interrupted_operation(
        store=store,
        admitted=admitted,
        heartbeat=heartbeat,
        attempt_id=attempt_id,
        lease_id=lease_id,
        owner_id=owner_id,
        fencing_token=fencing_token,
        reason_code=reason_code,
    )
    if outcome.marker_failure_reason_code:
        _logger.error(
            "Governed caller cancellation could not be classified durably",
            extra={
                "request_id": request_id,
                "graph_id": admitted.graph_id,
                "graph_revision": admitted.graph_revision,
                "node_id": admitted.node_id,
                "attempt_id": attempt_id,
                "worker_route": admitted.worker_route,
                "reason_code": outcome.marker_failure_reason_code,
                "exception_type": outcome.marker_exception_type,
            },
        )


async def build_worker_interruption_receipt(
    *,
    store: TaskStorePort,
    budgets: DurableBudgetRuntime,
    runtime_outbox: RuntimeOutbox,
    admitted: AdmittedExecution,
    heartbeat: DurableExecutionHeartbeat,
    command: Any,
    budget: BudgetRuntimeContext,
    attempt_id: str,
    lease_id: str,
    owner_id: str,
    fencing_token: int,
    worker_error: Exception,
    worker_started_ns: int,
    worker_time_budget_ms: int | None,
    operation_recovery_reason_code: str,
    worker_failure_default_reason_code: str,
    time_budget_exceeded_reason_code: str,
    budget_conflict_reason_code: str,
    execution_segment: str | None = None,
) -> dict[str, Any]:
    """Settle metering and return a recovery receipt without terminalizing."""

    worker_reason = stable_interruption_reason(
        worker_error,
        worker_failure_default_reason_code,
    )
    interruption = classify_interrupted_operation(
        store=store,
        admitted=admitted,
        heartbeat=heartbeat,
        attempt_id=attempt_id,
        lease_id=lease_id,
        owner_id=owner_id,
        fencing_token=fencing_token,
        reason_code=worker_reason,
    )
    _logger.error(
        "Governed worker execution failed",
        extra={
            "request_id": command.request_id,
            "graph_id": admitted.graph_id,
            "graph_revision": admitted.graph_revision,
            "node_id": admitted.node_id,
            "attempt_id": attempt_id,
            "worker_route": admitted.worker_route,
            "reason_code": worker_reason,
            "recovery_marker_reason_code": (
                interruption.marker_failure_reason_code
            ),
            "exception_type": type(worker_error).__name__,
        },
    )
    settlement_reason: str | None = None
    evidence_refs = (
        (
            _required_record_text(interruption.recovery_case, "case_ref"),
        )
        if interruption.recovery_case is not None
        else ()
    )
    try:
        charge = await budgets.settle_interrupted_worker(
            budget,
            node_id=admitted.node_id,
            attempt_id=attempt_id,
            worker_started_ns=worker_started_ns,
            worker_failure_reason=worker_reason,
            time_budget_ms=worker_time_budget_ms,
            execution_segment=execution_segment,
        )
        budget = charge.context
        evidence_refs = (*evidence_refs, charge.evidence_ref)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        settlement_reason = stable_interruption_reason(
            exc,
            budget_conflict_reason_code,
        )
    await runtime_outbox.publish_pending(best_effort=True)
    projection = {
        "worker_failure_reason_code": worker_reason,
        **interruption.projection(),
        "budget": budget.execution_payload(),
    }
    if worker_reason == time_budget_exceeded_reason_code:
        projection["time_budget_ms"] = worker_time_budget_ms
    if settlement_reason is not None:
        projection["budget_settlement_reason_code"] = settlement_reason
    return build_execution_receipt(
        admitted,
        attempt_id=attempt_id,
        status="RECOVERY_REQUIRED",
        verification_status="NOT_RUN",
        accepted=False,
        reason_code=operation_recovery_reason_code,
        evidence_refs=evidence_refs,
        projection=projection,
    )


__all__ = [
    "OperationInterruptionClassification",
    "build_worker_interruption_receipt",
    "classify_caller_cancellation",
    "classify_interrupted_operation",
    "stable_interruption_reason",
]
