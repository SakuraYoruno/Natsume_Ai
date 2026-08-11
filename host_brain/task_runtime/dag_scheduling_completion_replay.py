"""Read-only guard for an exact completed Internal checkpoint replay."""

from __future__ import annotations

import inspect
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedExecution
from .internal_checkpoint_budget_rejection import requires_internal_terminal_bundle


async def is_completed_internal_checkpoint_without_parallel_decision(
    *,
    task_store: Any,
    admitted: AdmittedExecution,
    command: HostCommand,
    graph_status: str,
) -> bool:
    """Allow node replay only for the exact recovery-completed Internal route."""

    if graph_status != "COMPLETED" or not requires_internal_terminal_bundle(
        admitted,
        command,
    ):
        return False
    decision = task_store.get_latest_parallel_success_decision(
        admitted.graph_id,
        admitted.graph_revision,
    )
    if inspect.isawaitable(decision):
        decision = await decision
    return decision is None


__all__ = ["is_completed_internal_checkpoint_without_parallel_decision"]
