"""Canonical terminal settlement for a committed checkpoint re-entry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext
from .coordinator_records import _ParallelNodeOutcome, _text
from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError
from .internal_checkpoint_budget_rejection import is_internal_budget_terminal
from .parallel_settlement_runtime import ParallelSettlementRuntime


async def settle_committed_checkpoint_reentry(
    *,
    admitted: AdmittedExecution,
    recovery_admission: AdmittedExecution,
    command: Any,
    recovery_terminal: Mapping[str, Any],
    budget: BudgetRuntimeContext,
    parallel_success_enabled: bool,
    parallel_settlement: ParallelSettlementRuntime,
) -> tuple[BudgetRuntimeContext, dict[str, Any] | None]:
    """Keep a child checkpoint terminal internal until Graph settlement."""

    terminal = dict(recovery_terminal)
    receipt = terminal.get("execution_receipt")
    if not isinstance(receipt, Mapping):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "committed checkpoint recovery terminal has no receipt",
        )
    if (
        _text(receipt.get("status")).upper() == "RECOVERY_REQUIRED"
        or len(admitted.nodes) == 1
        or (
            parallel_success_enabled
            and is_internal_budget_terminal(admitted, command, receipt)
        )
    ):
        return budget, terminal

    outcome = _ParallelNodeOutcome.from_terminal_event(
        node_id=recovery_admission.node_id,
        event=terminal,
    )
    return await parallel_settlement.resolve_parallel_outcomes(
        admitted=admitted,
        outcomes=(outcome,),
        budget=budget,
    )


__all__ = ["settle_committed_checkpoint_reentry"]
