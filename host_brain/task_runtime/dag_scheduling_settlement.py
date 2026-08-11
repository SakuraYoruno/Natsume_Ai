"""Narrow durable settlement for pre-execution Review holds."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext
from .coordinator_records import _ParallelNodeOutcome, _text
from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError


async def settle_waiting_approval(
    *,
    admitted: AdmittedExecution,
    receipt_event: Mapping[str, Any],
    budget: BudgetRuntimeContext,
    parallel_settlement: Any,
) -> tuple[BudgetRuntimeContext, dict[str, Any]]:
    """Persist the sole blocking settlement for a denied pre-execution gate."""

    receipt = receipt_event.get("execution_receipt")
    node_id = _text(receipt.get("node_id")) if isinstance(receipt, Mapping) else ""
    if not node_id:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "pre-execution review outcome has no TaskNode identity",
        )
    outcome = _ParallelNodeOutcome.from_terminal_event(
        node_id=node_id,
        event=receipt_event,
    )
    budget, terminal = await parallel_settlement.resolve_parallel_outcomes(
        admitted=admitted,
        outcomes=(outcome,),
        budget=budget,
    )
    if terminal is None:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "pre-execution review did not produce a blocking settlement",
        )
    return budget, terminal


__all__ = ["settle_waiting_approval"]
