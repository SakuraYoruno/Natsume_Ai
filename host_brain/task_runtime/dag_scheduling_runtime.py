"""Durable outer-loop orchestration for governed DAG execution."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from natsume_contracts.task.v1 import HostCommand
from host_brain.contracts.agent_runtime.budget_policy import BudgetReservationStatus
from host_brain.contracts.agent_runtime.node_artifact import (
    is_task_result_artifact_ref,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphCompletionPolicy,
    TaskNodeStatus,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
)

from .admission import AdmittedExecution, AdmittedTaskNode
from .accepted_graph_finalization import AcceptedGraphFinalizationRuntime
from .budget_runtime import BudgetRuntimeContext, DurableBudgetRuntime
from .coordinator_receipts import (
    _digest_from_receipt_ref,
    build_execution_receipt,
    terminal_event,
)
from .coordinator_records import (
    _ParallelNodeOutcome,
    _assert_durable_identity,
    _attempt_id,
    _mapping,
    _node_input_refs,
    _optional_record_non_negative_int,
    _record_evidence_refs,
    _record_non_negative_int,
    _required_record_text,
    _review_evidence_refs,
    _status,
    _text,
    _version,
)
from .dag_scheduler import (
    DagNodeScheduleCandidate,
    DagScheduleInputError,
    compute_dag_schedule,
)
from .dag_scheduling_preflight import prepare_dag_scheduling_preflight
from .dag_scheduling_review_denial import (
    assert_denied_review_gate,
    assert_denied_review_waiting_node,
    build_review_terminal_projection,
)
from .dag_scheduling_acceptance_review import (
    reopen_post_execution_acceptance_review,
)
from .dag_scheduling_reentry import reconstruct_parallel_outcomes
from .dag_scheduling_completion_replay import (
    is_completed_internal_checkpoint_without_parallel_decision,
)
from .dag_checkpoint_reentry import settle_committed_checkpoint_reentry
from .dag_scheduling_settlement import settle_waiting_approval
from .review_denial_lifecycle import reopen_denied_review_terminal, settle_denied_review
from .errors import (
    GOVERNED_BUDGET_EXCEEDED,
    GOVERNED_BUDGET_STATE_CONFLICT,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_EXECUTION_IN_PROGRESS,
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GOVERNED_REVIEW_DENIED,
    GOVERNED_REVIEW_REQUIRED,
    GovernedRuntimeError,
)
from .graph_cancellation_runtime import GraphCancellationRuntime
from .existing_operation_resolution import has_blocking_live_recovery_lease
from .internal_checkpoint_budget_rejection import is_internal_budget_terminal
from .node_execution_projection import NodeExecutionProjection
from .node_execution_records import build_review_context
from .node_execution_runtime import DurableNodeExecutionRuntime
from .node_lifecycle_runtime import DurableNodeLifecycleRuntime
from .parallel_settlement_runtime import ParallelSettlementRuntime
from .parallel_success_grant_reentry import restore_prior_parallel_success_grants
from .parallel_success_runtime import ParallelSuccessRuntime
from .ports import ExecutionProjectionReaderPort, TaskStorePort
from .preoperation_attempt_recovery import (
    classify_preoperation_recovery_candidates,
)
from .review_runtime import TaskNodeReviewRuntime
from .runtime_outbox import RuntimeOutbox


class DurableDagSchedulingRuntime:
    """Advance durable graph state through review, scheduling, and fan-out."""

    __slots__ = (
        "_store",
        "_runtime_outbox",
        "_budgets",
        "_node_lifecycle",
        "_node_execution_projection",
        "_node_execution",
        "_parallel_settlement",
        "_parallel_success",
        "_graph_cancellations",
        "_reviews",
        "_bounded_fanout_enabled",
        "_atomic_graph_success_enabled",
        "_acceptance_effects_enabled",
        "_accepted_graph_finalization",
        "_projection_reader",
        "_scheduler_checkpoint_continuation_authority",
        "_fanout_event_buffer_size",
    )

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        runtime_outbox: RuntimeOutbox,
        budget_runtime: DurableBudgetRuntime,
        node_lifecycle: DurableNodeLifecycleRuntime,
        node_execution_projection: NodeExecutionProjection,
        node_execution: DurableNodeExecutionRuntime,
        parallel_settlement: ParallelSettlementRuntime,
        parallel_success: ParallelSuccessRuntime,
        graph_cancellations: GraphCancellationRuntime,
        review_runtime: TaskNodeReviewRuntime | None,
        bounded_fanout_enabled: bool,
        atomic_graph_success_enabled: bool,
        acceptance_effects_enabled: bool,
        accepted_graph_finalization: AcceptedGraphFinalizationRuntime,
        projection_reader: ExecutionProjectionReaderPort | None,
        fanout_event_buffer_size: int,
    ) -> None:
        self._store = task_store
        self._runtime_outbox = runtime_outbox
        self._budgets = budget_runtime
        self._node_lifecycle = node_lifecycle
        self._node_execution_projection = node_execution_projection
        self._node_execution = node_execution
        authority_factory = getattr(
            node_execution,
            "_scheduler_owned_checkpoint_continuation_authority",
            None,
        )
        self._scheduler_checkpoint_continuation_authority = (
            authority_factory() if callable(authority_factory) else None
        )
        self._parallel_settlement = parallel_settlement
        self._parallel_success = parallel_success
        self._graph_cancellations = graph_cancellations
        self._reviews = review_runtime
        self._bounded_fanout_enabled = bool(bounded_fanout_enabled)
        self._atomic_graph_success_enabled = bool(atomic_graph_success_enabled)
        self._acceptance_effects_enabled = bool(acceptance_effects_enabled)
        self._accepted_graph_finalization = accepted_graph_finalization
        self._projection_reader = projection_reader
        self._fanout_event_buffer_size = int(fanout_event_buffer_size)

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

    async def execute_stream(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        node_grant_refs: Mapping[str, str] | None = None,
        denied_review_node_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        preflight = await prepare_dag_scheduling_preflight(
            admitted=admitted,
            command=command,
            work_order=work_order,
            node_grant_refs=node_grant_refs,
            denied_review_node_id=denied_review_node_id,
            store=self._store,
            budgets=self._budgets,
            graph_cancellations=self._graph_cancellations,
            supports_completion_policy=self.supports_completion_policy,
        )
        grant_refs = preflight.grant_refs
        # POST Review renews live grants; committed projections retain theirs.
        parallel_success_grant_refs = preflight.parallel_success_grant_refs
        restore_prior_parallel_success_authority = preflight.parallel_success_enabled
        denied_review_node = preflight.denied_review_node
        parallel_success_enabled = preflight.parallel_success_enabled
        if preflight.cancellation_event is not None:
            yield preflight.cancellation_event
            return
        budget = preflight.budget
        if budget is None:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "DAG preflight returned neither cancellation nor Budget authority",
            )

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
            if (
                denied_review_node
                and statuses[denied_review_node]
                not in {"WAITING_APPROVAL", "WAITING_ACCEPTANCE_REVIEW"}
            ):
                yield await reopen_denied_review_terminal(
                    store=self._store,
                    projection=self._node_execution_projection,
                    admitted=admitted.for_node(denied_review_node),
                    budget=budget,
                )
                return
            if restore_prior_parallel_success_authority:
                await restore_prior_parallel_success_grants(
                    admitted=admitted,
                    statuses=statuses,
                    grant_refs=parallel_success_grant_refs,
                    node_execution_projection=self._node_execution_projection,
                    projection_reader=self._projection_reader
                )
                restore_prior_parallel_success_authority = False
            assert_denied_review_waiting_node(denied_review_node, statuses=statuses)
            final_admission = admitted.for_node(
                admitted.result_node_id or admitted.nodes[-1].node_id
            )

            # Detect an active Operation checkpoint before any aggregate
            # Budget or parallel-settlement shortcut.  A worker usage may
            # already have atomically moved the shared Budget to EXCEEDED
            # while its response was lost; node-bound recovery must retain
            # precedence in both single-node and multi-node graphs.
            recovery_bindings: list[
                tuple[AdmittedTaskNode, dict[str, Any]]
            ] = []
            operation_free_node_ids: set[str] = set()
            for binding in admitted.nodes:
                if statuses[binding.node_id] not in {
                    "LEASED",
                    "RUNNING",
                    "EXECUTED",
                    "VERIFYING",
                    "VERIFIED",
                }:
                    continue
                candidate = admitted.for_node(binding.node_id)
                reservation = await self._node_execution_projection.try_operation(
                    candidate.reservation_id
                )
                if reservation is not None:
                    recovery_bindings.append(
                        (
                            binding,
                            _mapping(
                                reservation,
                                field_name="active Operation reservation",
                            ),
                        )
                    )
                else:
                    operation_free_node_ids.add(binding.node_id)

            # Freeze the Attempt/Lease view immediately after the exact
            # Operation checkpoint scan.  A pre-Operation crash must be
            # classified before shared Budget or parallel terminal shortcuts
            # can close the Graph, and the later bounded scheduler must reuse
            # this same consistency boundary rather than perform a second read.
            # Every Operation-free active node blocks aggregate closeout;
            # only the expired/unowned classifier subset becomes READY below.
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
            preoperation_recovery_candidates = (
                classify_preoperation_recovery_candidates(
                    scheduler_snapshot,
                    graph_id=admitted.graph_id,
                    graph_revision=admitted.graph_revision,
                    operation_free_node_ids=operation_free_node_ids,
                )
            )
            preoperation_recovery_node_ids = frozenset(
                candidate.node_id
                for candidate in preoperation_recovery_candidates
            )
            if operation_free_node_ids - preoperation_recovery_node_ids:
                raise GovernedRuntimeError(
                    GOVERNED_EXECUTION_IN_PROGRESS,
                    "Operation-free active TaskNode still has a live execution owner",
                )
            if recovery_bindings:
                observed_ts_ms = _record_non_negative_int(
                    scheduler_snapshot,
                    "observed_ts_ms",
                )
                raw_active_leases = scheduler_snapshot.get("active_leases")
                if not isinstance(raw_active_leases, Sequence) or isinstance(
                    raw_active_leases,
                    (str, bytes, bytearray),
                ):
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "durable scheduler Lease snapshot is invalid",
                    )
                if has_blocking_live_recovery_lease(
                    recovery_bindings,
                    raw_active_leases,
                    observed_ts_ms=observed_ts_ms,
                ):
                    raise GovernedRuntimeError(
                        GOVERNED_EXECUTION_IN_PROGRESS,
                        "Operation reservation still has a live execution owner",
                    )
                binding, _ = recovery_bindings[0]
                recovery_admission = admitted.for_node(binding.node_id)
                recovery_terminal: Mapping[str, Any] | None = None
                recovery_execution_kwargs: dict[str, Any] = {
                    "admitted": recovery_admission,
                    "command": command,
                    "work_order": work_order,
                    "budget": budget,
                    "node_grant_ref": grant_refs.get(recovery_admission.node_id),
                    "defer_shared_terminal": len(admitted.nodes) > 1,
                }
                if (
                    len(admitted.nodes) > 1
                    and self._scheduler_checkpoint_continuation_authority is not None
                ):
                    recovery_execution_kwargs[
                        "scheduler_checkpoint_continuation_authority"
                    ] = self._scheduler_checkpoint_continuation_authority
                async for event in self._node_execution.execute_stream(
                    **recovery_execution_kwargs,
                ):
                    if bool(event.get("done")):
                        recovery_terminal = dict(event)
                    else:
                        yield event
                if recovery_terminal is None:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "committed checkpoint recovery produced no terminal receipt",
                    )
                budget, checkpoint_terminal = (
                    await settle_committed_checkpoint_reentry(
                        admitted=admitted,
                        recovery_admission=recovery_admission,
                        command=command,
                        recovery_terminal=recovery_terminal,
                        budget=budget,
                        parallel_success_enabled=parallel_success_enabled,
                        parallel_settlement=self._parallel_settlement,
                    )
                )
                if checkpoint_terminal is not None:
                    yield checkpoint_terminal
                    return
                budget = await self._budgets.refresh(budget)
                continue

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
                and not denied_review_node
                and not operation_free_node_ids
                and (
                    budget.status == "EXCEEDED"
                    or set(statuses.values()).intersection(reentry_terminal_statuses)
                )
            ):
                await self._parallel_settlement.assert_parallel_reentry_idle(
                    admitted=admitted,
                    durable_nodes=durable_nodes,
                )
                outcomes = await reconstruct_parallel_outcomes(
                    admitted=admitted,
                    graph=graph,
                    durable_nodes=durable_nodes,
                    node_execution_projection=self._node_execution_projection,
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
            if (
                pre_review_node_id is not None
                and not preoperation_recovery_node_ids
                and graph_status in {"CREATED", "ACTIVE", "BLOCKED"}
            ):
                if self._reviews is None:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "TaskNode is WAITING_APPROVAL but no durable Review runtime is wired",
                    )
                review_admission = admitted.for_node(pre_review_node_id)
                review_node = durable_nodes[pre_review_node_id]
                if graph_status == "CREATED":
                    graph = await self._node_lifecycle.transition_graph(
                        review_admission,
                        graph,
                        "ACTIVE",
                    )
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
                assert_denied_review_gate(
                    denied_review_node,
                    disposition=gate.disposition,
                    phase="pre-execution",
                )
                review_refs = _review_evidence_refs(gate)
                pre_review_refs = tuple(
                    dict.fromkeys((*_record_evidence_refs(review_node), *review_refs))
                )
                denial_settlement: Mapping[str, Any] | None = None
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
                    denied = await settle_denied_review(
                        store=self._store, runtime_outbox=self._runtime_outbox,
                        admitted=review_admission, node=review_node, graph=graph,
                        budget=budget, gate=gate, evidence_refs=pre_review_refs,
                    )
                    graph = denied["graph"]
                    graph_status = _status(graph)
                    budget = denied["budget"]
                    pre_review_refs = tuple(denied["evidence_refs"])
                    denial_settlement = denied["settlement"]
                    reason_code = GOVERNED_REVIEW_DENIED
                    status = "REVIEW_DENIED"
                else:
                    reason_code = GOVERNED_REVIEW_REQUIRED
                    status = "WAITING_APPROVAL"
                await self._runtime_outbox.publish_pending()
                projection = build_review_terminal_projection(gate=gate, budget=budget, settlement=denial_settlement)
                receipt = build_execution_receipt(
                    review_admission,
                    attempt_id=None,
                    status=status,
                    verification_status="NOT_RUN",
                    accepted=False,
                    reason_code=reason_code,
                    evidence_refs=pre_review_refs,
                    projection=projection,
                )
                waiting_event = terminal_event(receipt)
                if status == "WAITING_APPROVAL" and graph_status == "ACTIVE":
                    budget, waiting_event = await settle_waiting_approval(
                        admitted=admitted,
                        receipt_event=waiting_event,
                        budget=budget,
                        parallel_settlement=self._parallel_settlement,
                    )
                yield waiting_event
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
                and not preoperation_recovery_node_ids
                and self._reviews is not None
                and graph_status in {"ACTIVE", "BLOCKED"}
            ):
                review_admission = admitted.for_node(post_review_node_id)
                review_node = durable_nodes[post_review_node_id]
                if graph_status == "ACTIVE":
                    graph = await self._node_lifecycle.transition_graph(review_admission, graph, "BLOCKED")
                    graph_status = _status(graph)
                attempt_id, acceptance_decision, gate = (
                    await reopen_post_execution_acceptance_review(
                        store=self._store,
                        reviews=self._reviews,
                        projection=self._node_execution_projection,
                        admitted=review_admission,
                        node=review_node,
                        command=command,
                        work_order=work_order,
                    )
                )
                assert_denied_review_gate(
                    denied_review_node,
                    disposition=gate.disposition,
                    phase="acceptance",
                )
                review_refs = _review_evidence_refs(gate)
                post_review_refs = tuple(
                    dict.fromkeys((*_record_evidence_refs(review_node), *review_refs))
                )
                reservation = await self._node_execution_projection.try_operation(
                    review_admission.reservation_id
                )
                receipt_ref = _text((reservation or {}).get("receipt_ref")) or None
                denial_settlement = None
                if gate.allowed:
                    acceptance_judgement = {
                        "schema_version": "Natsume.agent.acceptance_judgement.v1",
                        "judgement_id": f"review-accept::{gate.review_id}",
                        "work_order_id": review_admission.work_order_id,
                        "task_graph_id": review_admission.graph_id,
                        "task_node_id": review_admission.node_id,
                        "attempt_id": attempt_id,
                        "decision": "ACCEPT",
                        "reason_code": None,
                        "evidence_refs": list(post_review_refs),
                        # Approval does not replay the worker, but it is an
                        # explicit durable acceptance decision for the frozen
                        # node criteria.  Preserve the exact criterion list
                        # so AcceptanceGate can verify that the same TaskNode
                        # the reviewer approved is the one being accepted.
                        "criteria": [
                            {
                                "criterion_id": (
                                    f"review-criterion::{gate.review_id}::{index}"
                                ),
                                "criterion_text": criterion,
                                "verdict": "PASS",
                                "evidence_refs": list(post_review_refs),
                                "deterministic": True,
                                "reason_code": None,
                                "critic_verdict": None,
                            }
                            for index, criterion in enumerate(
                                review_admission.active_node.acceptance_criteria,
                                start=1,
                            )
                        ],
                        "critic_candidate": {
                            "source": "durable_review",
                            "review": gate.to_dict(),
                        },
                        "started_at_ms": command.created_at_ms,
                        "finished_at_ms": command.created_at_ms,
                    }
                    if not receipt_ref or self._projection_reader is None:
                        raise GovernedRuntimeError(
                            GOVERNED_DURABLE_STATE_CONFLICT,
                            "review-approved TaskNode has no durable worker projection",
                        )
                    loaded = await _maybe_await(
                        self._projection_reader.load(receipt_ref)
                    )
                    try:
                        worker_output = validate_worker_projection_payload(
                            loaded if isinstance(loaded, Mapping) else {},
                            expected_projection_id=receipt_ref,
                        )
                    except WorkerProjectionStoreError as exc:
                        raise GovernedRuntimeError(
                            GOVERNED_DURABLE_STATE_CONFLICT,
                            "review-approved TaskNode worker projection is invalid",
                        ) from exc
                    historical_node_grant_ref = _text(
                        worker_output.get("node_grant_ref")
                    )
                    if not historical_node_grant_ref:
                        raise GovernedRuntimeError(
                            GOVERNED_DURABLE_STATE_CONFLICT,
                            "review-approved TaskNode worker projection lacks "
                            "its historical node grant",
                        )
                    parallel_success_grant_refs[review_admission.node_id] = (
                        historical_node_grant_ref
                    )
                    await self._node_lifecycle.accept_node_output(
                        review_admission,
                        review_node,
                        attempt_id=attempt_id,
                        receipt_ref=receipt_ref,
                        worker_output=worker_output,
                        evidence_refs=post_review_refs,
                        acceptance_judgement=acceptance_judgement,
                    )
                    if graph_status == "BLOCKED":
                        await self._node_lifecycle.transition_graph(review_admission, graph, "ACTIVE")
                    await self._runtime_outbox.publish_pending()
                    continue
                if gate.disposition == "REVIEW_DENIED":
                    denied = await settle_denied_review(
                        store=self._store, runtime_outbox=self._runtime_outbox,
                        admitted=review_admission, node=review_node, graph=graph,
                        budget=budget, gate=gate, evidence_refs=post_review_refs,
                        acceptance_decision=acceptance_decision,
                        attempt_id=attempt_id,
                    )
                    graph = denied["graph"]
                    graph_status = _status(graph)
                    budget = denied["budget"]
                    post_review_refs = tuple(denied["evidence_refs"])
                    denial_settlement = denied["settlement"]
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
                projection = build_review_terminal_projection(gate=gate, budget=budget, settlement=denial_settlement)
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

            if budget.status == "EXCEEDED" and not operation_free_node_ids:
                if graph_status in {"CREATED", "ACTIVE"}:
                    graph = await self._node_lifecycle.transition_graph(final_admission, graph, "BLOCKED")
                    graph_status = _status(graph)
                    budget = await self._budgets.finalize(
                        budget, target_status=BudgetReservationStatus.RELEASED
                    )
                else:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "terminal TaskGraph has an unsettled exceeded Budget; "
                        "terminal reconciliation is required",
                    )
            if budget.status == "RELEASED" and not operation_free_node_ids:
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

            if (
                statuses
                and not preoperation_recovery_node_ids
                and all(status == "ACCEPTED" for status in statuses.values())
            ):
                if parallel_success_enabled:
                    if await is_completed_internal_checkpoint_without_parallel_decision(
                        task_store=self._store,
                        admitted=admitted,
                        command=command,
                        graph_status=graph_status,
                    ):
                        async for event in self._node_execution.execute_stream(
                            admitted=final_admission,
                            command=command,
                            work_order=work_order,
                            budget=budget,
                            node_grant_ref=grant_refs.get(final_admission.node_id),
                        ):
                            yield event
                        return
                    budget, graph_terminal = await self._parallel_success.complete(
                        admitted=admitted,
                        command=command,
                        work_order=work_order,
                        budget=budget,
                        node_grant_refs=parallel_success_grant_refs,
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
                    graph = await self._accepted_graph_finalization.complete(
                        admitted=final_admission,
                        command=command,
                        work_order=work_order,
                        graph=graph,
                        node_grant_ref=grant_refs.get(final_admission.node_id),
                        worker_commit=None,
                        attempt_id=None,
                    )
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
            if (
                not preoperation_recovery_node_ids
                and (blocked_node_id is not None or failed_node_id is not None)
            ):
                terminal_node_id = failed_node_id or blocked_node_id
                decision = await self._node_execution_projection.matching_parallel_decision(
                    admitted,
                    graph=graph,
                )
                if decision is not None:
                    selected = self._node_execution_projection.selected_parallel_decision_outcome(decision)
                    terminal_node_id = _required_record_text(selected, "node_id")
                terminal_admission = admitted.for_node(terminal_node_id or admitted.node_id)
                if (
                    failed_node_id is not None
                    and graph_status == "BLOCKED"
                    and budget.status in {"ACTIVE", "EXCEEDED"}
                ):
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "BLOCKED failure Graph has an unsettled Budget; "
                        "terminal reconciliation is required",
                    )
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

            if (
                not preoperation_recovery_node_ids
                and graph_status in {"FAILED", "CANCELLED", "COMPLETED"}
            ):
                if budget.status in {"ACTIVE", "EXCEEDED"}:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "terminal TaskGraph has an unsettled Budget; "
                        "terminal reconciliation is required",
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
            if not preoperation_recovery_node_ids and graph_status == "BLOCKED":
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "TaskGraph is BLOCKED without a durable acceptance-review node",
                )
            if graph_status not in {"CREATED", "ACTIVE"}:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"TaskGraph is not schedulable: {graph_status}",
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
                            status=(
                                TaskNodeStatus.READY
                                if binding.node_id
                                in preoperation_recovery_node_ids
                                else TaskNodeStatus(statuses[binding.node_id])
                            ),
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

            if decision.dependency_ready_node_ids:
                for node_id in decision.dependency_ready_node_ids:
                    dependency_node = durable_nodes[node_id]
                    if any(
                        is_task_result_artifact_ref(ref)
                        for ref in _node_input_refs(dependency_node)
                    ):
                        resolved = await _maybe_await(
                            self._store.resolve_node_input_artifacts(
                                admitted.graph_id,
                                admitted.graph_revision,
                                node_id,
                            )
                        )
                        if not isinstance(resolved, Sequence) or isinstance(
                            resolved,
                            (str, bytes, bytearray),
                        ) or any(not isinstance(item, Mapping) for item in resolved):
                            raise GovernedRuntimeError(
                                GOVERNED_DURABLE_STATE_CONFLICT,
                                "dependency-ready TaskNode artifact resolution is invalid",
                            )
                    await self._node_lifecycle.transition_node(
                        admitted.for_node(node_id),
                        dependency_node,
                        "READY",
                    )
                await self._runtime_outbox.publish_pending()
                continue

            selected_node_ids = set(decision.selected_node_ids)
            retry_due_claim_node_ids = (
                set(decision.retry_due_node_ids) & selected_node_ids
            )
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

            review_required_node_ids = TaskNodeReviewRuntime.pre_execution_required_nodes(
                work_order, node_ids=tuple(binding.node_id for binding in claimable)
            )
            review_retry_node_ids = review_required_node_ids & retry_due_claim_node_ids
            if review_retry_node_ids:
                for node_id in sorted(review_retry_node_ids):
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
            review_binding = None
            review_candidates = [
                candidate
                for candidate in claimable
                if candidate.node_id not in preoperation_recovery_node_ids
                and candidate.node_id in review_required_node_ids
            ]
            if review_candidates:
                if self._reviews is None:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "WorkOrder requires durable review but no Review runtime is wired",
                    )
                for candidate in review_candidates:
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
                if graph_status == "CREATED":
                    graph = await self._node_lifecycle.transition_graph(
                        review_admission,
                        graph,
                        "ACTIVE",
                    )
                    graph_status = _status(graph)
                review_node = await self._node_lifecycle.transition_node(
                    review_admission,
                    durable_nodes[binding.node_id],
                    "WAITING_APPROVAL",
                )
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
                    attempt_id=None,
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
                budget, waiting_event = await settle_waiting_approval(
                    admitted=admitted,
                    receipt_event=terminal_event(receipt),
                    budget=budget,
                    parallel_settlement=self._parallel_settlement,
                )
                yield waiting_event
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
                            retry_due_claim=(
                                node_admission.node_id in retry_due_claim_node_ids
                            ),
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
                    retry_due_claim=(
                        node_admission.node_id in retry_due_claim_node_ids
                    ),
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
                if parallel_success_enabled and is_internal_budget_terminal(admitted, command, receipt):
                    yield node_terminal
                    return
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

async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = ["DurableDagSchedulingRuntime"]
