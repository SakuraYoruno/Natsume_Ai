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

import hashlib
import inspect
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from natsume_contracts.task.v1 import HostCommand
from host_brain.contracts.agent_runtime.budget_policy import BudgetReservationStatus
from host_brain.contracts.agent_runtime.task_graph import TaskNodeStatus

from .admission import AdmittedExecution, AdmittedTaskNode
from .budget_runtime import BudgetCharge, BudgetRuntimeContext, DurableBudgetRuntime
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
    GOVERNED_VERIFICATION_EVIDENCE_MISSING,
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
from .review_runtime import (
    TaskNodeReviewContext,
    TaskNodeReviewGate,
    TaskNodeReviewRuntime,
)
from .ports import (
    AcceptancePort,
    ExecutionProjectionReaderPort,
    GovernedWorkerResolverPort,
    RuntimeOutboxPublisherPort,
    TaskOutboxFactoryPort,
    TaskStorePort,
    VerificationPort,
)
from .worker_registry import GovernedWorkerRoute
from .acceptance_effects import compile_graph_acceptance_effects


_logger = logging.getLogger("Natsume.task_runtime.coordinator")


@dataclass(frozen=True, slots=True)
class GovernedExecutionReceipt:
    command_id: str
    work_order_id: str
    assignment_id: str
    executor: str
    worker_route: str
    graph_id: str
    graph_revision: int
    node_id: str
    attempt_id: str
    reservation_id: str
    receipt_ref: str | None
    payload_digest: str | None
    status: str
    verification_status: str
    accepted: bool
    replayed: bool
    reason_code: str | None = None
    evidence_refs: tuple[str, ...] = ()
    projection: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "work_order_id": self.work_order_id,
            "assignment_id": self.assignment_id,
            "executor": self.executor,
            "worker_route": self.worker_route,
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "reservation_id": self.reservation_id,
            "receipt_ref": self.receipt_ref,
            "payload_digest": self.payload_digest,
            "status": self.status,
            "verification_status": self.verification_status,
            "accepted": self.accepted,
            "replayed": self.replayed,
            "reason_code": self.reason_code,
            "evidence_refs": list(self.evidence_refs),
            "projection": dict(self.projection),
        }


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
        self._store = task_store
        self._publisher = outbox_publisher
        self._outbox_factory = outbox_factory
        self._workers = worker_resolver
        self._verifier = verifier
        self._acceptance = acceptance
        self._projection_reader = projection_reader
        self._reviews = review_runtime
        self._acceptance_effects_enabled = bool(acceptance_effects_enabled)
        self._owner_id = owner
        self._lease_ttl_ms = int(lease_ttl_ms)
        self._operation_ttl_ms = int(operation_ttl_ms)
        self._publish_limit = max(1, int(publish_limit))
        self._budgets = DurableBudgetRuntime(task_store=task_store, outbox_factory=outbox_factory)

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
        if not isinstance(command, HostCommand) or command.command_id != admitted.command_id:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "runtime command differs from admitted command",
        )
        grant_refs = _validated_node_grant_refs(admitted, node_grant_refs)
        await _maybe_await(self._store.assert_dag_scheduling_ready())
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
                self._assert_durable_identity(node_admission, graph=graph, node=node)
                durable_nodes[binding.node_id] = node

            graph_status = _status(graph)
            statuses = {
                node_id: _status(node)
                for node_id, node in durable_nodes.items()
            }
            final_admission = admitted.for_node(admitted.nodes[-1].node_id)

            pre_review_node_id = next(
                (
                    binding.node_id
                    for binding in admitted.nodes
                    if statuses[binding.node_id] == "WAITING_APPROVAL"
                ),
                None,
            )
            if pre_review_node_id is not None:
                if self._reviews is None:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "TaskNode is WAITING_APPROVAL but no durable Review runtime is wired",
                    )
                review_admission = admitted.for_node(pre_review_node_id)
                review_node = durable_nodes[pre_review_node_id]
                if graph_status in {"CREATED", "ACTIVE"}:
                    graph = await self._transition_graph(review_admission, graph, "BLOCKED")
                    graph_status = _status(graph)
                gate = self._reviews.pre_execution_gate(
                    work_order=work_order,
                    context=self._review_context(
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
                    await self._transition_node(
                        review_admission,
                        review_node,
                        "READY",
                        evidence_refs=pre_review_refs,
                    )
                    if graph_status == "BLOCKED":
                        await self._transition_graph(review_admission, graph, "ACTIVE")
                    await self._publish_outbox()
                    continue
                if gate.disposition == "REVIEW_DENIED":
                    await self._cancel_waiting_review_node(
                        review_admission,
                        review_node,
                        evidence_refs=pre_review_refs,
                    )
                    if graph_status == "BLOCKED":
                        await self._transition_graph(review_admission, graph, "CANCELLED")
                    budget = await self._budgets.finalize(
                        budget, target_status=BudgetReservationStatus.RELEASED
                    )
                    reason_code = GOVERNED_REVIEW_DENIED
                    status = "REVIEW_DENIED"
                else:
                    reason_code = GOVERNED_REVIEW_REQUIRED
                    status = "WAITING_APPROVAL"
                await self._publish_outbox()
                receipt = self._receipt(
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
                yield self._terminal_event(receipt)
                return

            post_review_node_id = next(
                (
                    binding.node_id
                    for binding in admitted.nodes
                    if statuses[binding.node_id] == "WAITING_ACCEPTANCE_REVIEW"
                ),
                None,
            )
            if post_review_node_id is not None and self._reviews is not None:
                review_admission = admitted.for_node(post_review_node_id)
                review_node = durable_nodes[post_review_node_id]
                if graph_status == "ACTIVE":
                    graph = await self._transition_graph(review_admission, graph, "BLOCKED")
                    graph_status = _status(graph)
                attempts = await self._attempts_for_node(review_admission)
                if not attempts:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "post-execution review has no durable TaskAttempt",
                    )
                attempt_id = _required_record_text(attempts[-1], "attempt_id")
                gate = self._reviews.post_execution_acceptance_gate(
                    context=self._review_context(
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
                    await self._transition_node(
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
                        await self._transition_graph(review_admission, graph, "ACTIVE")
                    await self._publish_outbox()
                    continue
                reservation = await self._try_operation(review_admission.reservation_id)
                receipt_ref = _text((reservation or {}).get("receipt_ref")) or None
                if gate.disposition == "REVIEW_DENIED":
                    await self._transition_node(
                        review_admission,
                        review_node,
                        "FAILED",
                        evidence_refs=post_review_refs,
                    )
                    if graph_status == "BLOCKED":
                        await self._transition_graph(review_admission, graph, "FAILED")
                    budget = await self._budgets.finalize(
                        budget, target_status=BudgetReservationStatus.RELEASED
                    )
                    reason_code = GOVERNED_REVIEW_DENIED
                    status = "REVIEW_DENIED"
                else:
                    reason_code = GOVERNED_REVIEW_REQUIRED
                    status = "WAITING_ACCEPTANCE_REVIEW"
                await self._publish_outbox()
                receipt = self._receipt(
                    review_admission,
                    attempt_id=attempt_id,
                    receipt_ref=receipt_ref,
                    payload_digest=_digest_from_receipt_ref(receipt_ref),
                    status=status,
                    verification_status="PASS",
                    accepted=False,
                    reason_code=reason_code,
                    evidence_refs=(*post_review_refs, *((receipt_ref,) if receipt_ref else ())),
                    projection={
                        "review": gate.to_dict(),
                        "budget": budget.execution_payload(),
                    },
                )
                yield self._terminal_event(receipt)
                return

            if budget.status == "EXCEEDED":
                if graph_status in {"CREATED", "ACTIVE"}:
                    graph = await self._transition_graph(final_admission, graph, "BLOCKED")
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
                    receipt = self._receipt(
                        final_admission,
                        attempt_id=_attempt_id(final_admission, attempt_no=1),
                        status="BUDGET_BLOCKED",
                        verification_status="NOT_RUN",
                        accepted=False,
                        reason_code=GOVERNED_BUDGET_EXCEEDED,
                        projection={"budget": budget.execution_payload()},
                    )
                    yield self._terminal_event(receipt)
                    return

            if statuses and all(status == "ACCEPTED" for status in statuses.values()):
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
                    graph = await self._transition_graph(final_admission, graph, "COMPLETED")
                    graph_status = _status(graph)
                if graph_status != "COMPLETED":
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "all TaskNodes are ACCEPTED but TaskGraph is not ACTIVE/COMPLETED",
                    )
                async for event in self._execute_node_stream(
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
                terminal_node_id = blocked_node_id or failed_node_id
                terminal_admission = admitted.for_node(terminal_node_id or admitted.node_id)
                if graph_status == "ACTIVE":
                    graph = await self._transition_graph(
                        terminal_admission,
                        graph,
                        "BLOCKED" if blocked_node_id is not None else "FAILED",
                    )
                if failed_node_id is not None and budget.status == "ACTIVE":
                    budget = await self._budgets.finalize(
                        budget, target_status=BudgetReservationStatus.RELEASED
                    )
                async for event in self._execute_node_stream(
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
                terminal_admission = next(
                    (
                        admitted.for_node(binding.node_id)
                        for binding in reversed(admitted.nodes)
                        if statuses[binding.node_id] != "READY"
                    ),
                    final_admission,
                )
                async for event in self._execute_node_stream(
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
                    await self._transition_node(
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
                            outbox=self._outbox(
                                node_admission,
                                aggregate_type="TASK_NODE",
                                aggregate_id=(
                                    f"{admitted.graph_id}@{admitted.graph_revision}/{node_id}"
                                ),
                                aggregate_version=_version(node) + 1,
                                event_type="TASK_NODE_READY",
                            ),
                        )
                    )
                await self._publish_outbox()
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
                            context=self._review_context(
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
                review_node = await self._transition_node(
                    review_admission,
                    durable_nodes[binding.node_id],
                    "WAITING_APPROVAL",
                )
                graph = await self._transition_graph(review_admission, graph, "BLOCKED")
                gate = self._reviews.pre_execution_gate(
                    work_order=work_order,
                    context=self._review_context(
                        admitted=review_admission,
                        command=command,
                        work_order=work_order,
                        node=review_node,
                        attempt_id=None,
                        reason="TaskNode requires durable pre-execution approval",
                    ),
                )
                review_refs = _review_evidence_refs(gate)
                await self._publish_outbox()
                receipt = self._receipt(
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
                yield self._terminal_event(receipt)
                return

            for binding in claimable:
                node_admission = admitted.for_node(binding.node_id)
                terminal_event: dict[str, Any] | None = None
                async for event in self._execute_node_stream(
                    admitted=node_admission,
                    command=command,
                    work_order=work_order,
                    budget=budget,
                    node_grant_ref=grant_refs.get(node_admission.node_id),
                ):
                    if bool(event.get("done")):
                        terminal_event = dict(event)
                    else:
                        yield event
                if terminal_event is None:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        f"TaskNode {binding.node_id} produced no durable terminal projection",
                    )
                receipt = terminal_event.get("execution_receipt")
                accepted = isinstance(receipt, Mapping) and bool(receipt.get("accepted"))
                if not accepted:
                    yield terminal_event
                    return
                latest_graph = await _maybe_await(
                    self._store.get_graph(admitted.graph_id, admitted.graph_revision)
                )
                if _status(latest_graph) == "COMPLETED":
                    yield terminal_event
                    return

    async def _execute_node_stream(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        budget: BudgetRuntimeContext | None = None,
        node_grant_ref: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        admitted.validate()
        if not isinstance(command, HostCommand) or command.command_id != admitted.command_id:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "runtime command differs from admitted command",
            )
        if budget is None:
            budget = await self._budgets.ensure_active(admitted=admitted, work_order=work_order)

        graph = await _maybe_await(self._store.get_graph(admitted.graph_id, admitted.graph_revision))
        node = await _maybe_await(
            self._store.get_node(admitted.graph_id, admitted.graph_revision, admitted.node_id)
        )
        self._assert_durable_identity(admitted, graph=graph, node=node)

        replay_receipt = await self._terminal_replay(admitted, graph=graph, node=node)
        if replay_receipt is not None:
            await self._publish_outbox()
            yield self._terminal_event(replay_receipt)
            return

        graph_status = _status(graph)
        node_status = _status(node)
        if graph_status not in {"CREATED", "ACTIVE"} or node_status != "READY":
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"admission must expose CREATED/ACTIVE graph and READY node, got {graph_status}/{node_status}",
            )

        attempts = await self._attempts_for_node(admitted)
        attempt_numbers = [int(item.get("attempt_no") or 0) for item in attempts]
        if attempt_numbers != list(range(1, len(attempt_numbers) + 1)):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable TaskAttempt journal is not contiguous",
            )
        existing_reservation = await self._try_operation(admitted.reservation_id)
        if existing_reservation is not None:
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                "an existing operation reservation has no complete durable terminal projection",
            )

        if graph_status == "CREATED":
            graph = await self._transition_graph(admitted, graph, "ACTIVE")

        attempt_no = len(attempt_numbers) + 1
        attempt_id = _attempt_id(admitted, attempt_no=attempt_no)
        claimed_raw = await _maybe_await(
            self._store.claim_ready_node(
                attempt_id=attempt_id,
                graph_id=admitted.graph_id,
                graph_revision=admitted.graph_revision,
                node_id=admitted.node_id,
                expected_node_version=_version(node),
                expected_attempt_count=len(attempt_numbers),
                owner_id=self._owner_id,
                lease_ttl_ms=self._lease_ttl_ms,
                budget_id=budget.budget_id,
                operation_scope=admitted.business_scope,
                operation_fingerprint=admitted.operation_fingerprint,
                attempt_created_outbox=self._outbox(
                    admitted,
                    aggregate_type="TASK_ATTEMPT",
                    aggregate_id=attempt_id,
                    aggregate_version=0,
                    event_type="TASK_ATTEMPT_CREATED",
                    attempt_id=attempt_id,
                ),
                attempt_leased_outbox=self._outbox(
                    admitted,
                    aggregate_type="TASK_ATTEMPT",
                    aggregate_id=attempt_id,
                    aggregate_version=1,
                    event_type="TASK_ATTEMPT_LEASED",
                    attempt_id=attempt_id,
                ),
            )
        )
        claimed = _mapping(claimed_raw, field_name="atomic READY claim")
        attempt = _mapping(
            claimed.get("attempt"),
            field_name="atomic READY claim attempt",
        )
        lease = _mapping(
            claimed.get("lease"),
            field_name="atomic READY claim lease",
        )
        node = _mapping(
            claimed.get("node"),
            field_name="atomic READY claim node",
        )
        lease_id = _required_record_text(lease, "lease_id")
        fencing_token = _record_positive_int(lease, "fencing_token")
        node = await self._transition_node(admitted, node, "RUNNING")
        attempt = await self._transition_attempt(
            admitted,
            attempt,
            lease_id=lease_id,
            fencing_token=fencing_token,
            target_status="RUNNING",
        )
        precharge = await self._budgets.precharge(
            budget,
            node_id=admitted.node_id,
            attempt_id=attempt_id,
            tool_call_count=_precharged_tool_call_count(admitted.worker_route),
        )
        budget = precharge.context
        if not precharge.accepted:
            receipt = await self._reject_budget_charge(
                admitted, precharge, attempt_id=attempt_id, fencing_token=fencing_token
            )
            yield self._terminal_event(receipt)
            return

        reserved_raw = await _maybe_await(
            self._store.reserve_operation(
                reservation_id=admitted.reservation_id,
                business_scope=admitted.business_scope,
                operation_fingerprint=admitted.operation_fingerprint,
                attempt_id=attempt_id,
                lease_id=lease_id,
                owner_id=self._owner_id,
                fencing_token=fencing_token,
                ttl_ms=self._operation_ttl_ms,
                outbox=self._outbox(
                    admitted,
                    aggregate_type="OPERATION_RESERVATION",
                    aggregate_id=admitted.reservation_id,
                    aggregate_version=0,
                    event_type="OPERATION_RESERVED",
                    attempt_id=attempt_id,
                ),
            )
        )
        reservation_acquired, reservation = _reservation_result(reserved_raw)
        if not reservation_acquired:
            await self._mark_worker_failure(
                admitted,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
                graph_target="BLOCKED",
            )
            budget = await self._budgets.finalize(
                budget, target_status=BudgetReservationStatus.RELEASED
            )
            receipt = self._receipt(
                admitted,
                attempt_id=attempt_id,
                status="RECOVERY_REQUIRED",
                verification_status="NOT_RUN",
                accepted=False,
                reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
                projection={
                    "reservation_status": _status(reservation) or "OWNED",
                    "budget": budget.execution_payload(),
                },
            )
            yield self._terminal_event(receipt)
            return
        if node_grant_ref is None:
            # Compatibility is limited to tests/legacy callers that have not
            # opted into node grants. Production GovernedCommandApplication
            # supplies an exact grant for every admitted node.
            node_target_id = command.target_id
        else:
            frozen_route = GovernedWorkerRoute.parse(admitted.worker_route)
            node_target_id = frozen_route.target_id or command.target_id
        execution_context = {
            "execution_mode": "governed_task_runtime",
            "command_id": admitted.command_id,
            "request_id": command.request_id,
            "principal_id": command.principal_id,
            "target_id": node_target_id,
            "command_target_id": command.target_id,
            "work_order_id": admitted.work_order_id,
            "assignment_id": admitted.assignment_id,
            "executor": admitted.executor,
            "worker_route": admitted.worker_route,
            "graph_id": admitted.graph_id,
            "graph_revision": admitted.graph_revision,
            "node_id": admitted.node_id,
            "attempt_id": attempt_id,
            "lease_id": lease_id,
            "lease_owner_id": self._owner_id,
            "fencing_token": fencing_token,
            "reservation_id": admitted.reservation_id,
            "business_scope": admitted.business_scope,
            "operation_fingerprint": admitted.operation_fingerprint,
            "parent_grant_ref": str(command.governor_grant_ref or "").strip(),
            "budget": budget.execution_payload(),
            "node_input_refs": list(_node_input_refs(node)),
            "node_acceptance_criteria": list(admitted.active_node.acceptance_criteria),
        }
        if node_grant_ref is not None:
            execution_context["node_grant_ref"] = node_grant_ref

        worker_commit: dict[str, Any] | None = None
        worker_started_ns = time.monotonic_ns()
        try:
            commit_count = 0
            worker = self._workers.resolve(
                executor=admitted.executor,
                worker_route=admitted.worker_route,
            )
            async for raw_event in _worker_event_stream(
                worker,
                command=command,
                work_order=work_order,
                execution_context=execution_context,
            ):
                event = _mapping(raw_event, field_name="worker event")
                if _is_terminal_commit(event):
                    commit_count += 1
                    worker_commit = event
                    continue
                yield event
            if commit_count != 1 or worker_commit is None:
                raise GovernedRuntimeError(
                    GOVERNED_WORKER_COMMIT_INVALID,
                    f"worker must produce exactly one final COMMIT, got {commit_count}",
                )
        except BaseException as exc:
            _logger.error(
                "Governed worker execution failed",
                extra={
                    "request_id": command.request_id,
                    "graph_id": admitted.graph_id,
                    "graph_revision": admitted.graph_revision,
                    "node_id": admitted.node_id,
                    "attempt_id": attempt_id,
                    "worker_route": admitted.worker_route,
                    "reason_code": _exception_reason(exc, GOVERNED_WORKER_FAILED),
                    "exception_type": type(exc).__name__,
                },
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            elapsed_ms = max(0, (time.monotonic_ns() - worker_started_ns + 999_999) // 1_000_000)
            charge = await self._budgets.settle_worker(
                budget,
                node_id=admitted.node_id,
                attempt_id=attempt_id,
                worker_commit=None,
                elapsed_ms=elapsed_ms,
            )
            budget = charge.context
            if not charge.accepted:
                receipt = await self._reject_budget_charge(
                    admitted, charge, attempt_id=attempt_id, fencing_token=fencing_token
                )
                yield self._terminal_event(receipt)
                return
            await self._mark_worker_failure(
                admitted,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                reason_code=_exception_reason(exc, GOVERNED_WORKER_FAILED),
            )
            budget = await self._budgets.finalize(
                budget, target_status=BudgetReservationStatus.RELEASED
            )
            receipt = self._receipt(
                admitted,
                attempt_id=attempt_id,
                status="FAILED",
                verification_status="NOT_RUN",
                accepted=False,
                reason_code=_exception_reason(exc, GOVERNED_WORKER_FAILED),
                evidence_refs=(charge.evidence_ref,),
                projection={"budget": budget.execution_payload()},
            )
            yield self._terminal_event(receipt)
            return

        payload_digest = _payload_digest(worker_commit)
        digest_ref = f"sha256://execution/{payload_digest.split(':', 1)[1]}"
        receipt_ref = _worker_receipt_ref(worker_commit) or (
            "taskreceipt://"
            f"{_ref_token(admitted.graph_id)}/{admitted.graph_revision}/"
            f"{_ref_token(admitted.node_id)}/{_ref_token(attempt_id)}/"
            f"{payload_digest.split(':', 1)[1]}"
        )
        evidence_refs = (receipt_ref, digest_ref)
        try:
            reservation = await _maybe_await(
                self._store.commit_operation(
                    admitted.reservation_id,
                    attempt_id=attempt_id,
                    lease_id=lease_id,
                    owner_id=self._owner_id,
                    fencing_token=fencing_token,
                    expected_version=_version(reservation),
                    receipt_ref=receipt_ref,
                    outbox=self._outbox(
                        admitted,
                        aggregate_type="OPERATION_RESERVATION",
                        aggregate_id=admitted.reservation_id,
                        aggregate_version=_version(reservation) + 1,
                        event_type="OPERATION_COMMITTED",
                        attempt_id=attempt_id,
                        evidence_refs=evidence_refs,
                    ),
                )
            )
        except BaseException as exc:
            await self._publish_outbox(best_effort=True)
            raise GovernedRuntimeError(
                GOVERNED_RECEIPT_COMMIT_FAILED,
                f"worker receipt could not be committed before terminal state: {_exception_reason(exc, 'UNKNOWN')}",
            ) from exc

        try:
            charge = await self._budgets.settle_worker(
                budget,
                node_id=admitted.node_id,
                attempt_id=attempt_id,
                worker_commit=worker_commit,
                elapsed_ms=max(
                    0, (time.monotonic_ns() - worker_started_ns + 999_999) // 1_000_000
                ),
            )
        except BaseException as exc:
            await self._mark_worker_failure(
                admitted,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                reason_code=_exception_reason(exc, GOVERNED_BUDGET_STATE_CONFLICT),
            )
            budget = await self._budgets.finalize(
                budget, target_status=BudgetReservationStatus.RELEASED
            )
            receipt = self._receipt(
                admitted,
                attempt_id=attempt_id,
                status="FAILED",
                verification_status="NOT_RUN",
                accepted=False,
                reason_code=_exception_reason(exc, GOVERNED_BUDGET_STATE_CONFLICT),
                projection={"budget": budget.execution_payload()},
            )
            yield self._terminal_event(receipt)
            return
        budget = charge.context
        if not charge.accepted:
            receipt = await self._reject_budget_charge(
                admitted, charge, attempt_id=attempt_id, fencing_token=fencing_token
            )
            yield self._terminal_event(receipt)
            return
        execution_context["budget"] = budget.execution_payload()

        attempt = await self._transition_attempt(
            admitted,
            attempt,
            lease_id=lease_id,
            fencing_token=fencing_token,
            target_status="SUCCEEDED",
            evidence_refs=evidence_refs,
        )
        node = await self._transition_node(
            admitted,
            node,
            "EXECUTED",
            evidence_refs=evidence_refs,
        )
        node = await self._transition_node(
            admitted,
            node,
            "VERIFYING",
            evidence_refs=evidence_refs,
        )

        verification_raw = await _maybe_await(
            self._verifier.verify(
                command=command,
                work_order=work_order,
                execution_context=execution_context,
                worker_commit=worker_commit,
                evidence_refs=evidence_refs,
            )
        )
        verification = _mapping(verification_raw, field_name="verification result")
        verification_refs = _evidence_refs(verification)
        verification_status = _text(verification.get("status")).upper()
        missing_verification_refs = tuple(ref for ref in evidence_refs if ref not in verification_refs)
        if verification_status == "PASS" and missing_verification_refs:
            verification = {
                **verification,
                "status": "FAIL",
                "reason_code": GOVERNED_VERIFICATION_EVIDENCE_MISSING,
                "evidence_refs": list(dict.fromkeys((*verification_refs, *evidence_refs))),
            }
            verification_refs = _evidence_refs(verification)
            verification_status = "FAIL"

        if verification_status != "PASS":
            node = await self._transition_node(
                admitted,
                node,
                "VERIFICATION_FAILED",
                evidence_refs=verification_refs or evidence_refs,
                verification_result=verification,
            )
            budget = await self._budgets.finalize(
                budget, target_status=BudgetReservationStatus.RELEASED
            )
            graph = await self._transition_graph(admitted, graph, "FAILED")
            await self._publish_outbox()
            receipt = self._receipt(
                admitted,
                attempt_id=attempt_id,
                receipt_ref=receipt_ref,
                payload_digest=payload_digest,
                status="VERIFICATION_FAILED",
                verification_status=verification_status or "ERROR",
                accepted=False,
                reason_code=_text(verification.get("reason_code")) or GOVERNED_VERIFICATION_FAILED,
                evidence_refs=verification_refs or evidence_refs,
                projection=worker_commit,
            )
            yield self._terminal_event(receipt)
            return

        node = await self._transition_node(
            admitted,
            node,
            "VERIFIED",
            evidence_refs=verification_refs,
            verification_result=verification,
        )
        acceptance_raw = await _maybe_await(
            self._acceptance.judge(
                command=command,
                work_order=work_order,
                execution_context=execution_context,
                worker_commit=worker_commit,
                verification_result=verification,
                evidence_refs=verification_refs,
            )
        )
        acceptance = _mapping(acceptance_raw, field_name="acceptance judgement")
        acceptance_refs = _evidence_refs(acceptance)
        accepted = _text(acceptance.get("decision")).upper() == "ACCEPT"
        missing_acceptance_refs = tuple(ref for ref in evidence_refs if ref not in acceptance_refs)
        if accepted and missing_acceptance_refs:
            accepted = False
            acceptance = {
                **acceptance,
                "decision": "REVIEW",
                "reason_code": GOVERNED_ACCEPTANCE_FAILED,
                "evidence_refs": list(dict.fromkeys((*acceptance_refs, *evidence_refs))),
            }
            acceptance_refs = _evidence_refs(acceptance)

        if not accepted:
            node = await self._transition_node(
                admitted,
                node,
                "WAITING_ACCEPTANCE_REVIEW",
                evidence_refs=acceptance_refs or verification_refs,
            )
            graph = await self._transition_graph(admitted, graph, "BLOCKED")
            review_gate: TaskNodeReviewGate | None = None
            review_refs: tuple[str, ...] = ()
            if self._reviews is not None:
                review_gate = self._reviews.post_execution_acceptance_gate(
                    context=self._review_context(
                        admitted=admitted,
                        command=command,
                        work_order=work_order,
                        node=node,
                        attempt_id=attempt_id,
                        reason="Acceptance policy requires durable post-execution review",
                    )
                )
                review_refs = _review_evidence_refs(review_gate)
            await self._publish_outbox()
            review_projection = dict(worker_commit)
            if review_gate is not None:
                review_projection["review"] = review_gate.to_dict()
            receipt = self._receipt(
                admitted,
                attempt_id=attempt_id,
                receipt_ref=receipt_ref,
                payload_digest=payload_digest,
                status="WAITING_ACCEPTANCE_REVIEW",
                verification_status="PASS",
                accepted=False,
                reason_code=(
                    GOVERNED_REVIEW_REQUIRED
                    if review_gate is not None
                    else _text(acceptance.get("reason_code")) or GOVERNED_ACCEPTANCE_FAILED
                ),
                evidence_refs=tuple(
                    dict.fromkeys((*(acceptance_refs or verification_refs), *review_refs))
                ),
                projection=review_projection,
            )
            yield self._terminal_event(receipt)
            return

        node = await self._transition_node(
            admitted,
            node,
            "ACCEPTED",
            evidence_refs=acceptance_refs,
            acceptance_judgement=acceptance,
        )
        all_nodes_accepted = True
        for binding in admitted.nodes:
            durable_node = await _maybe_await(
                self._store.get_node(
                    admitted.graph_id,
                    admitted.graph_revision,
                    binding.node_id,
                )
            )
            if _status(durable_node) != "ACCEPTED":
                all_nodes_accepted = False
                break
        if all_nodes_accepted:
            budget = await self._budgets.finalize(
                budget, target_status=BudgetReservationStatus.CONSUMED
            )
            graph = await _maybe_await(
                self._store.get_graph(admitted.graph_id, admitted.graph_revision)
            )
            if _status(graph) == "ACTIVE":
                if self._acceptance_effects_enabled:
                    try:
                        effect_intents = compile_graph_acceptance_effects(
                            command=command,
                            work_order=work_order,
                            node_id=admitted.node_id,
                            attempt_id=attempt_id,
                            worker_commit=worker_commit,
                        )
                        graph = await self._transition_graph_with_acceptance_effects(
                            admitted,
                            graph,
                            effect_intents=effect_intents,
                        )
                    except BaseException as exc:
                        graph = await self._transition_graph(admitted, graph, "FAILED")
                        await self._publish_outbox()
                        receipt = self._receipt(
                            admitted,
                            attempt_id=attempt_id,
                            receipt_ref=receipt_ref,
                            payload_digest=payload_digest,
                            status="ACCEPTANCE_EFFECT_FAILED",
                            verification_status="PASS",
                            accepted=False,
                            reason_code=_exception_reason(
                                exc,
                                "ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID",
                            ),
                            evidence_refs=acceptance_refs,
                            projection=worker_commit,
                        )
                        yield self._terminal_event(receipt)
                        return
                else:
                    graph = await self._transition_graph(admitted, graph, "COMPLETED")
        await self._publish_outbox()
        receipt = self._receipt(
            admitted,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
            payload_digest=payload_digest,
            status="ACCEPTED",
            verification_status="PASS",
            accepted=True,
            evidence_refs=acceptance_refs,
            projection=worker_commit,
        )
        yield self._approved_commit_event(worker_commit, receipt)

    @staticmethod
    def _review_context(
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        node: Mapping[str, Any],
        attempt_id: str | None,
        reason: str,
        node_status_version: int | None = None,
    ) -> TaskNodeReviewContext:
        node_payload = _nested_mapping(node, "node")
        side_effects = tuple(
            _text(item)
            for item in tuple(getattr(work_order, "allowed_actions", ()) or ())
            if _text(item)
        )
        input_refs = tuple(
            _text(item)
            for item in tuple(getattr(work_order, "input_refs", ()) or ())
            if _text(item)
        )
        evidence_refs = tuple(
            dict.fromkeys((*input_refs, *_record_evidence_refs(node)))
        )
        review_policy = getattr(work_order, "review_policy", {})
        review_policy = dict(review_policy) if isinstance(review_policy, Mapping) else {}
        root_work_order_id = _text(
            getattr(work_order, "root_work_order_id", None)
            or getattr(work_order, "work_order_id", None)
            or admitted.work_order_id
        )
        return TaskNodeReviewContext(
            work_order_id=admitted.work_order_id,
            root_work_order_id=root_work_order_id,
            graph_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
            node_id=admitted.node_id,
            node_status_version=(
                _version(node)
                if node_status_version is None
                else int(node_status_version)
            ),
            attempt_id=attempt_id,
            requester=command.principal_id,
            reason=reason,
            risk_level=max(
                1,
                min(
                    5,
                    int(
                        node_payload.get("risk_level")
                        or getattr(work_order, "risk_level", 1)
                        or 1
                    ),
                ),
            ),
            side_effects=side_effects,
            evidence_refs=evidence_refs,
            diff_ref=_text(review_policy.get("diff_ref")) or None,
        )

    def _assert_durable_identity(
        self,
        admitted: AdmittedExecution,
        *,
        graph: Mapping[str, Any],
        node: Mapping[str, Any],
    ) -> None:
        graph_payload = _nested_mapping(graph, "graph")
        durable_graph_id = _text(graph.get("graph_id") or graph_payload.get("graph_id"))
        durable_revision = int(graph.get("revision") or graph_payload.get("revision") or 0)
        durable_root = _text(graph.get("root_work_order_id") or graph_payload.get("root_work_order_id"))
        node_payload = _nested_mapping(node, "node")
        durable_node_id = _text(node.get("node_id") or node_payload.get("node_id"))
        durable_route = _text(node.get("route") or node_payload.get("route"))
        raw_dependencies = node_payload.get("dependencies")
        durable_dependencies = tuple(
            _text(item)
            for item in (raw_dependencies if isinstance(raw_dependencies, list) else ())
            if _text(item)
        )
        if (
            durable_graph_id != admitted.graph_id
            or durable_revision != admitted.graph_revision
            or durable_root != admitted.work_order_id
            or durable_node_id != admitted.node_id
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable graph/node identity differs from admitted command",
            )
        if durable_route != admitted.worker_route:
            raise GovernedRuntimeError(
                GOVERNED_ASSIGNMENT_CONFLICT,
                "durable TaskNode.route differs from the frozen worker route",
            )
        if durable_dependencies != admitted.dependencies:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable TaskNode.dependencies differ from the frozen DAG",
            )

    async def _terminal_replay(
        self,
        admitted: AdmittedExecution,
        *,
        graph: Mapping[str, Any],
        node: Mapping[str, Any],
    ) -> GovernedExecutionReceipt | None:
        graph_status = _status(graph)
        node_status = _status(node)
        waiting_acceptance_review = (
            graph_status == "BLOCKED"
            and node_status == "WAITING_ACCEPTANCE_REVIEW"
        )
        terminal = (
            graph_status in {"COMPLETED", "FAILED", "CANCELLED"}
            or node_status in {
                "ACCEPTED",
                "FAILED",
                "VERIFICATION_FAILED",
                "CANCELLED",
            }
            or waiting_acceptance_review
        )
        if not terminal:
            return None
        reservation = await self._try_operation(admitted.reservation_id)
        attempts = await self._attempts_for_node(admitted)
        latest = attempts[-1] if attempts else {}
        attempt_id = _text(latest.get("attempt_id")) or _attempt_id(admitted, attempt_no=1)
        reservation_status = _status(reservation or {})
        receipt_ref = _text((reservation or {}).get("receipt_ref")) or None
        accepted = node_status == "ACCEPTED" and graph_status in {"ACTIVE", "COMPLETED"}
        if accepted and (reservation_status != "COMMITTED" or not receipt_ref):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted graph lacks its committed operation receipt",
            )
        if waiting_acceptance_review and (
            reservation_status != "COMMITTED" or not receipt_ref
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "acceptance-review graph lacks its committed operation receipt",
            )
        projection: Mapping[str, Any] | None = None
        if accepted and receipt_ref and self._projection_reader is not None:
            loaded = await _maybe_await(self._projection_reader.load(receipt_ref))
            if not isinstance(loaded, Mapping):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "accepted receipt has no durable public projection",
                )
            projection = dict(loaded)
        return self._receipt(
            admitted,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
            payload_digest=_digest_from_receipt_ref(receipt_ref),
            status=node_status or graph_status,
            verification_status=(
                "PASS"
                if accepted or waiting_acceptance_review
                else "FAIL"
                if node_status == "VERIFICATION_FAILED"
                else "UNKNOWN"
            ),
            accepted=accepted,
            replayed=True,
            reason_code=None if accepted else f"GOVERNED_REPLAY_{node_status or graph_status}",
            evidence_refs=(receipt_ref,) if receipt_ref else (),
            projection=projection,
        )

    async def _attempts_for_node(self, admitted: AdmittedExecution) -> list[dict[str, Any]]:
        snapshot = await _maybe_await(
            self._store.read_scheduler_snapshot(admitted.graph_id, admitted.graph_revision)
        )
        attempts = snapshot.get("attempts") if isinstance(snapshot, Mapping) else None
        if not isinstance(attempts, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable scheduler snapshot does not expose attempts",
            )
        rows = [
            dict(item)
            for item in attempts
            if isinstance(item, Mapping) and _text(item.get("node_id")) == admitted.node_id
        ]
        rows.sort(key=lambda item: (int(item.get("attempt_no") or 0), _text(item.get("attempt_id"))))
        return rows

    async def _try_operation(self, reservation_id: str) -> dict[str, Any] | None:
        try:
            value = await _maybe_await(self._store.get_operation_reservation(reservation_id))
        except Exception as exc:
            reason = _exception_reason(exc, "")
            if reason in {"TASK_STORE_NOT_FOUND", "NOT_FOUND", "OPERATION_NOT_FOUND"}:
                return None
            raise
        return dict(value) if isinstance(value, Mapping) else None

    async def _transition_graph(
        self,
        admitted: AdmittedExecution,
        graph: Mapping[str, Any],
        target_status: str,
    ) -> dict[str, Any]:
        version = _version(graph)
        return dict(
            await _maybe_await(
                self._store.transition_graph(
                    admitted.graph_id,
                    admitted.graph_revision,
                    expected_version=version,
                    target_status=target_status,
                    outbox=self._outbox(
                        admitted,
                        aggregate_type="TASK_GRAPH",
                        aggregate_id=admitted.graph_id,
                        aggregate_version=version + 1,
                        event_type=f"TASK_GRAPH_{target_status}",
                    ),
                )
            )
        )

    async def _transition_node(
        self,
        admitted: AdmittedExecution,
        node: Mapping[str, Any],
        target_status: str,
        *,
        evidence_refs: Sequence[str] = (),
        verification_result: Mapping[str, Any] | None = None,
        acceptance_judgement: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        version = _version(node)
        return dict(
            await _maybe_await(
                self._store.transition_node(
                    admitted.graph_id,
                    admitted.graph_revision,
                    admitted.node_id,
                    expected_version=version,
                    target_status=target_status,
                    outbox=self._outbox(
                        admitted,
                        aggregate_type="TASK_NODE",
                        aggregate_id=(
                            f"{admitted.graph_id}@{admitted.graph_revision}/{admitted.node_id}"
                        ),
                        aggregate_version=version + 1,
                        event_type=f"TASK_NODE_{target_status}",
                        evidence_refs=evidence_refs,
                    ),
                    verification_result=verification_result,
                    acceptance_judgement=acceptance_judgement,
                )
            )
        )

    async def _transition_graph_with_acceptance_effects(
        self,
        admitted: AdmittedExecution,
        graph: Mapping[str, Any],
        *,
        effect_intents: Sequence[Any],
    ) -> dict[str, Any]:
        version = _version(graph)
        result = await _maybe_await(
            self._store.transition_graph_and_stage_acceptance_effects(
                admitted.graph_id,
                admitted.graph_revision,
                expected_version=version,
                outbox=self._outbox(
                    admitted,
                    aggregate_type="TASK_GRAPH",
                    aggregate_id=admitted.graph_id,
                    aggregate_version=version + 1,
                    event_type="TASK_GRAPH_COMPLETED",
                ),
                effect_intents=effect_intents,
            )
        )
        projected = _mapping(
            result,
            field_name="atomic graph acceptance effect result",
        )
        completed = projected.get("graph")
        if not isinstance(completed, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "atomic graph acceptance did not return its completed graph",
            )
        return dict(completed)

    async def _cancel_waiting_review_node(
        self,
        admitted: AdmittedExecution,
        node: Mapping[str, Any],
        *,
        evidence_refs: Sequence[str] = (),
    ) -> dict[str, Any]:
        version = _version(node)
        return dict(
            await _maybe_await(
                self._store.request_cancel_node(
                    admitted.graph_id,
                    admitted.graph_revision,
                    admitted.node_id,
                    expected_version=version,
                    outbox=self._outbox(
                        admitted,
                        aggregate_type="TASK_NODE",
                        aggregate_id=(
                            f"{admitted.graph_id}@{admitted.graph_revision}/{admitted.node_id}"
                        ),
                        aggregate_version=version + 1,
                        event_type="TASK_NODE_CANCELLED",
                        evidence_refs=evidence_refs,
                    ),
                )
            )
        )

    async def _transition_attempt(
        self,
        admitted: AdmittedExecution,
        attempt: Mapping[str, Any],
        *,
        lease_id: str,
        fencing_token: int,
        target_status: str,
        evidence_refs: Sequence[str] = (),
        error_ref: str | None = None,
    ) -> dict[str, Any]:
        del lease_id  # binding is checked by the durable store through the active lease
        attempt_id = _required_record_text(attempt, "attempt_id")
        version = _version(attempt)
        return dict(
            await _maybe_await(
                self._store.transition_attempt(
                    attempt_id,
                    owner_id=self._owner_id,
                    fencing_token=fencing_token,
                    expected_version=version,
                    target_status=target_status,
                    error_ref=error_ref,
                    outbox=self._outbox(
                        admitted,
                        aggregate_type="TASK_ATTEMPT",
                        aggregate_id=attempt_id,
                        aggregate_version=version + 1,
                        event_type=f"TASK_ATTEMPT_{target_status}",
                        attempt_id=attempt_id,
                        evidence_refs=evidence_refs,
                    ),
                )
            )
        )

    async def _reject_budget_charge(
        self,
        admitted: AdmittedExecution,
        charge: BudgetCharge,
        *,
        attempt_id: str,
        fencing_token: int,
    ) -> GovernedExecutionReceipt:
        await self._mark_worker_failure(
            admitted,
            attempt_id=attempt_id,
            fencing_token=fencing_token,
            reason_code=GOVERNED_BUDGET_EXCEEDED,
            graph_target="BLOCKED",
            evidence_refs=(charge.evidence_ref,),
        )
        budget = await self._budgets.finalize(
            charge.context, target_status=BudgetReservationStatus.RELEASED
        )
        return self._receipt(
            admitted,
            attempt_id=attempt_id,
            status="BUDGET_BLOCKED",
            verification_status="NOT_RUN",
            accepted=False,
            reason_code=GOVERNED_BUDGET_EXCEEDED,
            evidence_refs=(charge.evidence_ref,),
            projection={
                "budget": budget.execution_payload(),
                "exceeded_dimensions": list(charge.exceeded_dimensions),
                "requested_action": budget.policy.on_exceeded.value,
                "enforced_action": "BLOCK",
            },
        )

    async def _mark_worker_failure(
        self,
        admitted: AdmittedExecution,
        *,
        attempt_id: str,
        fencing_token: int,
        reason_code: str,
        graph_target: str = "FAILED",
        evidence_refs: Sequence[str] = (),
    ) -> None:
        error_ref = "taskerror://" + hashlib.sha256(
            f"{attempt_id}\n{reason_code}".encode("utf-8")
        ).hexdigest()
        try:
            attempt = await _maybe_await(self._store.get_attempt(attempt_id))
            if _status(attempt) == "RUNNING":
                await self._transition_attempt(
                    admitted,
                    attempt,
                    lease_id="",
                    fencing_token=fencing_token,
                    target_status="FAILED",
                    error_ref=error_ref,
                    evidence_refs=(error_ref, *evidence_refs),
                )
            node = await _maybe_await(
                self._store.get_node(admitted.graph_id, admitted.graph_revision, admitted.node_id)
            )
            if _status(node) == "RUNNING":
                await self._transition_node(
                    admitted, node, "FAILED", evidence_refs=(error_ref, *evidence_refs)
                )
            graph = await _maybe_await(self._store.get_graph(admitted.graph_id, admitted.graph_revision))
            if _status(graph) == "ACTIVE":
                await self._transition_graph(admitted, graph, graph_target)
        finally:
            await self._publish_outbox(best_effort=True)

    def _outbox(
        self,
        admitted: AdmittedExecution,
        *,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
        attempt_id: str | None = None,
        evidence_refs: Sequence[str] = (),
    ) -> Any:
        material = "\n".join(
            (
                admitted.command_id,
                aggregate_type,
                aggregate_id,
                str(aggregate_version),
                event_type,
            )
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return self._outbox_factory.create(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=int(aggregate_version),
            event_type=event_type,
            payload_ref=f"taskstore://event/{aggregate_type}/{aggregate_id}/{int(aggregate_version)}",
            idempotency_key=f"governed:{digest}",
            attempt_id=attempt_id,
            evidence_refs=tuple(dict.fromkeys(_text(item) for item in evidence_refs if _text(item))),
            outbox_id=f"outbox::governed::{digest}",
        )

    async def _publish_outbox(self, *, best_effort: bool = False) -> dict[str, Any]:
        try:
            result = await _maybe_await(self._publisher.publish_pending(limit=self._publish_limit))
        except Exception:
            if best_effort:
                return {"status": "DEGRADED"}
            raise
        return dict(result) if isinstance(result, Mapping) else {"status": "OK"}

    def _receipt(
        self,
        admitted: AdmittedExecution,
        *,
        attempt_id: str,
        status: str,
        verification_status: str,
        accepted: bool,
        receipt_ref: str | None = None,
        payload_digest: str | None = None,
        replayed: bool | None = None,
        reason_code: str | None = None,
        evidence_refs: Sequence[str] = (),
        projection: Mapping[str, Any] | None = None,
    ) -> GovernedExecutionReceipt:
        return GovernedExecutionReceipt(
            command_id=admitted.command_id,
            work_order_id=admitted.work_order_id,
            assignment_id=admitted.assignment_id,
            executor=admitted.executor,
            worker_route=admitted.worker_route,
            graph_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
            node_id=admitted.node_id,
            attempt_id=attempt_id,
            reservation_id=admitted.reservation_id,
            receipt_ref=receipt_ref,
            payload_digest=payload_digest,
            status=str(status),
            verification_status=str(verification_status),
            accepted=bool(accepted),
            replayed=admitted.replayed if replayed is None else bool(replayed),
            reason_code=str(reason_code or "").strip() or None,
            evidence_refs=tuple(dict.fromkeys(_text(item) for item in evidence_refs if _text(item))),
            projection=dict(projection or {}),
        )

    @staticmethod
    def _approved_commit_event(
        worker_commit: Mapping[str, Any],
        receipt: GovernedExecutionReceipt,
    ) -> dict[str, Any]:
        event = dict(worker_commit)
        event.update(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "reason_code": None,
                "governed": True,
                "execution_receipt": receipt.to_dict(),
            }
        )
        return event

    @staticmethod
    def _terminal_event(receipt: GovernedExecutionReceipt) -> dict[str, Any]:
        if receipt.accepted:
            event = dict(receipt.projection)
            event.update({
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "reason_code": None,
                "governed": True,
                "replayed": receipt.replayed,
                "execution_receipt": receipt.to_dict(),
            })
            return event
        return {
            "phase": "TERMINAL",
            "done": True,
            "decision": "REJECT",
            "reason_code": receipt.reason_code or GOVERNED_DURABLE_STATE_CONFLICT,
            "governed": True,
            "replayed": receipt.replayed,
            "execution_receipt": receipt.to_dict(),
        }


async def _worker_event_stream(
    worker: Any,
    *,
    command: HostCommand,
    work_order: Any,
    execution_context: Mapping[str, Any],
) -> AsyncIterator[Mapping[str, Any]]:
    stream_method = getattr(worker, "execute_stream", None)
    if callable(stream_method):
        stream = stream_method(
            command=command,
            work_order=work_order,
            execution_context=execution_context,
        )
        if inspect.isawaitable(stream):
            stream = await stream
        if hasattr(stream, "__aiter__"):
            async for event in stream:
                yield event
            return
        if isinstance(stream, Sequence) and not isinstance(stream, (str, bytes, bytearray)):
            for event in stream:
                yield event
            return
        raise GovernedRuntimeError(GOVERNED_WORKER_FAILED, "worker stream is not iterable")

    execute_method = getattr(worker, "execute", None)
    if not callable(execute_method):
        raise GovernedRuntimeError(
            GOVERNED_WORKER_FAILED,
            "selected worker exposes neither execute_stream nor execute",
        )
    result = execute_method(
        command=command,
        work_order=work_order,
        execution_context=execution_context,
    )
    result = await _maybe_await(result)
    payload = _mapping(result, field_name="worker result")
    if not _is_terminal_commit(payload):
        payload = {**payload, "phase": "COMMIT", "done": True}
    yield payload


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    serializer = getattr(value, "to_dict", None)
    if callable(serializer):
        payload = serializer()
        if isinstance(payload, Mapping):
            return dict(payload)
    raise GovernedRuntimeError(GOVERNED_DURABLE_STATE_CONFLICT, f"{field_name} must be a mapping")


def _nested_mapping(value: Mapping[str, Any], key: str) -> dict[str, Any]:
    nested = value.get(key)
    return dict(nested) if isinstance(nested, Mapping) else {}


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


def _node_input_refs(node: Mapping[str, Any]) -> tuple[str, ...]:
    raw = _nested_mapping(node, "node").get("input_refs")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(_text(item) for item in raw if _text(item)))


def _record_evidence_refs(record: Mapping[str, Any]) -> tuple[str, ...]:
    raw = record.get("evidence_refs") if isinstance(record, Mapping) else None
    if not isinstance(raw, (list, tuple)):
        raw = _nested_mapping(record, "node").get("evidence_refs")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(_text(item) for item in raw if _text(item)))


def _review_evidence_refs(gate: TaskNodeReviewGate) -> tuple[str, ...]:
    refs: list[str] = []
    if _text(gate.review_id):
        refs.append(f"review://{_text(gate.review_id)}")
    if _text(gate.checkpoint_id):
        refs.append(_text(gate.checkpoint_id))
    return tuple(dict.fromkeys(refs))


def _text(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _status(record: Mapping[str, Any]) -> str:
    if not isinstance(record, Mapping):
        return ""
    nested = record.get("node") if isinstance(record.get("node"), Mapping) else record.get("graph")
    return _text(record.get("status") or (nested or {}).get("status")).upper()


def _version(record: Mapping[str, Any]) -> int:
    value = record.get("status_version") if isinstance(record, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "durable record lacks an exact non-negative status_version",
        )
    return value


def _required_record_text(record: Mapping[str, Any], key: str) -> str:
    value = _text(record.get(key)) if isinstance(record, Mapping) else ""
    if not value:
        raise GovernedRuntimeError(GOVERNED_DURABLE_STATE_CONFLICT, f"durable {key} is required")
    return value


def _record_positive_int(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key) if isinstance(record, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GovernedRuntimeError(GOVERNED_DURABLE_STATE_CONFLICT, f"durable {key} must be positive")
    return value


def _record_non_negative_int(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key) if isinstance(record, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"durable {key} must be a non-negative integer",
        )
    return value


def _optional_record_non_negative_int(
    record: Mapping[str, Any],
    key: str,
) -> int | None:
    value = record.get(key) if isinstance(record, Mapping) else None
    if value is None:
        return None
    return _record_non_negative_int(record, key)


def _reservation_result(value: Any) -> tuple[bool, dict[str, Any]]:
    if isinstance(value, Mapping):
        acquired = value.get("acquired")
        reservation = value.get("reservation")
    else:
        acquired = getattr(value, "acquired", None)
        reservation = getattr(value, "reservation", None)
    if not isinstance(acquired, bool) or not isinstance(reservation, Mapping):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "reserve_operation returned an invalid result",
        )
    return acquired, dict(reservation)


def _attempt_id(admitted: AdmittedExecution, *, attempt_no: int) -> str:
    material = (
        f"{admitted.graph_id}@{admitted.graph_revision}/{admitted.node_id}/"
        f"{admitted.assignment_id}/{int(attempt_no)}"
    )
    return "attempt::governed::" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _payload_digest(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GovernedRuntimeError(
            GOVERNED_WORKER_COMMIT_INVALID,
            "worker COMMIT is not canonical finite JSON",
        ) from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _is_terminal_commit(event: Mapping[str, Any]) -> bool:
    return _text(event.get("phase")).upper() == "COMMIT" and bool(event.get("done"))


def _worker_receipt_ref(event: Mapping[str, Any]) -> str | None:
    direct = _text(event.get("receipt_ref"))
    if direct:
        return direct
    nested = event.get("execution_receipt")
    if isinstance(nested, Mapping):
        value = _text(nested.get("receipt_ref"))
        return value or None
    return None


def _evidence_refs(value: Mapping[str, Any]) -> tuple[str, ...]:
    raw = value.get("evidence_refs") if isinstance(value, Mapping) else None
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(_text(item) for item in raw if _text(item)))


def _exception_reason(exc: BaseException, default: str) -> str:
    return _text(getattr(exc, "reason_code", None)) or _text(default) or exc.__class__.__name__.upper()


def _precharged_tool_call_count(worker_route: str) -> int:
    """Charge only routes that actually cross a tool/delegate boundary.

    Unknown compatibility routes keep the historical one-call precharge;
    canonical chat is model work and must not be misreported as a tool call.
    """

    kind = str(worker_route or "").strip().partition(":")[0].lower()
    return 0 if kind == "chat" else 1


def _ref_token(value: Any) -> str:
    text = _text(value)
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in text).strip("-") or "unknown"


def _digest_from_receipt_ref(receipt_ref: str | None) -> str | None:
    if not receipt_ref:
        return None
    tail = receipt_ref.rsplit("/", 1)[-1]
    if len(tail) == 64 and all(char in "0123456789abcdef" for char in tail.lower()):
        return "sha256:" + tail.lower()
    return None


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
