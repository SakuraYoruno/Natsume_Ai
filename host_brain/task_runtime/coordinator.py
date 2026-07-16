"""Composition facade for governed durable DAG execution.

The coordinator owns process-scoped wiring, public entry points, and the local
execution registry; it does not own DAG scheduling or durable mutation logic.
Admission must already have atomically persisted the command and graph.  The
injected runtimes preserve outbox-bound transitions, frozen node routes,
verification-before-commit, acceptance, cancellation, and exact replay.  The
TaskStore remains the sole mutation and unit-of-work authority for every
durable graph revision.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedExecution, AdmittedTaskNode
from .budget_runtime import DurableBudgetRuntime
from .coordinator_receipts import GovernedExecutionReceipt
from .coordinator_records import _text
from .dag_scheduling_runtime import DurableDagSchedulingRuntime
from .graph_cancellation_runtime import GraphCancellationRuntime
from .local_graph_execution_registry import LocalGraphExecutionRegistry
from .node_execution_projection import NodeExecutionProjection
from .node_execution_runtime import DurableNodeExecutionRuntime
from .node_lifecycle_runtime import DurableNodeLifecycleRuntime
from .parallel_success_runtime import ParallelSuccessRuntime
from .parallel_settlement_runtime import ParallelSettlementRuntime
from .runtime_outbox import RuntimeOutbox
from .errors import (
    GOVERNED_ACCEPTANCE_FAILED,
    GOVERNED_ADMISSION_INVALID,
    GOVERNED_ASSIGNMENT_CONFLICT,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_EXECUTION_IN_PROGRESS,
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GOVERNED_RECEIPT_COMMIT_FAILED,
    GOVERNED_VERIFICATION_FAILED,
    GOVERNED_WORKER_COMMIT_INVALID,
    GOVERNED_WORKER_FAILED,
    GovernedRuntimeError,
)
from .review_runtime import TaskNodeReviewRuntime
from .ports import (
    AcceptancePort,
    ExecutionProjectionReaderPort,
    GovernedWorkerResolverPort,
    RuntimeOutboxPublisherPort,
    TaskOutboxFactoryPort,
    TaskStorePort,
    VerificationPort,
)


class GovernedTaskCoordinator:
    """Compose and expose governed durable DAG execution."""

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        outbox_publisher: RuntimeOutboxPublisherPort,
        outbox_factory: TaskOutboxFactoryPort,
        worker_resolver: GovernedWorkerResolverPort,
        verifier: VerificationPort,
        acceptance: AcceptancePort,
        projection_reader: ExecutionProjectionReaderPort | None = None,
        review_runtime: TaskNodeReviewRuntime | None = None,
        acceptance_effects_enabled: bool = False,
        bounded_fanout_enabled: bool = False,
        atomic_graph_success_enabled: bool = False,
        fanout_event_buffer_size: int = 32,
        owner_id: str,
        lease_ttl_ms: int = 120_000,
        operation_ttl_ms: int = 120_000,
        publish_limit: int = 200,
    ) -> None:
        owner = _text(owner_id)
        if not owner:
            raise ValueError("owner_id is required")
        if int(lease_ttl_ms) <= 0 or int(operation_ttl_ms) <= 0:
            raise ValueError("lease and operation TTLs must be positive")
        if int(fanout_event_buffer_size) <= 0:
            raise ValueError("fanout_event_buffer_size must be positive")
        self._store = task_store
        self._runtime_outbox = RuntimeOutbox(
            publisher=outbox_publisher,
            factory=outbox_factory,
            publish_limit=publish_limit,
        )
        self._workers = worker_resolver
        self._verifier = verifier
        self._acceptance = acceptance
        self._projection_reader = projection_reader
        self._reviews = review_runtime
        self._acceptance_effects_enabled = bool(acceptance_effects_enabled)
        self._bounded_fanout_enabled = bool(bounded_fanout_enabled)
        self._atomic_graph_success_enabled = bool(atomic_graph_success_enabled)
        self._fanout_event_buffer_size = int(fanout_event_buffer_size)
        self._owner_id = owner
        self._lease_ttl_ms = int(lease_ttl_ms)
        self._operation_ttl_ms = int(operation_ttl_ms)
        # Process-local task handles only accelerate an already-durable graph
        # cancellation request.  They are never consulted as cancellation
        # authority and may be empty after restart or in another process.
        self._execution_registry = LocalGraphExecutionRegistry()
        self._graph_cancellations = GraphCancellationRuntime(
            task_store=task_store,
            runtime_outbox=self._runtime_outbox,
            execution_registry=self._execution_registry,
            lease_ttl_ms=self._lease_ttl_ms,
        )
        self._budgets = DurableBudgetRuntime(task_store=task_store, outbox_factory=outbox_factory)
        self._node_lifecycle = DurableNodeLifecycleRuntime(
            task_store=task_store,
            budget_runtime=self._budgets,
            runtime_outbox=self._runtime_outbox,
            owner_id=self._owner_id,
        )
        self._node_execution_projection = NodeExecutionProjection(
            task_store=task_store,
            projection_reader=projection_reader,
        )
        self._node_execution = DurableNodeExecutionRuntime(
            task_store=task_store,
            runtime_outbox=self._runtime_outbox,
            budget_runtime=self._budgets,
            node_lifecycle=self._node_lifecycle,
            worker_resolver=self._workers,
            verifier=self._verifier,
            acceptance=self._acceptance,
            review_runtime=self._reviews,
            projection=self._node_execution_projection,
            owner_id=self._owner_id,
            lease_ttl_ms=self._lease_ttl_ms,
            operation_ttl_ms=self._operation_ttl_ms,
            acceptance_effects_enabled=self._acceptance_effects_enabled,
        )
        self._parallel_success = ParallelSuccessRuntime(
            task_store=task_store,
            runtime_outbox=self._runtime_outbox,
            budget_runtime=self._budgets,
            projection_reader=projection_reader,
            node_execution_projection=self._node_execution_projection,
        )
        self._parallel_settlement = ParallelSettlementRuntime(
            task_store=task_store,
            budget_runtime=self._budgets,
            runtime_outbox=self._runtime_outbox,
        )
        self._dag_scheduling = DurableDagSchedulingRuntime(
            task_store=task_store,
            runtime_outbox=self._runtime_outbox,
            budget_runtime=self._budgets,
            node_lifecycle=self._node_lifecycle,
            node_execution_projection=self._node_execution_projection,
            node_execution=self._node_execution,
            parallel_settlement=self._parallel_settlement,
            parallel_success=self._parallel_success,
            graph_cancellations=self._graph_cancellations,
            review_runtime=self._reviews,
            bounded_fanout_enabled=self._bounded_fanout_enabled,
            atomic_graph_success_enabled=self._atomic_graph_success_enabled,
            acceptance_effects_enabled=self._acceptance_effects_enabled,
            projection_reader=self._projection_reader,
            fanout_event_buffer_size=self._fanout_event_buffer_size,
        )

    def supports_completion_policy(self, value: str | None) -> bool:
        return self._dag_scheduling.supports_completion_policy(value)

    async def request_graph_cancellation(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        request_id: str,
        request_identity: str,
        source: str,
        reason_code: str,
    ) -> dict[str, Any]:
        return await self._graph_cancellations.request_graph_cancellation(
            admitted=admitted,
            command=command,
            work_order=work_order,
            request_id=request_id,
            request_identity=request_identity,
            source=source,
            reason_code=reason_code,
        )

    async def execute(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        node_grant_refs: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        terminal: dict[str, Any] | None = None
        async for event in self.execute_stream(
            admitted=admitted,
            command=command,
            work_order=work_order,
            node_grant_refs=node_grant_refs,
        ):
            if bool(event.get("done")):
                terminal = dict(event)
        if terminal is None:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "governed execution produced no durable terminal projection",
            )
        return terminal

    async def execute_stream(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        node_grant_refs: Mapping[str, str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        admitted.validate()
        async with self._execution_registry.track(
            graph_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
        ):
            async for event in self._dag_scheduling.execute_stream(
                admitted=admitted,
                command=command,
                work_order=work_order,
                node_grant_refs=node_grant_refs,
            ):
                yield event


__all__ = [
    "AdmittedExecution",
    "AdmittedTaskNode",
    "GOVERNED_ACCEPTANCE_FAILED",
    "GOVERNED_ADMISSION_INVALID",
    "GOVERNED_ASSIGNMENT_CONFLICT",
    "GOVERNED_DURABLE_STATE_CONFLICT",
    "GOVERNED_EXECUTION_IN_PROGRESS",
    "GOVERNED_OPERATION_RECOVERY_REQUIRED",
    "GOVERNED_RECEIPT_COMMIT_FAILED",
    "GOVERNED_VERIFICATION_FAILED",
    "GOVERNED_WORKER_COMMIT_INVALID",
    "GOVERNED_WORKER_FAILED",
    "GovernedExecutionReceipt",
    "GovernedRuntimeError",
    "GovernedTaskCoordinator",
]
