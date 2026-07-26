"""Runtime boundary for freezing one durable Operation recovery hold."""

from __future__ import annotations

from collections.abc import Mapping
import inspect
from typing import Any

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext
from .errors import GOVERNED_OPERATION_RECOVERY_REQUIRED, GovernedRuntimeError
from .ports import TaskStorePort


async def persist_operation_recovery_hold(
    *,
    task_store: TaskStorePort,
    admitted: AdmittedExecution,
    budget: BudgetRuntimeContext,
    attempt_id: str,
    reason_code: str,
    checkpoint_state: str | None = None,
) -> dict[str, Any]:
    """Persist ambiguity before returning a recoverable execution receipt."""

    result = task_store.record_operation_recovery_hold(
        graph_id=admitted.graph_id,
        graph_revision=admitted.graph_revision,
        node_id=admitted.node_id,
        attempt_id=str(attempt_id or "").strip(),
        reservation_id=admitted.reservation_id,
        budget_id=budget.budget_id,
        reason_code=str(reason_code or "").strip().upper(),
        checkpoint_state=checkpoint_state,
    )
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, Mapping):
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "Operation recovery hold store returned no durable case",
        )
    case_ref = str(result.get("case_ref") or "").strip()
    digest = str(result.get("subject_sha256") or "").strip()
    if (
        not case_ref.startswith("operation-recovery-hold://sha256/")
        or not digest.startswith("sha256:")
        or result.get("current") is not True
    ):
        raise GovernedRuntimeError(
            GOVERNED_OPERATION_RECOVERY_REQUIRED,
            "Operation recovery hold identity is invalid",
        )
    return dict(result)


__all__ = ["persist_operation_recovery_hold"]
