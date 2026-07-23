"""Read-only reconstruction of durable parallel terminal outcomes."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from .admission import AdmittedExecution
from .coordinator_receipts import terminal_event
from .coordinator_records import _ParallelNodeOutcome, _status
from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError


async def reconstruct_parallel_outcomes(
    *,
    admitted: AdmittedExecution,
    graph: Mapping[str, Any],
    durable_nodes: Mapping[str, Mapping[str, Any]],
    node_execution_projection: Any,
) -> tuple[_ParallelNodeOutcome, ...]:
    """Reopen only durable terminal projections; never execute a worker."""

    reconstructable = {
        "ACCEPTED",
        "FAILED",
        "RETRY_PENDING",
        "VERIFICATION_FAILED",
        "WAITING_ACCEPTANCE_REVIEW",
        "CANCELLED",
    }
    outcomes: list[_ParallelNodeOutcome] = []
    for binding in admitted.nodes:
        node = durable_nodes.get(binding.node_id)
        if not isinstance(node, Mapping) or _status(node) not in reconstructable:
            continue
        node_admission = admitted.for_node(binding.node_id)
        receipt = node_execution_projection.terminal_replay(
            node_admission,
            graph=graph,
            node=node,
            allow_incomplete_atomic_graph_outcome=True,
        )
        if inspect.isawaitable(receipt):
            receipt = await receipt
        if receipt is None:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel re-entry cannot reconstruct TaskNode {binding.node_id}",
            )
        outcomes.append(
            _ParallelNodeOutcome.from_terminal_event(
                node_id=binding.node_id,
                event=terminal_event(receipt),
            )
        )
    return tuple(outcomes)


__all__ = ["reconstruct_parallel_outcomes"]
