"""Fail-closed admission and readiness phase for durable DAG scheduling."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import inspect
from typing import Any

from natsume_contracts.task.v1 import HostCommand
from host_brain.contracts.agent_runtime.task_graph import TaskGraphCompletionPolicy

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext, DurableBudgetRuntime
from .dag_scheduling_records import _validated_node_grant_refs
from .dag_scheduling_review_denial import normalize_denied_review_node
from .errors import (
    GOVERNED_ADMISSION_INVALID,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)
from .graph_cancellation_runtime import GraphCancellationRuntime
from .ports import TaskStorePort


@dataclass(frozen=True, slots=True)
class DagSchedulingPreflight:
    """Validated immutable inputs and the one mutable grant map for a run."""

    grant_refs: dict[str, str]
    parallel_success_grant_refs: dict[str, str]
    node_ids: frozenset[str]
    denied_review_node: str | None
    parallel_success_enabled: bool
    cancellation_event: dict[str, Any] | None
    budget: BudgetRuntimeContext | None


async def prepare_dag_scheduling_preflight(
    *,
    admitted: AdmittedExecution,
    command: HostCommand,
    work_order: Any,
    node_grant_refs: Mapping[str, str] | None,
    denied_review_node_id: str | None,
    store: TaskStorePort,
    budgets: DurableBudgetRuntime,
    graph_cancellations: GraphCancellationRuntime,
    supports_completion_policy: Callable[[str | None], bool],
) -> DagSchedulingPreflight:
    """Validate admission, readiness, cancellation, and shared Budget once."""

    admitted.validate()
    if not isinstance(command, HostCommand) or command.command_id != admitted.command_id:
        raise GovernedRuntimeError(
            GOVERNED_ADMISSION_INVALID,
            "runtime command differs from admitted command",
        )
    grant_refs = _validated_node_grant_refs(admitted, node_grant_refs)
    parallel_success_grant_refs = dict(grant_refs)
    node_ids = frozenset(binding.node_id for binding in admitted.nodes)
    denied_review_node = normalize_denied_review_node(
        denied_review_node_id,
        node_ids,
        grant_refs,
    )
    parallel_success_enabled = (
        admitted.completion_policy
        == TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value
    )
    if parallel_success_enabled and (
        not supports_completion_policy(admitted.completion_policy)
        or not admitted.result_node_id
        or (not denied_review_node and set(grant_refs) != node_ids)
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "TaskGraph requires atomic success semantics that this runtime cannot execute",
        )
    await _maybe_await(store.assert_dag_scheduling_ready())
    await _maybe_await(store.assert_parallel_settlement_decision_ready())
    await _maybe_await(store.assert_graph_cancellation_ready())
    if parallel_success_enabled:
        await _maybe_await(store.assert_parallel_success_decision_ready())
    cancellation_event = (
        await graph_cancellations.continue_requested_graph_cancellation(
            admitted=admitted,
            command=command,
            work_order=work_order,
        )
    )
    budget = (
        None
        if cancellation_event is not None
        else await budgets.ensure_active(admitted=admitted, work_order=work_order)
    )
    return DagSchedulingPreflight(
        grant_refs=grant_refs,
        parallel_success_grant_refs=parallel_success_grant_refs,
        node_ids=node_ids,
        denied_review_node=denied_review_node,
        parallel_success_enabled=parallel_success_enabled,
        cancellation_event=cancellation_event,
        budget=budget,
    )


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = ["DagSchedulingPreflight", "prepare_dag_scheduling_preflight"]
