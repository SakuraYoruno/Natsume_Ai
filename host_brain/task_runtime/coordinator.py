"""Durable DAG Task Runtime coordinator.

The coordinator is the execution authority for an admitted Host command.  It
does not create commands or graphs: admission must already have atomically
persisted one graph whose roots are READY and dependents are BLOCKED.  Every
subsequent state mutation is outbox-bound, each frozen node route selects
exactly one worker, and a worker's terminal COMMIT is withheld until
verification and acceptance both pass.  A dependent becomes READY only after
every dependency is ACCEPTED.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Mapping
from typing import Any

from natsume_contracts.task.v1 import HostCommand
from host_brain.contracts.agent_runtime.budget_policy import BudgetReservationStatus
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphCompletionPolicy,
    TaskNodeStatus,
)

from .admission import AdmittedExecution, AdmittedTaskNode
from .coordinator_records import (
    _ParallelNodeOutcome,
    _attempt_id,
    _assert_durable_identity,
    _evidence_refs,
    _mapping,
    _nested_mapping,
    _node_input_refs,
    _optional_record_non_negative_int,
    _record_evidence_refs,
    _record_non_negative_int,
    _record_positive_int,
    _required_record_text,
    _reservation_result,
    _review_evidence_refs,
    _status,
    _text,
    _version,
)
from .coordinator_receipts import (
    GovernedExecutionReceipt,
    _digest_from_receipt_ref,
    _worker_receipt_ref,
    build_execution_receipt,
    terminal_event,
)
from .graph_cancellation_runtime import GraphCancellationRuntime
from .local_graph_execution_registry import LocalGraphExecutionRegistry
from .node_execution_projection import NodeExecutionProjection
from .node_execution_records import build_review_context
from .node_execution_runtime import DurableNodeExecutionRuntime
from .node_lifecycle_runtime import DurableNodeLifecycleRuntime
from .parallel_success_runtime import ParallelSuccessRuntime
from .parallel_settlement_runtime import ParallelSettlementRuntime
from .runtime_outbox import RuntimeOutbox
from .budget_runtime import DurableBudgetRuntime
from .errors import (
    GOVERNED_ACCEPTANCE_FAILED,
    GOVERNED_ADMISSION_INVALID,
    GOVERNED_ASSIGNMENT_CONFLICT,
    GOVERNED_BUDGET_EXCEEDED,
    GOVERNED_BUDGET_STATE_CONFLICT,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_EXECUTION_IN_PROGRESS,
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GOVERNED_RECEIPT_COMMIT_FAILED,
    GOVERNED_REVIEW_DENIED,
    GOVERNED_REVIEW_REQUIRED,
    GOVERNED_VERIFICATION_FAILED,
    GOVERNED_WORKER_COMMIT_INVALID,
    GOVERNED_WORKER_FAILED,
    GovernedRuntimeError,
)
from .dag_scheduler import (
    DagNodeScheduleCandidate,
    DagScheduleInputError,
    compute_dag_schedule,
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
    """Advance one durable DAG through dependency-gated node executions."""

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

    def supports_completion_policy(self, value: str | None) -> bool:
        """Report process capability without selecting a Graph's semantics."""

        normalized = _text(value)
        if not normalized or normalized == TaskGraphCompletionPolicy.LEGACY_NODE_TERMINAL_V1.value:
            return True
        if normalized != TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value:
            return False
        return bool(
            self._atomic_graph_success_enabled
            and self._acceptance_effects_enabled
            and self._projection_reader is not None
        )

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
            async for event in self._execute_stream_impl(
                admitted=admitted,
                command=command,
                work_order=work_order,
                node_grant_refs=node_grant_refs,
            ):
                yield event

    async def _execute_stream_impl(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        node_grant_refs: Mapping[str, str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        admitted.validate()
        if not isinstance(command, HostCommand) or command.command_id != admitted.command_id:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "runtime command differs from admitted command",
        )
        grant_refs = _validated_node_grant_refs(admitted, node_grant_refs)
        parallel_success_enabled = (
            admitted.completion_policy
            == TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value
        )
        if parallel_success_enabled and (
            not self.supports_completion_policy(admitted.completion_policy)
            or not admitted.result_node_id
            or set(grant_refs) != {binding.node_id for binding in admitted.nodes}
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "TaskGraph requires atomic success semantics that this runtime cannot execute",
            )
        await _maybe_await(self._store.assert_dag_scheduling_ready())
        await _maybe_await(self._store.assert_parallel_settlement_decision_ready())
        await _maybe_await(self._store.assert_graph_cancellation_ready())
        if parallel_success_enabled:
            await _maybe_await(self._store.assert_parallel_success_decision_ready())
        cancellation_event = (
            await self._graph_cancellations.continue_requested_graph_cancellation(
                admitted=admitted,
                command=command,
                work_order=work_order,
            )
        )
        if cancellation_event is not None:
            yield cancellation_event
            return
        budget = await self._budgets.ensure_active(admitted=admitted, work_order=work_order)

        while True:
            graph = await _maybe_await(
                self._store.get_graph(admitted.graph_id, admitted.graph_revision)
            )
            durable_nodes: dict[str, dict[str, Any]] = {}
            for binding in admitted.nodes:
                node_admission = admitted.for_node(binding.node_id)
                node = dict(
                    await _maybe_await(
                        self._store.get_node(
                            admitted.graph_id,
                            admitted.graph_revision,
                            binding.node_id,
                        )
                    )
                )
                _assert_durable_identity(node_admission, graph=graph, node=node)
                durable_nodes[binding.node_id] = node

            graph_status = _status(graph)
            statuses = {
                node_id: _status(node)
                for node_id, node in durable_nodes.items()
            }
            final_admission = admitted.for_node(
                admitted.result_node_id or admitted.nodes[-1].node_id
            )

            unresolved_failure: tuple[AdmittedExecution, dict[str, Any]] | None = None
            for binding in admitted.nodes:
                if statuses[binding.node_id] not in {
                    "FAILED",
                    "VERIFICATION_FAILED",
                    "CANCELLED",
                }:
                    continue
                failed_admission = admitted.for_node(binding.node_id)
                reservation = await self._node_execution_projection.try_operation(
                    failed_admission.reservation_id
                )
                if _status(reservation or {}) in {"RESERVED", "RECOVERY_REQUIRED"}:
                    unresolved_failure = (failed_admission, reservation or {})
                    break
            if unresolved_failure is not None:
                recovery_admission, reservation = unresolved_failure
                attempts = await self._node_execution_projection.attempts_for_node(recovery_admission)
                attempt_id = (
                    _required_record_text(attempts[-1], "attempt_id")
                    if attempts
                    else _attempt_id(recovery_admission, attempt_no=1)
                )
                receipt = build_execution_receipt(
                    recovery_admission,
                    attempt_id=attempt_id,
                    status="RECOVERY_REQUIRED",
                    verification_status="NOT_RUN",
                    accepted=False,
                    reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
                    projection={
                        "reservation_status": _status(reservation),
                        "reservation_id": recovery_admission.reservation_id,
                        "budget": budget.execution_payload(),
                    },
                )
                yield terminal_event(receipt)
                return

            reentry_terminal_statuses = {
                "FAILED",
                "VERIFICATION_FAILED",
                "CANCELLED",
                "WAITING_ACCEPTANCE_REVIEW",
            }
            if graph_status == "ACTIVE" and len(admitted.nodes) > 1:
                budget = await self._budgets.refresh(budget)
            if (
                graph_status == "ACTIVE"
                and len(admitted.nodes) > 1
                and (
                    budget.status == "EXCEEDED"
                    or set(statuses.values()).intersection(reentry_terminal_statuses)
                )
            ):
                await self._parallel_settlement.assert_parallel_reentry_idle(
                    admitted=admitted,
                    durable_nodes=durable_nodes,
                )
                outcomes = await self._reentered_parallel_outcomes(
                    admitted=admitted,
                    graph=graph,
                    durable_nodes=durable_nodes,
                )
                if not outcomes or all(outcome.accepted for outcome in outcomes):
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "parallel re-entry has no durable non-accepted outcome",
                    )
                budget, parallel_terminal = (
                    await self._parallel_settlement.resolve_parallel_outcomes(
                        admitted=admitted,
                        outcomes=outcomes,
                        budget=budget,
                    )
                )
                if parallel_terminal is None:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "parallel re-entry did not produce a terminal decision",
                    )
                yield parallel_terminal
                return

            pre_review_node_id = next(
                (
                    binding.node_id
                    for binding in admitted.nodes
                    if statuses[binding.node_id] == "WAITING_APPROVAL"
                ),
                None,
            )
            if pre_review_node_id is not None and graph_status in {
                "CREATED",
                "ACTIVE",
                "BLOCKED",
            }:
                if self._reviews is None:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "TaskNode is WAITING_APPROVAL but no durable Review runtime is wired",
                    )
                review_admission = admitted.for_node(pre_review_node_id)
                review_node = durable_nodes[pre_review_node_id]
                if graph_status in {"CREATED", "ACTIVE"}:
                    graph = await self._node_lifecycle.transition_graph(review_admission, graph, "BLOCKED")
                    graph_status = _status(graph)
                gate = self._reviews.pre_execution_gate(
                    work_order=work_order,
                    context=build_review_context(
                        admitted=review_admission,
                        command=command,
                        work_order=work_order,
                        node=review_node,
                        attempt_id=None,
                        reason="TaskNode requires durable pre-execution approval",
                    ),
                )
                review_refs = _review_evidence_refs(gate)
                pre_review_refs = tuple(
                    dict.fromkeys((*_record_evidence_refs(review_node), *review_refs))
                )
                if gate.allowed:
                    await self._node_lifecycle.transition_node(
                        review_admission,
                        review_node,
                        "READY",
                        evidence_refs=pre_review_refs,
                    )
                    if graph_status == "BLOCKED":
                        graph = await self._node_lifecycle.transition_graph(
                            review_admission,
                            graph,
                            "ACTIVE",
                        )
                    await self._runtime_outbox.publish_pending()
                    continue
                if gate.disposition == "REVIEW_DENIED":
                    await self._node_lifecycle.cancel_waiting_review_node(
                        review_admission,
                        review_node,
                        evidence_refs=pre_review_refs,
                    )
                    if graph_status == "BLOCKED":
                        await self._node_lifecycle.transition_graph(review_admission, graph, "CANCELLED")
                    budget = await self._budgets.finalize(
                        budget, target_status=BudgetReservationStatus.RELEASED
                    )
                    reason_code = GOVERNED_REVIEW_DENIED
                    status = "REVIEW_DENIED"
                else:
                    reason_code = GOVERNED_REVIEW_REQUIRED
                    status = "WAITING_APPROVAL"
                await self._runtime_outbox.publish_pending()
                receipt = build_execution_receipt(
                    review_admission,
                    attempt_id=_attempt_id(review_admission, attempt_no=1),
                    status=status,
                    verification_status="NOT_RUN",
                    accepted=False,
                    reason_code=reason_code,
                    evidence_refs=pre_review_refs,
                    projection={
                        "review": gate.to_dict(),
                        "budget": budget.execution_payload(),
                    },
                )
                yield terminal_event(receipt)
                return

            post_review_node_id = next(
                (
                    binding.node_id
                    for binding in admitted.nodes
                    if statuses[binding.node_id] == "WAITING_ACCEPTANCE_REVIEW"
                ),
                None,
            )
            if (
                post_review_node_id is not None
                and self._reviews is not None
                and graph_status in {"ACTIVE", "BLOCKED"}
            ):
                review_admission = admitted.for_node(post_review_node_id)
                review_node = durable_nodes[post_review_node_id]
                if graph_status == "ACTIVE":
                    graph = await self._node_lifecycle.transition_graph(review_admission, graph, "BLOCKED")
                    graph_status = _status(graph)
                attempts = await self._node_execution_projection.attempts_for_node(review_admission)
                if not attempts:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "post-execution review has no durable TaskAttempt",
                    )
                attempt_id = _required_record_text(attempts[-1], "attempt_id")
                gate = self._reviews.post_execution_acceptance_gate(
                    context=build_review_context(
                        admitted=review_admission,
                        command=command,
                        work_order=work_order,
                        node=review_node,
                        attempt_id=attempt_id,
                        reason="Acceptance policy requires durable post-execution review",
                    )
                )
                review_refs = _review_evidence_refs(gate)
                post_review_refs = tuple(
                    dict.fromkeys((*_record_evidence_refs(review_node), *review_refs))
                )
                if gate.allowed:
                    await self._node_lifecycle.transition_node(
                        review_admission,
                        review_node,
                        "ACCEPTED",
                        evidence_refs=post_review_refs,
                        acceptance_judgement={
                            "schema_version": "Natsume.agent.acceptance_judgement.v1",
                            "judgement_id": f"review-accept::{gate.review_id}",
                            "work_order_id": review_admission.work_order_id,
                            "task_graph_id": review_admission.graph_id,
                            "task_node_id": review_admission.node_id,
                            "attempt_id": attempt_id,
                            "decision": "ACCEPT",
                            "reason_code": None,
                            "evidence_refs": list(post_review_refs),
                            "criteria": [],
                            "critic_candidate": {
                                "source": "durable_review",
                                "review": gate.to_dict(),
                            },
                            "started_at_ms": command.created_at_ms,
                            "finished_at_ms": command.created_at_ms,
                        },
                    )
                    if graph_status == "BLOCKED":
                        await self._node_lifecycle.transition_graph(review_admission, graph, "ACTIVE")
                    await self._runtime_outbox.publish_pending()
                    continue
                reservation = await self._node_execution_projection.try_operation(review_admission.reservation_id)
                receipt_ref = _text((reservation or {}).get("receipt_ref")) or None
                if gate.disposition == "REVIEW_DENIED":
                    await self._node_lifecycle.transition_node(
                        review_admission,
                        review_node,
                        "FAILED",
                        evidence_refs=post_review_refs,
                    )
                    if graph_status == "BLOCKED":
                        graph = await self._node_lifecycle.transition_graph(
                            review_admission,
                            graph,
                            "FAILED",
                        )
                        graph_status = _status(graph)
                    budget = await self._budgets.finalize(
                        budget, target_status=BudgetReservationStatus.RELEASED
                    )
                    reason_code = GOVERNED_REVIEW_DENIED
                    status = "REVIEW_DENIED"
                else:
                    reason_code = GOVERNED_REVIEW_REQUIRED
                    status = "WAITING_ACCEPTANCE_REVIEW"
                await self._runtime_outbox.publish_pending()
                decision = await self._node_execution_projection.matching_parallel_decision(
                    admitted,
                    graph=graph,
                )
                decision_ref: str | None = None
                if decision is not None:
                    selected = self._node_execution_projection.selected_parallel_decision_outcome(decision)
                    if _required_record_text(selected, "node_id") != review_admission.node_id:
                        raise GovernedRuntimeError(
                            GOVERNED_DURABLE_STATE_CONFLICT,
                            "parallel review replay selected a different TaskNode",
                        )
                    decision_ref = _required_record_text(decision, "decision_ref")
                projection: dict[str, Any] = {
                    "review": gate.to_dict(),
                    "budget": budget.execution_payload(),
                }
                if decision_ref is not None:
                    projection["parallel_settlement"] = {
                        "graph_status": graph_status,
                        "decision_ref": decision_ref,
                    }
                receipt = build_execution_receipt(
                    review_admission,
                    attempt_id=attempt_id,
                    receipt_ref=receipt_ref,
                    payload_digest=_digest_from_receipt_ref(receipt_ref),
                    status=status,
                    verification_status="PASS",
                    accepted=False,
                    reason_code=reason_code,
                    evidence_refs=tuple(
                        dict.fromkeys(
                            (
                                *post_review_refs,
                                *((receipt_ref,) if receipt_ref else ()),
                                *((decision_ref,) if decision_ref else ()),
                            )
                        )
                    ),
                    projection=projection,
                )
                yield terminal_event(receipt)
                return

            if budget.status == "EXCEEDED":
                if graph_status in {"CREATED", "ACTIVE"}:
                    graph = await self._node_lifecycle.transition_graph(final_admission, graph, "BLOCKED")
                    graph_status = _status(graph)
                budget = await self._budgets.finalize(
                    budget, target_status=BudgetReservationStatus.RELEASED
                )
            if budget.status == "RELEASED":
                if graph_status not in {"BLOCKED", "FAILED", "CANCELLED"}:
                    raise GovernedRuntimeError(
                        GOVERNED_BUDGET_STATE_CONFLICT,
                        "released budget belongs to a non-terminal TaskGraph",
                    )
                if graph_status == "BLOCKED":
                    decision = await self._node_execution_projection.matching_parallel_decision(
                        admitted,
                        graph=graph,
                    )
                    if decision is not None:
                        payload = _mapping(
                            decision.get("payload"),
                            field_name="parallel BLOCKED replay decision",
                        )
                        selected = self._node_execution_projection.selected_parallel_decision_outcome(decision)
                        selected_admission = admitted.for_node(
                            _required_record_text(selected, "node_id")
                        )
                        if _text(payload.get("decision_kind")) != "BUDGET":
                            async for event in self._node_execution.execute_stream(
                                admitted=selected_admission,
                                command=command,
                                work_order=work_order,
                                budget=budget,
                                node_grant_ref=grant_refs.get(
                                    selected_admission.node_id
                                ),
                            ):
                                yield event
                            return
                        if (
                            _status(selected) != "BUDGET_BLOCKED"
                            or bool(selected.get("accepted"))
                        ):
                            raise GovernedRuntimeError(
                                GOVERNED_DURABLE_STATE_CONFLICT,
                                "parallel Budget replay decision has an invalid selected outcome",
                            )
                        decision_ref = _required_record_text(
                            decision,
                            "decision_ref",
                        )
                        evidence_refs = tuple(
                            dict.fromkeys(
                                (
                                    *_record_evidence_refs(selected),
                                    decision_ref,
                                )
                            )
                        )
                        receipt = build_execution_receipt(
                            selected_admission,
                            attempt_id=_required_record_text(selected, "attempt_id"),
                            status="BUDGET_BLOCKED",
                            verification_status="NOT_RUN",
                            accepted=False,
                            replayed=True,
                            reason_code=GOVERNED_BUDGET_EXCEEDED,
                            evidence_refs=evidence_refs,
                            projection={
                                "budget": budget.execution_payload(),
                                "parallel_settlement": {
                                    "graph_status": graph_status,
                                    "decision_ref": decision_ref,
                                },
                            },
                        )
                        yield terminal_event(receipt)
                        return
                    receipt = build_execution_receipt(
                        final_admission,
                        attempt_id=_attempt_id(final_admission, attempt_no=1),
                        status="BUDGET_BLOCKED",
                        verification_status="NOT_RUN",
                        accepted=False,
                        reason_code=GOVERNED_BUDGET_EXCEEDED,
                        projection={"budget": budget.execution_payload()},
                    )
                    yield terminal_event(receipt)
                    return

            if statuses and all(status == "ACCEPTED" for status in statuses.values()):
                if parallel_success_enabled:
                    budget, graph_terminal = await self._parallel_success.complete(
                        admitted=admitted,
                        command=command,
                        work_order=work_order,
                        budget=budget,
                        node_grant_refs=grant_refs,
                    )
                    yield graph_terminal
                    return
                if budget.status == "ACTIVE":
                    budget = await self._budgets.finalize(
                        budget, target_status=BudgetReservationStatus.CONSUMED
                    )
                if budget.status != "CONSUMED":
                    raise GovernedRuntimeError(
                        GOVERNED_BUDGET_STATE_CONFLICT,
                        "accepted TaskGraph lacks a consumed budget",
                    )
                if graph_status == "ACTIVE":
                    graph = await self._node_lifecycle.transition_graph(final_admission, graph, "COMPLETED")
                    graph_status = _status(graph)
                if graph_status != "COMPLETED":
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "all TaskNodes are ACCEPTED but TaskGraph is not ACTIVE/COMPLETED",
                    )
                async for event in self._node_execution.execute_stream(
                    admitted=final_admission,
                    command=command,
                    work_order=work_order,
                    budget=budget,
                    node_grant_ref=grant_refs.get(final_admission.node_id),
                ):
                    yield event
                return

            blocked_node_id = next(
                (
                    binding.node_id
                    for binding in admitted.nodes
                    if statuses[binding.node_id] == "WAITING_ACCEPTANCE_REVIEW"
                ),
                None,
            )
            failed_node_id = next(
                (
                    binding.node_id
                    for binding in admitted.nodes
                    if statuses[binding.node_id]
                    in {"FAILED", "VERIFICATION_FAILED", "CANCELLED"}
                ),
                None,
            )
            if blocked_node_id is not None or failed_node_id is not None:
                terminal_node_id = failed_node_id or blocked_node_id
                decision = await self._node_execution_projection.matching_parallel_decision(
                    admitted,
                    graph=graph,
                )
                if decision is not None:
                    selected = self._node_execution_projection.selected_parallel_decision_outcome(decision)
                    terminal_node_id = _required_record_text(selected, "node_id")
                terminal_admission = admitted.for_node(terminal_node_id or admitted.node_id)
                if graph_status == "ACTIVE":
                    graph = await self._node_lifecycle.transition_graph(
                        terminal_admission,
                        graph,
                        "FAILED" if failed_node_id is not None else "BLOCKED",
                    )
                if failed_node_id is not None and budget.status == "ACTIVE":
                    budget = await self._budgets.finalize(
                        budget, target_status=BudgetReservationStatus.RELEASED
                    )
                async for event in self._node_execution.execute_stream(
                    admitted=terminal_admission,
                    command=command,
                    work_order=work_order,
                    budget=budget,
                    node_grant_ref=grant_refs.get(terminal_admission.node_id),
                ):
                    yield event
                return

            if graph_status in {"FAILED", "CANCELLED", "COMPLETED"}:
                if graph_status in {"FAILED", "CANCELLED"} and budget.status == "ACTIVE":
                    budget = await self._budgets.finalize(
                        budget, target_status=BudgetReservationStatus.RELEASED
                    )
                decision = await self._node_execution_projection.matching_parallel_decision(
                    admitted,
                    graph=graph,
                )
                if decision is not None:
                    selected = self._node_execution_projection.selected_parallel_decision_outcome(decision)
                    terminal_admission = admitted.for_node(
                        _required_record_text(selected, "node_id")
                    )
                else:
                    terminal_admission = next(
                        (
                            admitted.for_node(binding.node_id)
                            for binding in reversed(admitted.nodes)
                            if statuses[binding.node_id] != "READY"
                        ),
                        final_admission,
                    )
                async for event in self._node_execution.execute_stream(
                    admitted=terminal_admission,
                    command=command,
                    work_order=work_order,
                    budget=budget,
                    node_grant_ref=grant_refs.get(terminal_admission.node_id),
                ):
                    yield event
                return
            if graph_status == "BLOCKED":
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "TaskGraph is BLOCKED without a durable acceptance-review node",
                )
            if graph_status not in {"CREATED", "ACTIVE"}:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"TaskGraph is not schedulable: {graph_status}",
                )

            scheduler_snapshot = await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            )
            if not isinstance(scheduler_snapshot, Mapping):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "durable scheduler snapshot is invalid",
                )
            observed_ts_ms = _record_non_negative_int(
                scheduler_snapshot,
                "observed_ts_ms",
            )
            raw_active_leases = scheduler_snapshot.get("active_leases")
            if not isinstance(raw_active_leases, list):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "durable scheduler snapshot does not expose active leases",
                )
            occupied_attempts = {
                _required_record_text(lease, "attempt_id")
                for lease in raw_active_leases
                if isinstance(lease, Mapping)
                and _record_non_negative_int(lease, "expires_ts_ms") > observed_ts_ms
            }
            try:
                decision = compute_dag_schedule(
                    tuple(
                        DagNodeScheduleCandidate(
                            node_id=binding.node_id,
                            dependencies=binding.dependencies,
                            status=TaskNodeStatus(statuses[binding.node_id]),
                            retry_not_before_ms=_optional_record_non_negative_int(
                                durable_nodes[binding.node_id],
                                "retry_not_before_ms",
                            ),
                        )
                        for binding in admitted.nodes
                    ),
                    now_ms=observed_ts_ms,
                    concurrency_limit=budget.policy.limits.concurrency,
                    occupied_slots=len(occupied_attempts),
                )
            except (DagScheduleInputError, ValueError) as exc:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"durable DAG scheduling snapshot is invalid: {exc}",
                ) from exc

            if decision.dependency_ready_node_ids or decision.retry_due_node_ids:
                for node_id in decision.dependency_ready_node_ids:
                    await self._node_lifecycle.transition_node(
                        admitted.for_node(node_id),
                        durable_nodes[node_id],
                        "READY",
                    )
                for node_id in decision.retry_due_node_ids:
                    node_admission = admitted.for_node(node_id)
                    node = durable_nodes[node_id]
                    await _maybe_await(
                        self._store.promote_retry_due(
                            admitted.graph_id,
                            admitted.graph_revision,
                            node_id,
                            expected_version=_version(node),
                            outbox=self._runtime_outbox.build(
                                command_id=node_admission.command_id,
                                aggregate_type="TASK_NODE",
                                aggregate_id=(
                                    f"{admitted.graph_id}@{admitted.graph_revision}/{node_id}"
                                ),
                                aggregate_version=_version(node) + 1,
                                event_type="TASK_NODE_READY",
                            ),
                        )
                    )
                await self._runtime_outbox.publish_pending()
                continue

            selected_node_ids = set(decision.selected_node_ids)
            claimable = [
                binding for binding in admitted.nodes if binding.node_id in selected_node_ids
            ]
            if not claimable:
                active_statuses = {
                    "LEASED",
                    "RUNNING",
                    "EXECUTED",
                    "VERIFYING",
                    "VERIFIED",
                    "RETRY_PENDING",
                    "CANCEL_REQUESTED",
                }
                if any(status in active_statuses for status in statuses.values()):
                    raise GovernedRuntimeError(
                        GOVERNED_EXECUTION_IN_PROGRESS,
                        "TaskGraph has an in-progress node and cannot be claimed again",
                    )
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "no READY TaskNode has all dependencies ACCEPTED",
                )

            review_required = TaskNodeReviewRuntime.pre_execution_required(work_order)
            review_binding = None
            if review_required:
                if self._reviews is None:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "WorkOrder requires durable review but no Review runtime is wired",
                    )
                for candidate in claimable:
                    candidate_node = durable_nodes[candidate.node_id]
                    prior_gate = None
                    if _version(candidate_node) > 0:
                        prior_gate = self._reviews.lookup_pre_execution_gate(
                            context=build_review_context(
                                admitted=admitted.for_node(candidate.node_id),
                                command=command,
                                work_order=work_order,
                                node=candidate_node,
                                attempt_id=None,
                                reason="TaskNode requires durable pre-execution approval",
                                node_status_version=_version(candidate_node) - 1,
                            )
                        )
                    if prior_gate is None:
                        review_binding = candidate
                        break
                    if not prior_gate.allowed:
                        raise GovernedRuntimeError(
                            GOVERNED_DURABLE_STATE_CONFLICT,
                            "READY TaskNode is bound to an unconsumed prior review",
                        )
            if review_binding is not None:
                binding = review_binding
                review_admission = admitted.for_node(binding.node_id)
                review_node = await self._node_lifecycle.transition_node(
                    review_admission,
                    durable_nodes[binding.node_id],
                    "WAITING_APPROVAL",
                )
                graph = await self._node_lifecycle.transition_graph(review_admission, graph, "BLOCKED")
                gate = self._reviews.pre_execution_gate(
                    work_order=work_order,
                    context=build_review_context(
                        admitted=review_admission,
                        command=command,
                        work_order=work_order,
                        node=review_node,
                        attempt_id=None,
                        reason="TaskNode requires durable pre-execution approval",
                    ),
                )
                review_refs = _review_evidence_refs(gate)
                await self._runtime_outbox.publish_pending()
                receipt = build_execution_receipt(
                    review_admission,
                    attempt_id=_attempt_id(review_admission, attempt_no=1),
                    status="WAITING_APPROVAL",
                    verification_status="NOT_RUN",
                    accepted=False,
                    reason_code=GOVERNED_REVIEW_REQUIRED,
                    evidence_refs=review_refs,
                    projection={
                        "review": gate.to_dict(),
                        "budget": budget.execution_payload(),
                    },
                )
                yield terminal_event(receipt)
                return

            # Atomic-success Graphs freeze their outer terminal authority in
            # durable graph_json.  Activate the Graph before either a fan-out
            # or a sequential deferred child starts; no child is allowed to
            # own the shared Graph/Budget terminal transition.
            if parallel_success_enabled and graph_status == "CREATED":
                graph = await self._node_lifecycle.transition_graph(
                    admitted.for_node(claimable[0].node_id),
                    graph,
                    "ACTIVE",
                )
                graph_status = _status(graph)

            has_unaccepted_successor = any(
                binding.node_id not in selected_node_ids
                and statuses[binding.node_id] != "ACCEPTED"
                for binding in admitted.nodes
            )
            # B4 may fan out only while a later node remains.  B5's atomic
            # success authority removes that terminal-leaf restriction when
            # its explicit result/projection/effect prerequisites are present.
            if (
                self._bounded_fanout_enabled
                and len(claimable) > 1
                and (has_unaccepted_successor or parallel_success_enabled)
            ):
                if graph_status == "CREATED":
                    graph = await self._node_lifecycle.transition_graph(
                        admitted.for_node(claimable[0].node_id),
                        graph,
                        "ACTIVE",
                    )
                    graph_status = _status(graph)
                # Progress is bounded independently from terminal/error
                # control messages.  Every child owns one reserved control
                # slot, so self-cancellation cannot disappear while the
                # progress buffer is full and leave the supervisor hanging.
                progress_slots = asyncio.BoundedSemaphore(
                    self._fanout_event_buffer_size
                )
                queue: asyncio.Queue[tuple[str, str, Any]] = asyncio.Queue(
                    maxsize=self._fanout_event_buffer_size + len(claimable)
                )

                async def run_claimed_node(binding: AdmittedTaskNode) -> None:
                    node_admission = admitted.for_node(binding.node_id)
                    terminal_event: dict[str, Any] | None = None
                    try:
                        async for event in self._node_execution.execute_stream(
                            admitted=node_admission,
                            command=command,
                            work_order=work_order,
                            budget=budget,
                            node_grant_ref=grant_refs.get(node_admission.node_id),
                            defer_shared_terminal=True,
                        ):
                            if bool(event.get("done")):
                                terminal_event = dict(event)
                            else:
                                await progress_slots.acquire()
                                queue.put_nowait(
                                    ("event", binding.node_id, dict(event))
                                )
                        if terminal_event is None:
                            raise GovernedRuntimeError(
                                GOVERNED_DURABLE_STATE_CONFLICT,
                                f"TaskNode {binding.node_id} produced no durable terminal projection",
                            )
                    except asyncio.CancelledError as exc:
                        queue.put_nowait(("error", binding.node_id, exc))
                        return
                    except BaseException as exc:
                        queue.put_nowait(("error", binding.node_id, exc))
                        return
                    queue.put_nowait(("terminal", binding.node_id, terminal_event))

                tasks = {
                    binding.node_id: asyncio.create_task(
                        run_claimed_node(binding),
                        name=(
                            f"governed-node:{admitted.graph_id}@"
                            f"{admitted.graph_revision}/{binding.node_id}"
                        ),
                    )
                    for binding in claimable
                }
                terminals: dict[str, dict[str, Any]] = {}
                errors: dict[str, BaseException] = {}
                remaining = len(tasks)
                fatal_error: BaseException | None = None
                stop_batch = False
                try:
                    while remaining:
                        kind, node_id, payload = await queue.get()
                        if kind == "event":
                            progress_slots.release()
                            yield dict(payload)
                            continue
                        remaining -= 1
                        if kind == "terminal":
                            terminals[node_id] = dict(payload)
                            receipt = payload.get("execution_receipt")
                            if (
                                isinstance(receipt, Mapping)
                                and _text(receipt.get("status")).upper()
                                == "RECOVERY_REQUIRED"
                            ):
                                # A durable recovery outcome cannot safely wait
                                # for a blocked sibling. Stop this process-local
                                # batch, join siblings, and let durable state
                                # drive the next takeover or re-entry.
                                stop_batch = True
                                break
                            continue
                        error = payload
                        if not isinstance(error, BaseException):
                            error = GovernedRuntimeError(
                                GOVERNED_DURABLE_STATE_CONFLICT,
                                f"TaskNode {node_id} returned an invalid parallel error",
                            )
                        errors[node_id] = error
                        stop_batch = True
                        if not isinstance(error, Exception):
                            fatal_error = error
                        break
                finally:
                    if remaining or stop_batch:
                        for task in tasks.values():
                            if not task.done():
                                task.cancel()
                    await asyncio.gather(*tasks.values(), return_exceptions=True)
                if fatal_error is not None:
                    raise fatal_error
                if errors:
                    first_error_node = next(
                        binding.node_id
                        for binding in claimable
                        if binding.node_id in errors
                    )
                    raise errors[first_error_node]
                outcomes: list[_ParallelNodeOutcome] = []
                for binding in claimable:
                    node_terminal = terminals.get(binding.node_id)
                    if node_terminal is None:
                        if stop_batch:
                            continue
                        raise GovernedRuntimeError(
                            GOVERNED_DURABLE_STATE_CONFLICT,
                            f"TaskNode {binding.node_id} lost its parallel terminal projection",
                        )
                    outcomes.append(
                        _ParallelNodeOutcome.from_terminal_event(
                            node_id=binding.node_id,
                            event=node_terminal,
                        )
                    )
                budget, parallel_terminal = (
                    await self._parallel_settlement.resolve_parallel_outcomes(
                        admitted=admitted,
                        outcomes=tuple(outcomes),
                        budget=budget,
                    )
                )
                if parallel_terminal is not None:
                    yield parallel_terminal
                    return
                budget = await self._budgets.refresh(budget)
                continue

            for binding in claimable:
                node_admission = admitted.for_node(binding.node_id)
                node_terminal: dict[str, Any] | None = None
                async for event in self._node_execution.execute_stream(
                    admitted=node_admission,
                    command=command,
                    work_order=work_order,
                    budget=budget,
                    node_grant_ref=grant_refs.get(node_admission.node_id),
                    defer_shared_terminal=parallel_success_enabled,
                ):
                    if bool(event.get("done")):
                        node_terminal = dict(event)
                    else:
                        yield event
                if node_terminal is None:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        f"TaskNode {binding.node_id} produced no durable terminal projection",
                    )
                receipt = node_terminal.get("execution_receipt")
                accepted = isinstance(receipt, Mapping) and bool(receipt.get("accepted"))
                if parallel_success_enabled:
                    outcome = _ParallelNodeOutcome.from_terminal_event(
                        node_id=binding.node_id,
                        event=node_terminal,
                    )
                    budget, parallel_terminal = (
                        await self._parallel_settlement.resolve_parallel_outcomes(
                            admitted=admitted,
                            outcomes=(outcome,),
                            budget=budget,
                        )
                    )
                    if parallel_terminal is not None:
                        yield parallel_terminal
                        return
                    continue
                if not accepted:
                    yield node_terminal
                    return
                latest_graph = await _maybe_await(
                    self._store.get_graph(admitted.graph_id, admitted.graph_revision)
                )
                if _status(latest_graph) == "COMPLETED":
                    yield node_terminal
                    return


    async def _reentered_parallel_outcomes(
        self,
        *,
        admitted: AdmittedExecution,
        graph: Mapping[str, Any],
        durable_nodes: Mapping[str, Mapping[str, Any]],
    ) -> tuple[_ParallelNodeOutcome, ...]:
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
            receipt = await self._node_execution_projection.terminal_replay(
                node_admission,
                graph=graph,
                node=node,
            )
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


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _validated_node_grant_refs(
    admitted: AdmittedExecution,
    value: Mapping[str, str] | None,
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise GovernedRuntimeError(
            GOVERNED_ASSIGNMENT_CONFLICT,
            "node_grant_refs must be an exact TaskNode mapping",
        )
    refs = {
        _text(node_id): _text(grant_ref)
        for node_id, grant_ref in value.items()
        if _text(node_id) and _text(grant_ref)
    }
    expected = {binding.node_id for binding in admitted.nodes}
    if set(refs) != expected or len(refs) != len(value):
        raise GovernedRuntimeError(
            GOVERNED_ASSIGNMENT_CONFLICT,
            "node_grant_refs must cover the admitted TaskGraph exactly",
        )
    return refs


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
