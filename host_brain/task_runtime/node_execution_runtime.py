"""Durable execution of one admitted TaskNode through acceptance."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from natsume_contracts.task.v1 import HostCommand
from host_brain.contracts.agent_runtime.budget_policy import BudgetReservationStatus
from .accepted_graph_finalization import AcceptedGraphFinalizationRuntime
from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext, DurableBudgetRuntime
from .coordinator_receipts import (
    _digest_from_receipt_ref,
    _worker_receipt_ref,
    approved_commit_event,
    build_execution_receipt,
    terminal_event,
)
from .coordinator_records import (
    _assert_durable_identity,
    _attempt_id,
    _evidence_refs,
    _exception_reason,
    _mapping,
    _record_non_negative_int,
    _record_positive_int,
    _required_record_text,
    _reservation_result,
    _review_evidence_refs,
    _status,
    _text,
    _version,
)
from .errors import (
    GOVERNED_ACCEPTANCE_FAILED,
    GOVERNED_ADMISSION_INVALID,
    GOVERNED_BUDGET_EXCEEDED,
    GOVERNED_BUDGET_STATE_CONFLICT,
    GOVERNED_CALLER_CANCELLED,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_EXECUTION_IN_PROGRESS,
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GOVERNED_RECEIPT_COMMIT_FAILED,
    GOVERNED_REVIEW_REQUIRED,
    GOVERNED_TIME_BUDGET_EXCEEDED,
    GOVERNED_VERIFICATION_EVIDENCE_MISSING,
    GOVERNED_VERIFICATION_FAILED,
    GOVERNED_WORKER_COMMIT_INVALID,
    GOVERNED_WORKER_FAILED,
    GOVERNED_WORKER_SELF_CANCELLED,
    GovernedRuntimeError,
    RetryableWorkerPreparationError,
)
from .heartbeat import DurableExecutionHeartbeat
from .internal_checkpoint_budget_rejection import (
    InternalCheckpointBudgetRejectionRuntime,
    is_internal_agent_checkpoint_authority,
    requires_internal_terminal_bundle,
)
from .internal_agent_sealed_checkpoint_recovery import recover_internal_agent_sealed_checkpoint
from .node_execution_projection import NodeExecutionProjection
from .node_execution_preparation import (
    bind_preoperation_recovery_attempt,
    load_preoperation_recovery_candidate,
    preverify_node_claim_authority,
    resolve_node_input_artifacts,
)
from .node_execution_records import (
    _heartbeat_failure_phase,
    _is_terminal_commit,
    _payload_digest,
    _precharged_tool_call_count,
    _ref_token,
    _retry_attempt_available,
    _retry_usage_id,
    build_review_context,
)
from .node_lifecycle_runtime import DurableNodeLifecycleRuntime
from .node_worker_stream import (
    _maybe_await,
    _worker_elapsed_ms,
    bounded_worker_event_stream,
    stop_heartbeat_after_interruption,
)
from .operation_checkpoint_continuation import CommittedCheckpointContinuation
from .operation_interruption_runtime import (
    build_worker_interruption_receipt,
    classify_caller_cancellation,
    classify_interrupted_operation,
)
from .operation_recovery_runtime import DurableOperationRecoveryRuntime
from .ports import (
    AcceptancePort,
    ExecutionProjectionReaderPort,
    GovernedWorkerResolverPort,
    InternalAgentCheckpointUnitOfWorkPort,
    TaskStorePort,
    VerificationPort,
)
from .preoperation_attempt_recovery import (
    PreOperationRecoveryCandidate,
    acquire_preoperation_takeover_lease,
)
from .review_runtime import TaskNodeReviewGate, TaskNodeReviewRuntime
from .runtime_outbox import RuntimeOutbox
from .worker_registry import GovernedWorkerRoute

_logger = logging.getLogger("Natsume.task_runtime.node_execution_runtime")


class DurableNodeExecutionRuntime:
    """Run one admitted node while preserving all durable execution fences."""
    __slots__ = (
        "_acceptance",
        "_budgets",
        "_checkpoint_uow",
        "_checkpoint_continuation",
        "_graph_finalizer",
        "_internal_budget_rejection",
        "_lease_ttl_ms",
        "_node_lifecycle",
        "_node_grant_verifier",
        "_operation_ttl_ms",
        "_operation_recovery",
        "_owner_id",
        "_projection",
        "_reviews",
        "_runtime_outbox",
        "_scheduler_checkpoint_continuation_authority",
        "_store",
        "_verifier",
        "_workers",
    )

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        checkpoint_uow: InternalAgentCheckpointUnitOfWorkPort,
        runtime_outbox: RuntimeOutbox,
        budget_runtime: DurableBudgetRuntime,
        node_lifecycle: DurableNodeLifecycleRuntime,
        node_grant_verifier: Any | None,
        worker_resolver: GovernedWorkerResolverPort,
        verifier: VerificationPort,
        acceptance: AcceptancePort,
        review_runtime: TaskNodeReviewRuntime | None,
        projection: NodeExecutionProjection,
        projection_reader: ExecutionProjectionReaderPort | None,
        graph_finalizer: AcceptedGraphFinalizationRuntime,
        owner_id: str,
        lease_ttl_ms: int,
        operation_ttl_ms: int,
    ) -> None:
        self._store = task_store
        self._checkpoint_uow = checkpoint_uow
        self._runtime_outbox = runtime_outbox
        # The outer durable DAG scheduler receives this opaque identity during
        # composition.  A direct node caller cannot opt into multi-node
        # checkpoint continuation with a boolean switch.
        self._scheduler_checkpoint_continuation_authority = object()
        self._budgets = budget_runtime
        self._node_lifecycle = node_lifecycle
        self._node_grant_verifier = node_grant_verifier
        self._workers = worker_resolver
        self._verifier = verifier
        self._acceptance = acceptance
        self._reviews = review_runtime
        self._projection = projection
        self._graph_finalizer = graph_finalizer
        self._operation_recovery = DurableOperationRecoveryRuntime(
            task_store=task_store,
            projection_reader=projection_reader,
            runtime_outbox=runtime_outbox,
            owner_id=owner_id,
            lease_ttl_ms=lease_ttl_ms,
        )
        self._internal_budget_rejection = (
            InternalCheckpointBudgetRejectionRuntime(
                checkpoint_uow=checkpoint_uow,
                runtime_outbox=runtime_outbox,
                owner_id=owner_id,
            )
        )
        self._checkpoint_continuation = CommittedCheckpointContinuation(
            task_store=task_store,
            checkpoint_uow=checkpoint_uow,
            runtime_outbox=runtime_outbox,
            budget_runtime=budget_runtime,
            node_lifecycle=node_lifecycle,
            verifier=verifier,
            acceptance=acceptance,
            review_runtime=review_runtime,
            owner_id=owner_id,
            graph_finalizer=graph_finalizer,
        )
        self._owner_id = owner_id
        self._lease_ttl_ms = int(lease_ttl_ms)
        self._operation_ttl_ms = int(operation_ttl_ms)

    def _scheduler_owned_checkpoint_continuation_authority(self) -> object:
        """Return the composition-only capability for outer-DAG recovery."""

        return self._scheduler_checkpoint_continuation_authority

    async def execute_stream(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        budget: BudgetRuntimeContext | None = None,
        node_grant_ref: str | None = None,
        defer_shared_terminal: bool = False,
        retry_due_claim: bool = False,
        scheduler_checkpoint_continuation_authority: object | None = None,
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
        _assert_durable_identity(admitted, graph=graph, node=node)
        replay_receipt = None if retry_due_claim else await self._projection.terminal_replay(
            admitted, graph=graph, node=node
        )
        if replay_receipt is not None:
            await self._runtime_outbox.publish_pending()
            yield terminal_event(replay_receipt)
            return
        graph_status = _status(graph)
        node_status = _status(node)
        attempts = await self._projection.attempts_for_node(admitted)
        attempt_numbers = [int(item.get("attempt_no") or 0) for item in attempts]
        if attempt_numbers != list(range(1, len(attempt_numbers) + 1)):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable TaskAttempt journal is not contiguous",
            )
        existing_reservation = await self._projection.try_operation(admitted.reservation_id)
        if existing_reservation is not None:
            if not attempts:
                raise GovernedRuntimeError(
                    GOVERNED_OPERATION_RECOVERY_REQUIRED,
                    "existing operation reservation has no durable Attempt",
                )
            latest_attempt = attempts[-1]
            self._operation_recovery.assert_existing_lineage(
                admitted=admitted,
                reservation=existing_reservation,
                latest_attempt=latest_attempt,
            )
            existing_reservation = (
                await self._operation_recovery.reconcile_reservation_state(
                    admitted=admitted,
                    reservation=existing_reservation,
                    latest_attempt=latest_attempt,
                )
            )
            reservation_status = _status(existing_reservation)
            sealed_recovery_detail: str | None = None
            sealed_worker_elapsed_ms: int | None = None
            if reservation_status in {"RECOVERY_REQUIRED", "COMMITTED"}:
                try:
                    recovered = await recover_internal_agent_sealed_checkpoint(
                        task_store=self._store,
                        checkpoint_uow=self._checkpoint_uow,
                        worker_resolver=self._workers,
                        operation_recovery=self._operation_recovery,
                        runtime_outbox=self._runtime_outbox,
                        admitted=admitted,
                        command=command,
                        work_order=work_order,
                        budget=budget,
                        graph=graph,
                        node=node,
                        reservation=existing_reservation,
                        latest_attempt=latest_attempt,
                        node_grant_ref=node_grant_ref,
                    )
                except Exception as exc:
                    recovered = None
                    _logger.warning(
                        "Internal Agent sealed checkpoint recovery was rejected: %s",
                        exc,
                    )
                    sealed_recovery_detail = _exception_reason(
                        exc,
                        GOVERNED_OPERATION_RECOVERY_REQUIRED,
                    )
                if recovered is not None:
                    existing_reservation, sealed_worker_elapsed_ms = recovered
                    reservation_status = _status(existing_reservation)
            if reservation_status != "COMMITTED":
                active_lease = await _maybe_await(
                    self._store.get_active_lease(
                        _required_record_text(latest_attempt, "attempt_id")
                    )
                )
                recovery_projection = {
                    **self._operation_recovery.unresolved_projection(
                        existing_reservation
                    ),
                    "online_takeover_state": (
                        "WAITING_FOR_LEASE_EXPIRY"
                        if reservation_status == "RESERVED"
                        and active_lease is not None
                        else "SIGNED_RECONCILIATION_REQUIRED"
                    ),
                    "budget": budget.execution_payload(),
                }
                if sealed_recovery_detail:
                    recovery_projection["sealed_checkpoint_state"] = "INVALID"
                    recovery_projection["sealed_checkpoint_detail"] = (
                        sealed_recovery_detail
                    )
                receipt = build_execution_receipt(
                    admitted,
                    attempt_id=_required_record_text(latest_attempt, "attempt_id"),
                    status="RECOVERY_REQUIRED",
                    verification_status="NOT_RUN",
                    accepted=False,
                    reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
                    projection=recovery_projection,
                )
                yield terminal_event(receipt)
                return
            if graph_status != "ACTIVE" or node_status not in {
                "LEASED",
                "RUNNING",
                "EXECUTED",
                "VERIFYING",
                "VERIFIED",
            }:
                raise GovernedRuntimeError(
                    GOVERNED_OPERATION_RECOVERY_REQUIRED,
                    "committed checkpoint durable Graph/Node state is not continuable",
                )
            try:
                checkpoint = await self._operation_recovery.takeover_committed(
                    admitted=admitted,
                    command=command,
                    reservation=existing_reservation,
                    latest_attempt=latest_attempt,
                    node_grant_ref=node_grant_ref,
                    budget_id=budget.budget_id,
                    durable_elapsed_ms_override=sealed_worker_elapsed_ms,
                    allow_multinode_checkpoint_continuation=(
                        scheduler_checkpoint_continuation_authority
                        is self._scheduler_checkpoint_continuation_authority
                    ),
                )
            except GovernedRuntimeError as exc:
                _logger.warning(
                    "Committed checkpoint takeover was rejected: %s",
                    exc,
                )
                receipt = build_execution_receipt(
                    admitted,
                    attempt_id=_required_record_text(
                        latest_attempt,
                        "attempt_id",
                    ),
                    status="RECOVERY_REQUIRED",
                    verification_status="NOT_RUN",
                    accepted=False,
                    reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
                    projection={
                        "online_takeover_state": (
                            "BATCH_RECOVERY_PENDING"
                            if len(admitted.nodes) != 1
                            else "CHECKPOINT_INCOMPLETE"
                        ),
                        "checkpoint_detail": _exception_reason(
                            exc,
                            GOVERNED_OPERATION_RECOVERY_REQUIRED,
                        ),
                        "worker_replayed": False,
                        "budget": budget.execution_payload(),
                    },
                )
                yield terminal_event(receipt)
                return
            async for event in self._checkpoint_continuation.resume(
                admitted=admitted,
                command=command,
                work_order=work_order,
                budget=budget,
                graph=graph,
                node=node,
                checkpoint=checkpoint,
                node_grant_ref=node_grant_ref,
                defer_shared_terminal=defer_shared_terminal,
            ):
                yield event
            return
        preoperation_candidate: PreOperationRecoveryCandidate | None = None
        if graph_status == "ACTIVE" and node_status in {"LEASED", "RUNNING"}:
            preoperation_candidate = await load_preoperation_recovery_candidate(
                task_store=self._store,
                admitted=admitted,
            )
            if preoperation_candidate is None:
                raise GovernedRuntimeError(
                    GOVERNED_EXECUTION_IN_PROGRESS,
                    "TaskNode still has a live pre-operation execution owner",
                )
        elif (graph_status not in {"CREATED", "ACTIVE"} or node_status != (
            "RETRY_PENDING" if retry_due_claim else "READY"
        )):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"admission must expose CREATED/ACTIVE graph and claimable node, got {graph_status}/{node_status}",
            )
        node_input_refs, input_artifacts = await resolve_node_input_artifacts(
            task_store=self._store,
            admitted=admitted,
            node=node,
        )
        review_continuation_authority = await preverify_node_claim_authority(
            verifier=self._node_grant_verifier,
            admitted=admitted,
            command=command,
            work_order=work_order,
            node_grant_ref=node_grant_ref,
        )
        if graph_status == "CREATED":
            if defer_shared_terminal:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel node execution requires an outer-activated TaskGraph",
                )
            graph = await self._node_lifecycle.transition_graph(admitted, graph, "ACTIVE")

        if preoperation_candidate is not None:
            attempt_no = preoperation_candidate.attempt_no
            attempt_id = preoperation_candidate.attempt_id
            latest_attempt = bind_preoperation_recovery_attempt(
                preoperation_candidate,
                attempts,
            )
            try:
                attempt, lease = await acquire_preoperation_takeover_lease(
                    task_store=self._store,
                    runtime_outbox=self._runtime_outbox,
                    admitted=admitted,
                    attempt=latest_attempt,
                    owner_id=self._owner_id,
                    lease_ttl_ms=self._lease_ttl_ms,
                )
            except GovernedRuntimeError as exc:
                raced_reservation = await self._projection.try_operation(
                    admitted.reservation_id
                )
                if (
                    exc.reason_code == GOVERNED_OPERATION_RECOVERY_REQUIRED
                    and raced_reservation is not None
                ):
                    async for event in self.execute_stream(
                        admitted=admitted,
                        command=command,
                        work_order=work_order,
                        budget=budget,
                        node_grant_ref=node_grant_ref,
                        defer_shared_terminal=defer_shared_terminal,
                    ):
                        yield event
                    return
                raise
            node = _mapping(
                await _maybe_await(
                    self._store.get_node(
                        admitted.graph_id,
                        admitted.graph_revision,
                        admitted.node_id,
                    )
                ),
                field_name="pre-operation takeover node",
            )
            _assert_durable_identity(admitted, graph=graph, node=node)
        else:
            attempt_no = len(attempt_numbers) + 1
            attempt_id = _attempt_id(admitted, attempt_no=attempt_no)
            claimed_raw = await _maybe_await(self._store.claim_ready_node(
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
                attempt_created_outbox=self._runtime_outbox.build(
                    command_id=admitted.command_id,
                    aggregate_type="TASK_ATTEMPT",
                    aggregate_id=attempt_id,
                    aggregate_version=0,
                    event_type="TASK_ATTEMPT_CREATED",
                    attempt_id=attempt_id,
                ),
                attempt_leased_outbox=self._runtime_outbox.build(
                    command_id=admitted.command_id,
                    aggregate_type="TASK_ATTEMPT",
                    aggregate_id=attempt_id,
                    aggregate_version=1,
                    event_type="TASK_ATTEMPT_LEASED",
                    attempt_id=attempt_id,
                ),
                retry_ready_outbox=(self._runtime_outbox.build(
                    command_id=admitted.command_id, aggregate_type="TASK_NODE",
                    aggregate_id=f"{admitted.graph_id}@{admitted.graph_revision}/{admitted.node_id}",
                    aggregate_version=_version(node) + 1, event_type="TASK_NODE_READY",
                ) if retry_due_claim else None),
                review_continuation_authority=review_continuation_authority,
            ))
            claimed = _mapping(claimed_raw, field_name="atomic READY claim")
            attempt = _mapping(claimed.get("attempt"), field_name="atomic READY claim attempt")
            lease = _mapping(claimed.get("lease"), field_name="atomic READY claim lease")
            node = _mapping(claimed.get("node"), field_name="atomic READY claim node")
        lease_id = _required_record_text(lease, "lease_id")
        fencing_token = _record_positive_int(lease, "fencing_token")
        if _status(node) == "LEASED":
            node = await self._node_lifecycle.transition_node(admitted, node, "RUNNING")
        elif _status(node) != "RUNNING":
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "claimed TaskNode is not LEASED or RUNNING",
            )
        attempt = await self._node_lifecycle.transition_attempt(
            admitted,
            attempt,
            lease_id=lease_id,
            fencing_token=fencing_token,
            target_status="RUNNING",
        )
        try:
            precharge = await self._budgets.precharge(
                budget,
                node_id=admitted.node_id,
                attempt_id=attempt_id,
                tool_call_count=_precharged_tool_call_count(admitted.worker_route),
            )
        except GovernedRuntimeError:
            refreshed_budget = await self._budgets.refresh(budget)
            if not defer_shared_terminal or refreshed_budget.status != "EXCEEDED":
                raise
            failure_ref = await self._node_lifecycle.mark_pre_reservation_failure(
                admitted,
                graph=graph,
                node=node,
                attempt=attempt,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                reason_code=GOVERNED_BUDGET_EXCEEDED,
                defer_graph_terminal=True,
            )
            receipt = build_execution_receipt(
                admitted,
                attempt_id=attempt_id,
                status="BUDGET_BLOCKED",
                verification_status="NOT_RUN",
                accepted=False,
                reason_code=GOVERNED_BUDGET_EXCEEDED,
                evidence_refs=(failure_ref,),
                projection={"budget": refreshed_budget.execution_payload()},
            )
            yield terminal_event(receipt)
            return
        budget = precharge.context
        if not precharge.accepted or (
            preoperation_candidate is not None and budget.status == "EXCEEDED"
        ):
            receipt = await self._node_lifecycle.reject_budget_charge(
                admitted,
                precharge,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                pre_reservation_graph=graph,
                pre_reservation_node=node,
                pre_reservation_attempt=attempt,
                defer_shared_terminal=defer_shared_terminal,
            )
            yield terminal_event(receipt)
            return
        time_guard = await self._budgets.guard_execution_time(
            budget,
            node_id=admitted.node_id,
            attempt_id=attempt_id,
        )
        if time_guard is not None:
            budget = time_guard.context
            receipt = await self._node_lifecycle.reject_budget_charge(
                admitted,
                time_guard,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                pre_reservation_graph=graph,
                pre_reservation_node=node,
                pre_reservation_attempt=attempt,
                defer_shared_terminal=defer_shared_terminal,
            )
            yield terminal_event(receipt)
            return
        worker_time_budget_ms = budget.remaining_execution_time_ms()

        # This is the only automatic retry boundary.  Worker resolution is a
        # side-effect-free preparation step and no operation reservation exists
        # yet.  After reserve_operation() succeeds, every exception follows the
        # terminal/reconciliation path and is never passed to schedule_retry().
        try:
            worker = self._workers.resolve(
                executor=admitted.executor,
                worker_route=admitted.worker_route,
            )
        except RetryableWorkerPreparationError as exc:
            reason_code = _exception_reason(exc, GOVERNED_WORKER_FAILED)
            if not _retry_attempt_available(node, attempt_no=attempt_no):
                failure_ref = await self._node_lifecycle.mark_pre_reservation_failure(
                    admitted,
                    graph=graph,
                    node=node,
                    attempt=attempt,
                    attempt_id=attempt_id,
                    fencing_token=fencing_token,
                    reason_code=reason_code,
                    defer_graph_terminal=defer_shared_terminal, budget=budget,
                )
                budget = await self._node_lifecycle.finalize_node_budget(
                    budget,
                    target_status=BudgetReservationStatus.RELEASED,
                    defer_shared_terminal=defer_shared_terminal,
                )
                await self._runtime_outbox.publish_pending()
                receipt = build_execution_receipt(
                    admitted,
                    attempt_id=attempt_id,
                    status="FAILED",
                    verification_status="NOT_RUN",
                    accepted=False,
                    reason_code=reason_code,
                    evidence_refs=(failure_ref,),
                    projection={"budget": budget.execution_payload()},
                )
                yield terminal_event(receipt)
                return

            error_ref = "taskerror://" + hashlib.sha256(
                f"{attempt_id}\n{reason_code}".encode("utf-8")
            ).hexdigest()
            expected_retry_usage_id = _retry_usage_id(
                budget_id=budget.budget_id,
                attempt_id=attempt_id,
                node_only=defer_shared_terminal,
            )
            retry_arguments = {
                "budget_id": budget.budget_id,
                "owner_id": self._owner_id,
                "fencing_token": fencing_token,
                "expected_attempt_version": _version(attempt),
                "error_ref": error_ref,
                "outbox": self._runtime_outbox.build(
                    command_id=admitted.command_id,
                    aggregate_type="TASK_ATTEMPT",
                    aggregate_id=attempt_id,
                    aggregate_version=_version(attempt) + 1,
                    event_type="TASK_ATTEMPT_FAILED",
                    attempt_id=attempt_id,
                    evidence_refs=(error_ref, expected_retry_usage_id),
                ),
            }
            if defer_shared_terminal:
                retry_arguments["expected_graph_version"] = _version(graph)
            schedule_retry = (
                self._store.schedule_retry_node_outcome
                if defer_shared_terminal
                else self._store.schedule_retry
            )
            try:
                retry_raw = await _maybe_await(
                    schedule_retry(attempt_id, **retry_arguments)
                )
            except Exception as first_exc:
                # The first call may have committed before its response was
                # lost.  schedule_retry() therefore provides exact replay for
                # the same deterministic retry decision.  One immediate
                # replay also covers a transient failure before BEGIN/COMMIT.
                try:
                    retry_raw = await _maybe_await(
                        schedule_retry(attempt_id, **retry_arguments)
                    )
                except Exception as replay_exc:
                    current_attempt = await _maybe_await(
                        self._store.get_attempt(attempt_id)
                    )
                    if _status(current_attempt) != "RUNNING":
                        raise GovernedRuntimeError(
                            GOVERNED_DURABLE_STATE_CONFLICT,
                            "durable retry decision could not be exactly replayed",
                        ) from replay_exc
                    failure_ref = await self._node_lifecycle.mark_pre_reservation_failure(
                        admitted,
                        graph=graph,
                        node=node,
                        attempt=current_attempt,
                        attempt_id=attempt_id,
                        fencing_token=fencing_token,
                        reason_code=GOVERNED_DURABLE_STATE_CONFLICT,
                        evidence_refs=(error_ref,),
                        defer_graph_terminal=defer_shared_terminal, budget=budget,
                    )
                    budget = await self._node_lifecycle.finalize_node_budget(
                        budget,
                        target_status=BudgetReservationStatus.RELEASED,
                        defer_shared_terminal=defer_shared_terminal,
                    )
                    await self._runtime_outbox.publish_pending()
                    receipt = build_execution_receipt(
                        admitted,
                        attempt_id=attempt_id,
                        status="FAILED",
                        verification_status="NOT_RUN",
                        accepted=False,
                        reason_code=GOVERNED_DURABLE_STATE_CONFLICT,
                        evidence_refs=(error_ref, failure_ref),
                        projection={
                            "budget": budget.execution_payload(),
                            "retry_schedule_error": _exception_reason(
                                first_exc,
                                GOVERNED_DURABLE_STATE_CONFLICT,
                            ),
                        },
                    )
                    yield terminal_event(receipt)
                    return
            retry = _mapping(retry_raw, field_name="atomic retry schedule")
            retry_budget = _mapping(
                retry.get("budget"),
                field_name="atomic retry schedule budget",
            )
            budget = budget.refreshed(retry_budget)
            retry_accepted = retry.get("accepted")
            if not isinstance(retry_accepted, bool):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "atomic retry schedule has no boolean accepted outcome",
                )
            retry_usage_id = _required_record_text(retry, "retry_usage_id")
            if retry_usage_id != expected_retry_usage_id:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "atomic retry schedule returned a different BudgetUsage identity",
                )
            retry_evidence = (error_ref, retry_usage_id)
            if retry_accepted:
                retry_node = _mapping(
                    retry.get("node"),
                    field_name="atomic retry schedule node",
                )
                retry_not_before_ms = _record_non_negative_int(
                    retry,
                    "retry_not_before_ms",
                )
                if _status(retry_node) != "RETRY_PENDING" or budget.status != "ACTIVE":
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "accepted retry did not persist RETRY_PENDING with ACTIVE budget",
                    )
                await self._runtime_outbox.publish_pending()
                receipt = build_execution_receipt(
                    admitted,
                    attempt_id=attempt_id,
                    status="RETRY_PENDING",
                    verification_status="NOT_RUN",
                    accepted=False,
                    reason_code=GOVERNED_EXECUTION_IN_PROGRESS,
                    evidence_refs=retry_evidence,
                    projection={
                        "retry_not_before_ms": retry_not_before_ms,
                        "retry_usage_id": retry_usage_id,
                        "budget": budget.execution_payload(),
                    },
                )
                yield terminal_event(receipt)
                return

            retry_graph = _mapping(
                retry.get("graph"),
                field_name="atomic retry schedule graph",
            )
            retry_node = _mapping(
                retry.get("node"),
                field_name="atomic retry schedule node",
            )
            if (
                budget.status != "EXCEEDED"
                or _status(retry_graph)
                != ("ACTIVE" if defer_shared_terminal else "BLOCKED")
                or _status(retry_node) != "FAILED"
            ):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "retry budget rejection did not fail closed",
                )
            budget = await self._node_lifecycle.finalize_node_budget(
                budget,
                target_status=BudgetReservationStatus.RELEASED,
                defer_shared_terminal=defer_shared_terminal,
            )
            await self._runtime_outbox.publish_pending()
            receipt = build_execution_receipt(
                admitted,
                attempt_id=attempt_id,
                status="BUDGET_BLOCKED",
                verification_status="NOT_RUN",
                accepted=False,
                reason_code=GOVERNED_BUDGET_EXCEEDED,
                evidence_refs=retry_evidence,
                projection={
                    "retry_usage_id": retry_usage_id,
                    "exceeded_dimensions": list(
                        retry.get("exceeded_dimensions") or ()
                    ),
                    "budget": budget.execution_payload(),
                },
            )
            yield terminal_event(receipt)
            return
        except Exception as exc:
            reason_code = _exception_reason(exc, GOVERNED_WORKER_FAILED)
            failure_ref = await self._node_lifecycle.mark_pre_reservation_failure(
                admitted,
                graph=graph,
                node=node,
                attempt=attempt,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                reason_code=reason_code,
                defer_graph_terminal=defer_shared_terminal, budget=budget,
            )
            budget = await self._node_lifecycle.finalize_node_budget(
                budget,
                target_status=BudgetReservationStatus.RELEASED,
                defer_shared_terminal=defer_shared_terminal,
            )
            await self._runtime_outbox.publish_pending()
            receipt = build_execution_receipt(
                admitted,
                attempt_id=attempt_id,
                status="FAILED",
                verification_status="NOT_RUN",
                accepted=False,
                reason_code=reason_code,
                evidence_refs=(failure_ref,),
                projection={"budget": budget.execution_payload()},
            )
            yield terminal_event(receipt)
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
                outbox=self._runtime_outbox.build(
                    command_id=admitted.command_id,
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
            # Another owner won the exact Operation reservation after our
            # initial read.  From this point the external effect is ambiguous;
            # ordinary Attempt/Node/Graph failure and Budget release are not
            # authorized.  The existing reservation must be reconciled.
            await self._runtime_outbox.publish_pending(best_effort=True)
            receipt = build_execution_receipt(
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
            yield terminal_event(receipt)
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
            "node_input_refs": list(node_input_refs),
            "node_input_artifacts": input_artifacts,
            "node_acceptance_criteria": list(admitted.active_node.acceptance_criteria),
            "node_risk_level": int(admitted.active_node.risk_level),
        }
        if node_grant_ref is not None:
            execution_context["node_grant_ref"] = node_grant_ref

        def heartbeat_outbox(**values: Any) -> Any:
            values.pop("attempt_id", None)
            return self._runtime_outbox.build(
                command_id=admitted.command_id,
                attempt_id=attempt_id,
                **values,
            )

        heartbeat = DurableExecutionHeartbeat(
            store=self._store,
            outbox_builder=heartbeat_outbox,
            attempt_id=attempt_id,
            lease_id=lease_id,
            owner_id=self._owner_id,
            fencing_token=fencing_token,
            lease_version=_version(lease),
            lease_ttl_ms=self._lease_ttl_ms,
            operation_ttl_ms=self._operation_ttl_ms,
        )
        try:
            await heartbeat.start()
            await heartbeat.attach_reservation(
                reservation_id=admitted.reservation_id,
                reservation_version=_version(reservation),
            )
        except asyncio.CancelledError:
            await stop_heartbeat_after_interruption(heartbeat)
            raise
        except Exception as exc:
            await stop_heartbeat_after_interruption(heartbeat)
            await self._runtime_outbox.publish_pending(best_effort=True)
            receipt = build_execution_receipt(
                admitted,
                attempt_id=attempt_id,
                status="RECOVERY_REQUIRED",
                verification_status="NOT_RUN",
                accepted=False,
                reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
                projection={
                    "heartbeat_phase": _heartbeat_failure_phase(exc),
                    "budget": budget.execution_payload(),
                },
            )
            yield terminal_event(receipt)
            return

        worker_commit: dict[str, Any] | None = None
        worker_started_ns = time.monotonic_ns()
        worker_error: BaseException | None = None
        heartbeat_error: BaseException | None = None
        try:
            commit_count = 0
            async for raw_event in bounded_worker_event_stream(
                worker,
                command=command,
                work_order=work_order,
                execution_context=execution_context,
                time_budget_ms=worker_time_budget_ms,
            ):
                await heartbeat.checkpoint()
                event = _mapping(raw_event, field_name="worker event")
                if _is_terminal_commit(event):
                    commit_count += 1
                    worker_commit = event
                    continue
                yield event
            await heartbeat.checkpoint()
            if commit_count != 1 or worker_commit is None:
                raise GovernedRuntimeError(
                    GOVERNED_WORKER_COMMIT_INVALID,
                    f"worker must produce exactly one final COMMIT, got {commit_count}",
                )
        except BaseException as exc:
            worker_error = exc
        finally:
            try:
                await heartbeat.stop()
            except BaseException as exc:
                heartbeat_error = exc

        if isinstance(worker_error, asyncio.CancelledError):
            executing_task = asyncio.current_task()
            if executing_task is not None and executing_task.cancelling() == 0:
                # A worker that raises CancelledError itself does not own the
                # caller/task lifecycle.  Treat it as a worker interruption so
                # it follows the durable post-reservation recovery path.  A
                # real task cancellation (disconnect, shutdown, or an already
                # persisted graph cancellation) keeps propagating unchanged.
                worker_error = GovernedRuntimeError(
                    GOVERNED_WORKER_SELF_CANCELLED,
                    "worker cancelled itself without a caller cancellation request",
                )
            else:
                # Classification is synchronous and best effort so the
                # caller's cancellation remains the lifecycle authority.
                classify_caller_cancellation(
                    store=self._store,
                    admitted=admitted,
                    heartbeat=heartbeat,
                    attempt_id=attempt_id,
                    lease_id=lease_id,
                    owner_id=self._owner_id,
                    fencing_token=fencing_token,
                    reason_code=GOVERNED_CALLER_CANCELLED,
                    request_id=command.request_id,
                )
                raise worker_error
        if worker_error is not None and not isinstance(worker_error, Exception):
            raise worker_error
        if heartbeat_error is not None and not isinstance(heartbeat_error, Exception):
            raise heartbeat_error
        if heartbeat_error is not None:
            heartbeat_interruption = classify_interrupted_operation(
                store=self._store,
                admitted=admitted,
                heartbeat=heartbeat,
                attempt_id=attempt_id,
                lease_id=lease_id,
                owner_id=self._owner_id,
                fencing_token=fencing_token,
                reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
            )
            _logger.error(
                "Governed execution lost its durable heartbeat before commit",
                extra={
                    "request_id": command.request_id,
                    "graph_id": admitted.graph_id,
                    "graph_revision": admitted.graph_revision,
                    "node_id": admitted.node_id,
                    "attempt_id": attempt_id,
                    "worker_route": admitted.worker_route,
                    "heartbeat_phase": _heartbeat_failure_phase(heartbeat_error),
                    "exception_type": type(heartbeat_error).__name__,
                    "recovery_marker_reason_code": (
                        heartbeat_interruption.marker_failure_reason_code
                    ),
                },
                exc_info=(
                    type(heartbeat_error),
                    heartbeat_error,
                    heartbeat_error.__traceback__,
                ),
            )
            await self._runtime_outbox.publish_pending(best_effort=True)
            heartbeat_projection = {
                "heartbeat_phase": _heartbeat_failure_phase(heartbeat_error),
                **heartbeat_interruption.projection(),
                "budget": budget.execution_payload(),
            }
            receipt = build_execution_receipt(
                admitted,
                attempt_id=attempt_id,
                status="RECOVERY_REQUIRED",
                verification_status="NOT_RUN",
                accepted=False,
                reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
                projection=heartbeat_projection,
            )
            yield terminal_event(receipt)
            return
        if worker_error is not None:
            receipt = await build_worker_interruption_receipt(
                store=self._store,
                budgets=self._budgets,
                runtime_outbox=self._runtime_outbox,
                admitted=admitted,
                heartbeat=heartbeat,
                command=command,
                budget=budget,
                attempt_id=attempt_id,
                lease_id=lease_id,
                owner_id=self._owner_id,
                fencing_token=fencing_token,
                worker_error=worker_error,
                worker_started_ns=worker_started_ns,
                worker_time_budget_ms=worker_time_budget_ms,
                operation_recovery_reason_code=(
                    GOVERNED_OPERATION_RECOVERY_REQUIRED
                ),
                worker_failure_default_reason_code=GOVERNED_WORKER_FAILED,
                time_budget_exceeded_reason_code=GOVERNED_TIME_BUDGET_EXCEEDED,
                budget_conflict_reason_code=GOVERNED_BUDGET_STATE_CONFLICT,
            )
            yield terminal_event(receipt)
            return

        reservation_version = heartbeat.latest_reservation_version
        if reservation_version is None:
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                "operation reservation was detached from its execution heartbeat",
            )
        transient_payload_digest = _payload_digest(worker_commit)
        receipt_ref = _worker_receipt_ref(worker_commit) or (
            "taskreceipt://"
            f"{_ref_token(admitted.graph_id)}/{admitted.graph_revision}/"
            f"{_ref_token(admitted.node_id)}/{_ref_token(attempt_id)}/"
            f"{transient_payload_digest.split(':', 1)[1]}"
        )
        payload_digest = (
            _digest_from_receipt_ref(receipt_ref) or transient_payload_digest
        )
        digest_ref = f"sha256://execution/{payload_digest.split(':', 1)[1]}"
        evidence_refs = (receipt_ref, digest_ref)
        try:
            reservation = await self._operation_recovery.commit_or_reconcile(
                commit=lambda: self._store.commit_operation(
                    admitted.reservation_id,
                    attempt_id=attempt_id,
                    lease_id=lease_id,
                    owner_id=self._owner_id,
                    fencing_token=fencing_token,
                    expected_version=reservation_version,
                    receipt_ref=receipt_ref,
                    outbox=self._runtime_outbox.build(
                        command_id=admitted.command_id,
                        aggregate_type="OPERATION_RESERVATION",
                        aggregate_id=admitted.reservation_id,
                        aggregate_version=reservation_version + 1,
                        event_type="OPERATION_COMMITTED",
                        attempt_id=attempt_id,
                        evidence_refs=evidence_refs,
                    ),
                ),
                reservation_id=admitted.reservation_id,
                business_scope=admitted.business_scope,
                operation_fingerprint=admitted.operation_fingerprint,
                attempt_id=attempt_id,
                lease_id=lease_id,
                owner_id=self._owner_id,
                fencing_token=fencing_token,
                expected_version=reservation_version,
                receipt_ref=receipt_ref,
                checkpoint_digest_ref=digest_ref,
            )
        except BaseException as exc:
            await self._runtime_outbox.publish_pending(best_effort=True)
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
                elapsed_ms=_worker_elapsed_ms(worker_started_ns),
            )
        except Exception:
            # Operation COMMITTED proves the worker boundary was crossed.  A
            # BudgetUsage write may also have committed before its response or
            # first read-back was lost.  Never rewrite that ambiguity as a
            # worker failure or release the Budget.  A later higher-fence
            # owner can resume the exact single-node checkpoint; multi-node
            # batches remain durably held for their separate recovery path.
            await self._runtime_outbox.publish_pending(best_effort=True)
            receipt = build_execution_receipt(
                admitted,
                attempt_id=attempt_id,
                status="RECOVERY_REQUIRED",
                verification_status="NOT_RUN",
                accepted=False,
                reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
                evidence_refs=evidence_refs,
                projection={
                    "budget_settlement_state": "UNKNOWN",
                    "operation_status": "COMMITTED",
                    "worker_replayed": False,
                    "budget": budget.execution_payload(),
                },
            )
            yield terminal_event(receipt)
            return
        budget = charge.context
        if not charge.accepted:
            internal_agent_checkpoint = is_internal_agent_checkpoint_authority(
                command,
            )
            if not defer_shared_terminal or requires_internal_terminal_bundle(admitted, command):
                receipt = await self._internal_budget_rejection.reject(
                    admitted=admitted,
                    charge=charge,
                    attempt=attempt,
                    node=node,
                    graph=graph,
                    reservation=_mapping(
                        reservation,
                        field_name="Internal Agent committed reservation",
                    ),
                    lease=lease,
                    fencing_token=fencing_token,
                    evidence_refs=evidence_refs,
                    require_internal_agent=internal_agent_checkpoint,
                )
            else:
                receipt = await self._node_lifecycle.reject_budget_charge(
                    admitted,
                    charge,
                    attempt_id=attempt_id,
                    fencing_token=fencing_token,
                    defer_shared_terminal=defer_shared_terminal,
                )
            yield terminal_event(receipt)
            return
        execution_context["budget"] = budget.execution_payload()

        attempt = await self._node_lifecycle.transition_attempt(
            admitted,
            attempt,
            lease_id=lease_id,
            fencing_token=fencing_token,
            target_status="SUCCEEDED",
            evidence_refs=evidence_refs,
        )
        node = await self._node_lifecycle.transition_node(
            admitted,
            node,
            "EXECUTED",
            evidence_refs=evidence_refs,
        )
        node = await self._node_lifecycle.transition_node(
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
            node = await self._node_lifecycle.transition_node(
                admitted,
                node,
                "VERIFICATION_FAILED",
                evidence_refs=verification_refs or evidence_refs,
                verification_result=verification,
            )
            budget = await self._node_lifecycle.finalize_node_budget(
                budget,
                target_status=BudgetReservationStatus.RELEASED,
                defer_shared_terminal=defer_shared_terminal,
            )
            if not defer_shared_terminal:
                graph = await self._node_lifecycle.transition_graph(admitted, graph, "FAILED")
            await self._runtime_outbox.publish_pending()
            receipt = build_execution_receipt(
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
            yield terminal_event(receipt)
            return

        node = await self._node_lifecycle.transition_node(
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
            node = await self._node_lifecycle.transition_node(
                admitted,
                node,
                "WAITING_ACCEPTANCE_REVIEW",
                evidence_refs=acceptance_refs or verification_refs,
            )
            if not defer_shared_terminal:
                graph = await self._node_lifecycle.transition_graph(admitted, graph, "BLOCKED")
            review_gate: TaskNodeReviewGate | None = None
            review_refs: tuple[str, ...] = ()
            if self._reviews is not None:
                review_gate = self._reviews.post_execution_acceptance_gate(
                    context=build_review_context(
                        admitted=admitted,
                        command=command,
                        work_order=work_order,
                        node=node,
                        attempt_id=attempt_id,
                        reason="Acceptance policy requires durable post-execution review",
                    )
                )
                review_refs = _review_evidence_refs(review_gate)
            await self._runtime_outbox.publish_pending()
            review_projection = dict(worker_commit)
            if review_gate is not None:
                review_projection["review"] = review_gate.to_dict()
            receipt = build_execution_receipt(
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
            yield terminal_event(receipt)
            return

        node = await self._node_lifecycle.accept_node_output(
            admitted,
            node,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
            worker_output=worker_commit,
            evidence_refs=acceptance_refs,
            acceptance_judgement=acceptance,
        )
        all_nodes_accepted = not defer_shared_terminal
        if all_nodes_accepted:
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
                try:
                    graph = await self._graph_finalizer.complete(
                        admitted=admitted,
                        command=command,
                        work_order=work_order,
                        graph=graph,
                        node_grant_ref=node_grant_ref,
                        worker_commit=worker_commit,
                        attempt_id=attempt_id,
                    )
                except Exception as exc:
                    # Legacy Graphs may still exist in durable storage.  Their
                    # historical closeout consumed Budget before staging Graph
                    # effects.  A finalizer error must not convert that
                    # recoverable ACTIVE/CONSUMED checkpoint into the corrupt
                    # FAILED/CONSUMED terminal matrix.  New production Graphs
                    # use the 0091 whole-Graph transaction; legacy re-entry
                    # remains held until the exact finalizer can succeed.
                    await self._runtime_outbox.publish_pending(best_effort=True)
                    receipt = build_execution_receipt(
                        admitted,
                        attempt_id=attempt_id,
                        receipt_ref=receipt_ref,
                        payload_digest=payload_digest,
                        status="RECOVERY_REQUIRED",
                        verification_status="PASS",
                        accepted=False,
                        reason_code=_exception_reason(
                            exc,
                            "ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID",
                        ),
                        evidence_refs=acceptance_refs,
                        projection=worker_commit,
                    )
                    yield terminal_event(receipt)
                    return
        await self._runtime_outbox.publish_pending()
        receipt = build_execution_receipt(
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
        yield approved_commit_event(worker_commit, receipt)
__all__ = ["DurableNodeExecutionRuntime"]
