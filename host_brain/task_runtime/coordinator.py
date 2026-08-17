"""受治理持久化 DAG 执行的组合门面。

协调器持有进程范围的装配和公开入口，生产环境注入一个进程共享的本地 registry，
但不拥有持久化写入权。准入必须已原子持久化命令与图；注入的运行时负责保持
outbox 绑定的转移、冻结路由、先验证后提交、验收、取消和精确重放。每个图修订的
唯一写入与 UoW 权威始终是 TaskStore。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedExecution, AdmittedTaskNode
from .accepted_graph_finalization import AcceptedGraphFinalizationRuntime
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
    InternalAgentCheckpointUnitOfWorkPort,
    RuntimeOutboxPublisherPort,
    TaskOutboxFactoryPort,
    TaskStorePort,
    VerificationPort,
)


class GovernedTaskCoordinator:
    """组合并公开受治理 DAG 执行入口，而不把权限判断下放为进程内状态。"""

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        internal_agent_checkpoint_uow: InternalAgentCheckpointUnitOfWorkPort,
        outbox_publisher: RuntimeOutboxPublisherPort,
        outbox_factory: TaskOutboxFactoryPort,
        worker_resolver: GovernedWorkerResolverPort,
        verifier: VerificationPort,
        acceptance: AcceptancePort,
        projection_reader: ExecutionProjectionReaderPort | None = None,
        review_runtime: TaskNodeReviewRuntime | None = None,
        node_grant_verifier: Any | None = None,
        acceptance_effects_enabled: bool = False,
        bounded_fanout_enabled: bool = False,
        atomic_graph_success_enabled: bool = False,
        fanout_event_buffer_size: int = 32,
        execution_registry: LocalGraphExecutionRegistry | None = None,
        owner_id: str,
        lease_ttl_ms: int = 120_000,
        operation_ttl_ms: int = 120_000,
        publish_limit: int = 200,
    ) -> None:
        # Coordinator 只组装进程内运行时；所有可恢复状态仍须经 TaskStore 和 outbox 的持久边界。
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
        # Local handles prove process quiescence but never cancellation authority.
        # Isolated callers get a private registry; production injects one shared.
        self._execution_registry = execution_registry or LocalGraphExecutionRegistry()
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
        self._accepted_graph_finalization = AcceptedGraphFinalizationRuntime(
            task_store=task_store,
            node_lifecycle=self._node_lifecycle,
            projection_reader=projection_reader,
            acceptance_effects_enabled=self._acceptance_effects_enabled,
        )
        self._node_execution = DurableNodeExecutionRuntime(
            task_store=task_store,
            checkpoint_uow=internal_agent_checkpoint_uow,
            runtime_outbox=self._runtime_outbox,
            budget_runtime=self._budgets,
            node_lifecycle=self._node_lifecycle,
            node_grant_verifier=node_grant_verifier,
            worker_resolver=self._workers,
            verifier=self._verifier,
            acceptance=self._acceptance,
            review_runtime=self._reviews,
            projection=self._node_execution_projection,
            projection_reader=projection_reader,
            graph_finalizer=self._accepted_graph_finalization,
            owner_id=self._owner_id,
            lease_ttl_ms=self._lease_ttl_ms,
            operation_ttl_ms=self._operation_ttl_ms,
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
            accepted_graph_finalization=self._accepted_graph_finalization,
            projection_reader=self._projection_reader,
            fanout_event_buffer_size=self._fanout_event_buffer_size,
        )

    def supports_completion_policy(self, value: str | None) -> bool:
        """委托调度器判断完成策略是否已在此组合中明确启用。"""
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
        """将取消请求交给持久化取消运行时；本地 registry 不构成取消授权。"""
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
        denied_review_node_id: str | None = None,
    ) -> dict[str, Any]:
        """消费流式主链并返回唯一 durable 终态，不能以“未抛异常”推断成功。"""
        # 非流式入口消费同一事件链，只接受已投影的终态，不能凭“没有异常”推断任务成功。
        terminal: dict[str, Any] | None = None
        async for event in self.execute_stream(
            admitted=admitted,
            command=command,
            work_order=work_order,
            node_grant_refs=node_grant_refs,
            denied_review_node_id=denied_review_node_id,
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
        denied_review_node_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """在本地静止追踪范围内转发调度事件；租约和取消边界仍由持久层维护。"""
        # 准入快照先重验；本地 registry 仅追踪进程静止状态，并不替代持久租约或取消权威。
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
                denied_review_node_id=denied_review_node_id,
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
