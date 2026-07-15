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
import hashlib
import inspect
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from natsume_contracts.task.v1 import HostCommand
from host_brain.contracts.agent_runtime.budget_policy import BudgetReservationStatus
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphCompletionPolicy,
    TaskNodeStatus,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
)

from .admission import AdmittedExecution, AdmittedTaskNode
from .coordinator_records import (
    _ParallelNodeOutcome,
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
    approved_commit_event,
    build_execution_receipt,
    terminal_event,
)
from .graph_cancellation_records import (
    _cancellation_budget_projection,
    _cancellation_budget_target,
    _cancellation_final_node_states,
    _cancellation_node_states,
    _cancellation_requested_node_states,
    _validated_graph_cancellation_intent,
)
from .local_graph_execution_registry import LocalGraphExecutionRegistry
from .runtime_outbox import RuntimeOutbox
from .budget_runtime import BudgetCharge, BudgetRuntimeContext, DurableBudgetRuntime
from .errors import (
    GOVERNED_ACCEPTANCE_FAILED,
    GOVERNED_ADMISSION_INVALID,
    GOVERNED_ASSIGNMENT_CONFLICT,
    GOVERNED_BUDGET_EXCEEDED,
    GOVERNED_BUDGET_STATE_CONFLICT,
    GOVERNED_CANCELLATION_AUTHORITY_INVALID,
    GOVERNED_GRAPH_CANCELLATION_PENDING,
    GOVERNED_GRAPH_CANCELLED,
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
    GOVERNED_WORKER_SELF_CANCELLED,
    GovernedRuntimeError,
    RetryableWorkerPreparationError,
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
from .heartbeat import (
    DurableExecutionHeartbeat,
    DurableExecutionHeartbeatError,
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
from host_brain.contracts.agent_runtime.parallel_settlement import (
    ParallelSettlementContractError,
    normalize_parallel_settlement_decision,
)
from host_brain.contracts.agent_runtime.parallel_success import (
    ParallelSuccessContractError,
    normalize_parallel_success_decision,
)
from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
    GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
    GraphCancellationContractError,
    normalize_graph_cancellation_decision,
    normalize_graph_cancellation_intent,
)


_logger = logging.getLogger("Natsume.task_runtime.coordinator")
_TASK_STORE_CAS_CONFLICT = "TASK_STORE_CAS_CONFLICT"
_TASK_STORE_OWNERSHIP_CONFLICT = "TASK_STORE_OWNERSHIP_CONFLICT"






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
        self._budgets = DurableBudgetRuntime(task_store=task_store, outbox_factory=outbox_factory)

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
        """Install durable cancellation authority before touching local tasks."""

        admitted.validate()
        if not isinstance(command, HostCommand) or command.command_id != admitted.command_id:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "cancellation command differs from durable admission",
            )
        await _maybe_await(self._store.assert_graph_cancellation_ready())
        existing = await _maybe_await(
            self._store.get_graph_cancellation_for_graph(
                admitted.graph_id,
                admitted.graph_revision,
            )
        )
        cancellation: Mapping[str, Any]
        cancellation_replayed = False
        if existing is None:
            graph = _mapping(
                await _maybe_await(
                    self._store.get_graph(admitted.graph_id, admitted.graph_revision)
                ),
                field_name="cancellation Graph",
            )
            nodes = await self._cancellation_nodes(admitted, graph=graph)
            graph_status = _status(graph)
            if graph_status not in {"CREATED", "ACTIVE", "BLOCKED"}:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"TaskGraph cannot be cancelled from {graph_status}",
                )
            budget_record = await self._cancellation_budget_record(admitted)
            node_states_before = _cancellation_node_states(nodes)
            node_states_after = _cancellation_requested_node_states(node_states_before)
            graph_version = _version(graph)
            graph_outbox_seed = self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="TASK_GRAPH",
                aggregate_id=admitted.graph_id,
                aggregate_version=graph_version + 1,
                event_type="TASK_GRAPH_CANCELLATION_REQUESTED",
            )
            try:
                intent = normalize_graph_cancellation_intent(
                    {
                        "schema_version": GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
                        "request_id": str(request_id or "").strip(),
                        "request_identity": str(request_identity or "").strip(),
                        "source": str(source or "").strip().upper(),
                        "reason_code": str(reason_code or "").strip().upper(),
                        "graph_id": admitted.graph_id,
                        "graph_revision": admitted.graph_revision,
                        "graph_status_before": graph_status,
                        "graph_status_version_before": graph_version,
                        "graph_target_status": "BLOCKED",
                        "budget_id": (
                            _required_record_text(budget_record, "budget_id")
                            if budget_record is not None
                            else None
                        ),
                        "budget_status_before": (
                            _status(budget_record) if budget_record is not None else None
                        ),
                        "budget_status_version_before": (
                            _version(budget_record) if budget_record is not None else None
                        ),
                        "node_states_before": node_states_before,
                        "node_states_after": node_states_after,
                        "graph_outbox_id": graph_outbox_seed.outbox_id,
                    }
                )
            except GraphCancellationContractError as exc:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"graph cancellation intent is invalid: {exc}",
                ) from exc
            graph_outbox = self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="TASK_GRAPH",
                aggregate_id=admitted.graph_id,
                aggregate_version=graph_version + 1,
                event_type="TASK_GRAPH_CANCELLATION_REQUESTED",
                evidence_refs=(intent.intent_ref, intent.payload["request_identity"]),
            )
            arguments = {
                "budget_id": intent.payload["budget_id"],
                "expected_graph_version": graph_version,
                "expected_budget_version": intent.payload[
                    "budget_status_version_before"
                ],
                "expected_node_states": node_states_before,
                "intent_ref": intent.intent_ref,
                "intent_payload": intent.payload,
                "graph_outbox": graph_outbox,
            }
            try:
                requested_raw = await _maybe_await(
                    self._store.request_graph_cancellation(
                        admitted.graph_id,
                        admitted.graph_revision,
                        **arguments,
                    )
                )
            except Exception as first_exc:
                try:
                    requested_raw = await _maybe_await(
                        self._store.request_graph_cancellation(
                            admitted.graph_id,
                            admitted.graph_revision,
                            **arguments,
                        )
                    )
                except Exception as replay_exc:
                    durable = await _maybe_await(
                        self._store.get_graph_cancellation_for_graph(
                            admitted.graph_id,
                            admitted.graph_revision,
                        )
                    )
                    if not isinstance(durable, Mapping) or _text(
                        durable.get("intent_ref")
                    ) != intent.intent_ref:
                        raise GovernedRuntimeError(
                            GOVERNED_DURABLE_STATE_CONFLICT,
                            "graph cancellation intent could not be committed or exactly replayed",
                        ) from replay_exc
                    cancellation = dict(durable)
                    cancellation_replayed = True
                else:
                    _logger.warning(
                        "Replayed committed graph cancellation intent",
                        extra={
                            "graph_id": admitted.graph_id,
                            "graph_revision": admitted.graph_revision,
                            "intent_ref": intent.intent_ref,
                            "first_exception_type": type(first_exc).__name__,
                        },
                    )
                    requested = _mapping(
                        requested_raw,
                        field_name="graph cancellation request",
                    )
                    cancellation = _mapping(
                        requested.get("intent"),
                        field_name="graph cancellation intent",
                    )
                    cancellation_replayed = requested.get("replayed") is True
            else:
                requested = _mapping(
                    requested_raw,
                    field_name="graph cancellation request",
                )
                cancellation = _mapping(
                    requested.get("intent"),
                    field_name="graph cancellation intent",
                )
                cancellation_replayed = requested.get("replayed") is True
        else:
            cancellation = _mapping(
                existing,
                field_name="existing graph cancellation intent",
            )
            cancellation_replayed = True

        intent = _validated_graph_cancellation_intent(admitted, cancellation)
        requested_binding = {
            "request_id": str(request_id or "").strip(),
            "request_identity": str(request_identity or "").strip(),
            "source": str(source or "").strip().upper(),
            "reason_code": str(reason_code or "").strip().upper(),
        }
        durable_binding = {
            key: intent.payload[key]
            for key in (
                "request_id",
                "request_identity",
                "source",
                "reason_code",
            )
        }
        if durable_binding != requested_binding:
            raise GovernedRuntimeError(
                GOVERNED_CANCELLATION_AUTHORITY_INVALID,
                "graph cancellation replay authority differs from its durable intent",
            )
        local_pending: tuple[str, ...] = ()
        try:
            await self._runtime_outbox.publish_pending()
        finally:
            # Once the durable intent exists, Outbox transport health must not
            # keep local workers running behind an already-installed fence.
            # The publish exception still propagates after every local task
            # has received cancellation and the bounded join has completed.
            local_pending = await self._execution_registry.cancel_and_join(
                graph_id=admitted.graph_id,
                graph_revision=admitted.graph_revision,
                intent_ref=intent.intent_ref,
                timeout_seconds=self._lease_ttl_ms / 1000.0,
            )
        return await self._continue_graph_cancellation(
            admitted=admitted,
            command=command,
            work_order=work_order,
            cancellation=cancellation,
            local_pending=local_pending,
            replayed=cancellation_replayed,
        )


    async def _cancellation_nodes(
        self,
        admitted: AdmittedExecution,
        *,
        graph: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        nodes: dict[str, dict[str, Any]] = {}
        for binding in admitted.nodes:
            node_admission = admitted.for_node(binding.node_id)
            node = _mapping(
                await _maybe_await(
                    self._store.get_node(
                        admitted.graph_id,
                        admitted.graph_revision,
                        binding.node_id,
                    )
                ),
                field_name=f"cancellation TaskNode {binding.node_id}",
            )
            _assert_durable_identity(node_admission, graph=graph, node=node)
            nodes[binding.node_id] = node
        return nodes

    async def _cancellation_budget_record(
        self,
        admitted: AdmittedExecution,
    ) -> dict[str, Any] | None:
        snapshot = _mapping(
            await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            ),
            field_name="cancellation scheduler snapshot",
        )
        raw_budgets = snapshot.get("budgets")
        if not isinstance(raw_budgets, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "cancellation scheduler snapshot has no Budget list",
            )
        budgets = [dict(item) for item in raw_budgets if isinstance(item, Mapping)]
        if len(budgets) > 1:
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "TaskGraph cancellation found multiple shared Budgets",
            )
        if not budgets:
            return None
        budget = budgets[0]
        if (
            _text(budget.get("graph_id")) != admitted.graph_id
            or int(budget.get("graph_revision") or 0) != admitted.graph_revision
            or budget.get("node_id") is not None
            or budget.get("attempt_id") is not None
        ):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "cancellation Budget is not bound to the admitted Graph revision",
            )
        return budget

    async def _continue_graph_cancellation(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        cancellation: Mapping[str, Any],
        local_pending: Sequence[str] = (),
        replayed: bool = False,
    ) -> dict[str, Any]:
        del command, work_order
        intent = _validated_graph_cancellation_intent(admitted, cancellation)
        intent_record = dict(cancellation)
        reconciliation_evidence_refs: tuple[str, ...] = ()
        if _status(intent_record) == "FINALIZED":
            decision_record = await self._cancellation_decision_for_intent(
                admitted,
                intent_ref=intent.intent_ref,
            )
            return await self._graph_cancellation_terminal_event(
                admitted=admitted,
                intent_record=intent_record,
                decision_record=decision_record,
                local_pending=local_pending,
                replayed=True,
            )

        if not local_pending:
            try:
                advanced_raw = await _maybe_await(
                    self._store.advance_graph_cancellation(intent.intent_ref)
                )
            except Exception as exc:
                if _exception_reason(exc, "") not in {
                    _TASK_STORE_OWNERSHIP_CONFLICT,
                    "TASK_STORE_CANCELLATION_CONFLICT",
                }:
                    raise
                return await self._graph_cancellation_terminal_event(
                    admitted=admitted,
                    intent_record=intent_record,
                    decision_record=None,
                    replayed=replayed,
                )
            else:
                if isinstance(advanced_raw, Mapping):
                    advanced_intent = advanced_raw.get("intent")
                    if isinstance(advanced_intent, Mapping):
                        intent_record = dict(advanced_intent)
                        intent = _validated_graph_cancellation_intent(
                            admitted,
                            intent_record,
                        )
                    raw_evidence = advanced_raw.get("reconciliation_evidence_refs")
                    if isinstance(raw_evidence, (list, tuple)):
                        reconciliation_evidence_refs = tuple(
                            dict.fromkeys(
                                _text(item) for item in raw_evidence if _text(item)
                            )
                        )
                    if advanced_raw.get("ready_to_finalize") is not True:
                        await self._runtime_outbox.publish_pending()
                        return await self._graph_cancellation_terminal_event(
                            admitted=admitted,
                            intent_record=intent_record,
                            decision_record=None,
                            replayed=replayed,
                        )
                else:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "graph cancellation continuation returned no durable result",
                    )
                await self._runtime_outbox.publish_pending()

        refreshed = await _maybe_await(
            self._store.get_graph_cancellation_for_graph(
                admitted.graph_id,
                admitted.graph_revision,
            )
        )
        if not isinstance(refreshed, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable cancellation intent disappeared during continuation",
            )
        intent_record = dict(refreshed)
        intent = _validated_graph_cancellation_intent(admitted, intent_record)
        if _status(intent_record) == "FINALIZED":
            decision_record = await self._cancellation_decision_for_intent(
                admitted,
                intent_ref=intent.intent_ref,
            )
            return await self._graph_cancellation_terminal_event(
                admitted=admitted,
                intent_record=intent_record,
                decision_record=decision_record,
                local_pending=local_pending,
                replayed=True,
            )
        if local_pending:
            return await self._graph_cancellation_terminal_event(
                admitted=admitted,
                intent_record=intent_record,
                decision_record=None,
                local_pending=local_pending,
                replayed=replayed,
            )

        graph = _mapping(
            await _maybe_await(
                self._store.get_graph(admitted.graph_id, admitted.graph_revision)
            ),
            field_name="cancellation final Graph",
        )
        nodes = await self._cancellation_nodes(admitted, graph=graph)
        budget_record = await self._cancellation_budget_record(admitted)
        node_states_before = _cancellation_node_states(nodes)
        try:
            node_states_after = _cancellation_final_node_states(node_states_before)
        except GovernedRuntimeError:
            return await self._graph_cancellation_terminal_event(
                admitted=admitted,
                intent_record=intent_record,
                decision_record=None,
                replayed=replayed,
            )
        if _status(graph) != "BLOCKED":
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "requested graph cancellation lost its BLOCKED fence",
            )
        graph_version = _version(graph)
        budget_id = (
            _required_record_text(budget_record, "budget_id")
            if budget_record is not None
            else None
        )
        budget_status = _status(budget_record) if budget_record is not None else None
        budget_version = _version(budget_record) if budget_record is not None else None
        budget_target = _cancellation_budget_target(budget_status)
        graph_outbox_seed = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_GRAPH",
            aggregate_id=admitted.graph_id,
            aggregate_version=graph_version + 1,
            event_type="TASK_GRAPH_CANCELLED",
        )
        budget_outbox_seed = None
        if budget_id is not None and budget_status != budget_target:
            budget_outbox_seed = self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget_id,
                aggregate_version=int(budget_version) + 1,
                event_type=f"BUDGET_RESERVATION_{budget_target}",
            )
        try:
            decision = normalize_graph_cancellation_decision(
                {
                    "schema_version": GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
                    "intent_ref": intent.intent_ref,
                    "request_id": intent.payload["request_id"],
                    "request_identity": intent.payload["request_identity"],
                    "graph_id": admitted.graph_id,
                    "graph_revision": admitted.graph_revision,
                    "graph_status_before": "BLOCKED",
                    "graph_status_version_before": graph_version,
                    "graph_target_status": "CANCELLED",
                    "budget_id": budget_id,
                    "budget_status_before": budget_status,
                    "budget_status_version_before": budget_version,
                    "budget_target_status": budget_target,
                    "node_states_before": node_states_before,
                    "node_states_after": node_states_after,
                    "reconciliation_evidence_refs": list(
                        reconciliation_evidence_refs
                    ),
                    "graph_outbox_id": graph_outbox_seed.outbox_id,
                    "budget_outbox_id": (
                        budget_outbox_seed.outbox_id
                        if budget_outbox_seed is not None
                        else None
                    ),
                }
            )
        except GraphCancellationContractError as exc:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"graph cancellation decision is invalid: {exc}",
            ) from exc
        evidence_refs = tuple(
            dict.fromkeys(
                (
                    decision.decision_ref,
                    intent.intent_ref,
                    intent.payload["request_identity"],
                )
            )
        )
        graph_outbox = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_GRAPH",
            aggregate_id=admitted.graph_id,
            aggregate_version=graph_version + 1,
            event_type="TASK_GRAPH_CANCELLED",
            evidence_refs=evidence_refs,
        )
        budget_outbox = None
        if budget_outbox_seed is not None and budget_id is not None:
            budget_outbox = self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget_id,
                aggregate_version=int(budget_version) + 1,
                event_type=f"BUDGET_RESERVATION_{budget_target}",
                evidence_refs=evidence_refs,
            )
        arguments = {
            "budget_id": budget_id,
            "expected_graph_version": graph_version,
            "expected_budget_version": budget_version,
            "expected_node_states": node_states_before,
            "intent_ref": intent.intent_ref,
            "decision_ref": decision.decision_ref,
            "decision_payload": decision.payload,
            "graph_outbox": graph_outbox,
            "budget_outbox": budget_outbox,
        }
        try:
            finalized_raw = await _maybe_await(
                self._store.finalize_graph_cancellation(
                    admitted.graph_id,
                    admitted.graph_revision,
                    **arguments,
                )
            )
        except Exception as first_exc:
            reason = _exception_reason(first_exc, "")
            if reason == _TASK_STORE_OWNERSHIP_CONFLICT:
                return await self._graph_cancellation_terminal_event(
                    admitted=admitted,
                    intent_record=intent_record,
                    decision_record=None,
                    replayed=replayed,
                )
            try:
                finalized_raw = await _maybe_await(
                    self._store.finalize_graph_cancellation(
                        admitted.graph_id,
                        admitted.graph_revision,
                        **arguments,
                    )
                )
            except Exception as replay_exc:
                latest = await _maybe_await(
                    self._store.get_graph_cancellation_for_graph(
                        admitted.graph_id,
                        admitted.graph_revision,
                    )
                )
                if not isinstance(latest, Mapping) or _status(latest) != "FINALIZED":
                    if _exception_reason(replay_exc, "") == _TASK_STORE_OWNERSHIP_CONFLICT:
                        return await self._graph_cancellation_terminal_event(
                            admitted=admitted,
                            intent_record=intent_record,
                            decision_record=None,
                            replayed=replayed,
                        )
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "graph cancellation could not be finalized or exactly replayed",
                    ) from replay_exc
                final_intent = dict(latest)
                final_decision = await self._cancellation_decision_for_intent(
                    admitted,
                    intent_ref=intent.intent_ref,
                )
                await self._runtime_outbox.publish_pending()
                return await self._graph_cancellation_terminal_event(
                    admitted=admitted,
                    intent_record=final_intent,
                    decision_record=final_decision,
                    replayed=True,
                )
        finalized = _mapping(finalized_raw, field_name="final graph cancellation")
        final_intent = _mapping(
            finalized.get("intent"),
            field_name="final graph cancellation intent",
        )
        final_decision = _mapping(
            finalized.get("decision"),
            field_name="final graph cancellation decision",
        )
        _validated_graph_cancellation_intent(admitted, final_intent)
        await self._runtime_outbox.publish_pending()
        return await self._graph_cancellation_terminal_event(
            admitted=admitted,
            intent_record=final_intent,
            decision_record=final_decision,
            replayed=(replayed or finalized.get("replayed") is True),
        )

    async def _cancellation_decision_for_intent(
        self,
        admitted: AdmittedExecution,
        *,
        intent_ref: str,
    ) -> dict[str, Any]:
        raw = await _maybe_await(
            self._store.get_graph_cancellation_decision_for_intent(intent_ref)
        )
        if not isinstance(raw, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "finalized cancellation intent has no canonical decision",
            )
        record = dict(raw)
        try:
            decision = normalize_graph_cancellation_decision(
                _mapping(record.get("payload"), field_name="cancellation decision payload")
            )
        except (GraphCancellationContractError, GovernedRuntimeError) as exc:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"durable graph cancellation decision is invalid: {exc}",
            ) from exc
        if (
            _text(record.get("decision_ref")) != decision.decision_ref
            or decision.payload["intent_ref"] != intent_ref
            or decision.payload["graph_id"] != admitted.graph_id
            or decision.payload["graph_revision"] != admitted.graph_revision
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable graph cancellation decision identity drifted",
            )
        return record

    async def _graph_cancellation_terminal_event(
        self,
        *,
        admitted: AdmittedExecution,
        intent_record: Mapping[str, Any],
        decision_record: Mapping[str, Any] | None,
        local_pending: Sequence[str] = (),
        replayed: bool,
    ) -> dict[str, Any]:
        intent = _validated_graph_cancellation_intent(admitted, intent_record)
        finalized = decision_record is not None
        decision_ref = None
        payload_digest = _text(intent_record.get("payload_sha256")) or None
        if decision_record is not None:
            decision = normalize_graph_cancellation_decision(
                _mapping(
                    decision_record.get("payload"),
                    field_name="terminal cancellation decision payload",
                )
            )
            decision_ref = decision.decision_ref
            payload_digest = _text(decision_record.get("payload_sha256")) or None
        selected = admitted.for_node(
            admitted.result_node_id or admitted.nodes[-1].node_id
        )
        attempts = await self._attempts_for_node(selected)
        latest = attempts[-1] if attempts else None
        attempt_id = (
            _required_record_text(latest, "attempt_id")
            if latest is not None
            else _attempt_id(selected, attempt_no=1)
        )
        budget = await self._cancellation_budget_record(admitted)
        projection: dict[str, Any] = {
            "graph_cancellation": {
                "status": "FINALIZED" if finalized else "REQUESTED",
                "graph_status": "CANCELLED" if finalized else "BLOCKED",
                "intent_ref": intent.intent_ref,
                "decision_ref": decision_ref,
                "request_id": intent.payload["request_id"],
                "request_identity": intent.payload["request_identity"],
                "source": intent.payload["source"],
                "reason_code": intent.payload["reason_code"],
                "local_pending_tasks": list(local_pending),
            },
            "budget": _cancellation_budget_projection(budget),
        }
        evidence_refs = tuple(
            item
            for item in (
                intent.intent_ref,
                decision_ref,
                intent.payload["request_identity"],
            )
            if item
        )
        receipt = build_execution_receipt(
            selected,
            attempt_id=attempt_id,
            receipt_ref=decision_ref or intent.intent_ref,
            payload_digest=payload_digest,
            status="CANCELLED" if finalized else "CANCEL_REQUESTED",
            verification_status="NOT_RUN",
            accepted=False,
            replayed=bool(replayed),
            reason_code=(
                GOVERNED_GRAPH_CANCELLED
                if finalized
                else GOVERNED_GRAPH_CANCELLATION_PENDING
            ),
            evidence_refs=evidence_refs,
            projection=projection,
        )
        return terminal_event(receipt)

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
        cancellation = await _maybe_await(
            self._store.get_graph_cancellation_for_graph(
                admitted.graph_id,
                admitted.graph_revision,
            )
        )
        if cancellation is not None:
            terminal_event = await self._continue_graph_cancellation(
                admitted=admitted,
                command=command,
                work_order=work_order,
                cancellation=cancellation,
                replayed=True,
            )
            yield terminal_event
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
                reservation = await self._try_operation(
                    failed_admission.reservation_id
                )
                if _status(reservation or {}) in {"RESERVED", "RECOVERY_REQUIRED"}:
                    unresolved_failure = (failed_admission, reservation or {})
                    break
            if unresolved_failure is not None:
                recovery_admission, reservation = unresolved_failure
                attempts = await self._attempts_for_node(recovery_admission)
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
                await self._assert_parallel_reentry_idle(
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
                budget, terminal_event = await self._resolve_parallel_outcomes(
                    admitted=admitted,
                    outcomes=outcomes,
                    budget=budget,
                )
                if terminal_event is None:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "parallel re-entry did not produce a terminal decision",
                    )
                yield terminal_event
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
                        graph = await self._transition_graph(
                            review_admission,
                            graph,
                            "ACTIVE",
                        )
                    await self._runtime_outbox.publish_pending()
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
                    await self._runtime_outbox.publish_pending()
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
                        graph = await self._transition_graph(
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
                decision = await self._matching_parallel_decision(
                    admitted,
                    graph=graph,
                )
                decision_ref: str | None = None
                if decision is not None:
                    selected = self._selected_parallel_decision_outcome(decision)
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
                    decision = await self._matching_parallel_decision(
                        admitted,
                        graph=graph,
                    )
                    if decision is not None:
                        payload = _mapping(
                            decision.get("payload"),
                            field_name="parallel BLOCKED replay decision",
                        )
                        selected = self._selected_parallel_decision_outcome(decision)
                        selected_admission = admitted.for_node(
                            _required_record_text(selected, "node_id")
                        )
                        if _text(payload.get("decision_kind")) != "BUDGET":
                            async for event in self._execute_node_stream(
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
                    budget, terminal_event = await self._complete_parallel_success(
                        admitted=admitted,
                        command=command,
                        work_order=work_order,
                        budget=budget,
                        node_grant_refs=grant_refs,
                    )
                    yield terminal_event
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
                terminal_node_id = failed_node_id or blocked_node_id
                decision = await self._matching_parallel_decision(
                    admitted,
                    graph=graph,
                )
                if decision is not None:
                    selected = self._selected_parallel_decision_outcome(decision)
                    terminal_node_id = _required_record_text(selected, "node_id")
                terminal_admission = admitted.for_node(terminal_node_id or admitted.node_id)
                if graph_status == "ACTIVE":
                    graph = await self._transition_graph(
                        terminal_admission,
                        graph,
                        "FAILED" if failed_node_id is not None else "BLOCKED",
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
                decision = await self._matching_parallel_decision(
                    admitted,
                    graph=graph,
                )
                if decision is not None:
                    selected = self._selected_parallel_decision_outcome(decision)
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
                graph = await self._transition_graph(
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
                    graph = await self._transition_graph(
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
                        async for event in self._execute_node_stream(
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
                    terminal_event = terminals.get(binding.node_id)
                    if terminal_event is None:
                        if stop_batch:
                            continue
                        raise GovernedRuntimeError(
                            GOVERNED_DURABLE_STATE_CONFLICT,
                            f"TaskNode {binding.node_id} lost its parallel terminal projection",
                        )
                    outcomes.append(
                        _ParallelNodeOutcome.from_terminal_event(
                            node_id=binding.node_id,
                            event=terminal_event,
                        )
                    )
                budget, parallel_terminal = await self._resolve_parallel_outcomes(
                    admitted=admitted,
                    outcomes=tuple(outcomes),
                    budget=budget,
                )
                if parallel_terminal is not None:
                    yield parallel_terminal
                    return
                budget = await self._budgets.refresh(budget)
                continue

            for binding in claimable:
                node_admission = admitted.for_node(binding.node_id)
                terminal_event: dict[str, Any] | None = None
                async for event in self._execute_node_stream(
                    admitted=node_admission,
                    command=command,
                    work_order=work_order,
                    budget=budget,
                    node_grant_ref=grant_refs.get(node_admission.node_id),
                    defer_shared_terminal=parallel_success_enabled,
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
                if parallel_success_enabled:
                    outcome = _ParallelNodeOutcome.from_terminal_event(
                        node_id=binding.node_id,
                        event=terminal_event,
                    )
                    budget, parallel_terminal = await self._resolve_parallel_outcomes(
                        admitted=admitted,
                        outcomes=(outcome,),
                        budget=budget,
                    )
                    if parallel_terminal is not None:
                        yield parallel_terminal
                        return
                    continue
                if not accepted:
                    yield terminal_event
                    return
                latest_graph = await _maybe_await(
                    self._store.get_graph(admitted.graph_id, admitted.graph_revision)
                )
                if _status(latest_graph) == "COMPLETED":
                    yield terminal_event
                    return

    async def _assert_parallel_reentry_idle(
        self,
        *,
        admitted: AdmittedExecution,
        durable_nodes: Mapping[str, Mapping[str, Any]],
    ) -> None:
        active_node_statuses = frozenset(
            {
                "LEASED",
                "RUNNING",
                "EXECUTED",
                "VERIFYING",
                "VERIFIED",
                "CANCEL_REQUESTED",
            }
        )
        active_attempt_statuses = frozenset(
            {
                "CREATED",
                "LEASED",
                "RUNNING",
                "RECOVERABLE",
                "CANCEL_REQUESTED",
            }
        )
        active_nodes = tuple(
            sorted(
                node_id
                for node_id, node in durable_nodes.items()
                if _status(node) in active_node_statuses
            )
        )
        snapshot = _mapping(
            await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            ),
            field_name="parallel re-entry scheduler snapshot",
        )
        raw_leases = snapshot.get("active_leases")
        raw_attempts = snapshot.get("attempts")
        if not isinstance(raw_leases, list) or not isinstance(raw_attempts, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel re-entry scheduler snapshot is incomplete",
            )
        active_leases = tuple(
            sorted(
                _text(lease.get("lease_id"))
                for lease in raw_leases
                if isinstance(lease, Mapping) and _text(lease.get("lease_id"))
            )
        )
        active_attempts = tuple(
            sorted(
                _text(attempt.get("attempt_id"))
                for attempt in raw_attempts
                if isinstance(attempt, Mapping)
                and _status(attempt) in active_attempt_statuses
                and _text(attempt.get("attempt_id"))
            )
        )
        if active_nodes or active_leases or active_attempts:
            raise GovernedRuntimeError(
                GOVERNED_EXECUTION_IN_PROGRESS,
                "parallel TaskGraph still has an in-progress durable batch",
            )

    async def _parallel_settlement_conflict_is_in_progress(
        self,
        *,
        admitted: AdmittedExecution,
    ) -> bool:
        """Distinguish a settlement-window claim from durable state drift."""

        try:
            durable_nodes = {
                binding.node_id: _mapping(
                    await _maybe_await(
                        self._store.get_node(
                            admitted.graph_id,
                            admitted.graph_revision,
                            binding.node_id,
                        )
                    ),
                    field_name=f"parallel settlement node {binding.node_id}",
                )
                for binding in admitted.nodes
            }
            await self._assert_parallel_reentry_idle(
                admitted=admitted,
                durable_nodes=durable_nodes,
            )
        except GovernedRuntimeError as exc:
            return exc.reason_code == GOVERNED_EXECUTION_IN_PROGRESS
        except Exception:
            return False
        return False

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
            receipt = await self._terminal_replay(
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

    async def _resolve_parallel_outcomes(
        self,
        *,
        admitted: AdmittedExecution,
        outcomes: tuple[_ParallelNodeOutcome, ...],
        budget: BudgetRuntimeContext,
    ) -> tuple[BudgetRuntimeContext, dict[str, Any] | None]:
        if not outcomes:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel batch produced no node outcomes",
            )
        await self._validate_parallel_outcome_snapshot(
            admitted=admitted,
            outcomes=outcomes,
        )
        budget = await self._budgets.refresh(budget)

        def first_status(*statuses: str) -> _ParallelNodeOutcome | None:
            expected = frozenset(statuses)
            return next((item for item in outcomes if item.status in expected), None)

        recovery = first_status("RECOVERY_REQUIRED")
        if recovery is not None:
            return budget, self._parallel_event_with_budget(
                recovery.event,
                budget=budget,
                graph_status="ACTIVE",
                decision_ref=None,
            )

        budget_blocked = first_status("BUDGET_BLOCKED")
        if budget.status == "EXCEEDED":
            budget_blocked = await self._durable_budget_blocked_outcome(
                admitted=admitted,
                outcomes=outcomes,
                budget=budget,
            )
        if budget.status == "EXCEEDED" or budget_blocked is not None:
            if budget_blocked is None:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel Budget settlement lacks its rejected durable usage",
                )
            selected = budget_blocked
            settlement_outcomes = tuple(
                selected
                if (
                    outcome.node_id == selected.node_id
                    and outcome.attempt_id == selected.attempt_id
                )
                else outcome
                for outcome in outcomes
            )
            budget, graph_status, decision_ref = await self._settle_parallel_terminal(
                admitted=admitted.for_node(selected.node_id),
                outcomes=settlement_outcomes,
                budget=budget,
                graph_target_status="BLOCKED",
                release_budget=True,
                decision_kind="BUDGET",
                selected_node_id=selected.node_id,
            )
            return budget, self._parallel_event_with_budget(
                selected.event,
                budget=budget,
                graph_status=graph_status,
                decision_ref=decision_ref,
            )

        failed = first_status("FAILED", "VERIFICATION_FAILED", "CANCELLED")
        if failed is not None:
            graph_target = (
                "BLOCKED"
                if failed.reason_code == GOVERNED_OPERATION_RECOVERY_REQUIRED
                else "FAILED"
            )
            budget, graph_status, decision_ref = await self._settle_parallel_terminal(
                admitted=admitted.for_node(failed.node_id),
                outcomes=outcomes,
                budget=budget,
                graph_target_status=graph_target,
                release_budget=True,
                decision_kind="FAILURE",
                selected_node_id=failed.node_id,
            )
            return budget, self._parallel_event_with_budget(
                failed.event,
                budget=budget,
                graph_status=graph_status,
                decision_ref=decision_ref,
            )

        review = first_status("WAITING_ACCEPTANCE_REVIEW", "WAITING_APPROVAL")
        if review is not None:
            budget, graph_status, decision_ref = await self._settle_parallel_terminal(
                admitted=admitted.for_node(review.node_id),
                outcomes=outcomes,
                budget=budget,
                graph_target_status="BLOCKED",
                release_budget=False,
                decision_kind="REVIEW",
                selected_node_id=review.node_id,
            )
            return budget, self._parallel_event_with_budget(
                review.event,
                budget=budget,
                graph_status=graph_status,
                decision_ref=decision_ref,
            )

        retry = first_status("RETRY_PENDING")
        if retry is not None:
            return budget, self._parallel_event_with_budget(
                retry.event,
                budget=budget,
                graph_status="ACTIVE",
                decision_ref=None,
            )

        if all(item.accepted and item.status == "ACCEPTED" for item in outcomes):
            return budget, None
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "parallel batch returned an unsupported node-outcome combination",
        )

    async def _durable_budget_blocked_outcome(
        self,
        *,
        admitted: AdmittedExecution,
        outcomes: tuple[_ParallelNodeOutcome, ...],
        budget: BudgetRuntimeContext,
    ) -> _ParallelNodeOutcome:
        snapshot = _mapping(
            await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            ),
            field_name="parallel Budget outcome snapshot",
        )
        raw_usages = snapshot.get("budget_usage")
        if not isinstance(raw_usages, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel Budget outcome has no durable usage ledger",
            )
        outcomes_by_lineage = {
            (outcome.node_id, outcome.attempt_id): outcome for outcome in outcomes
        }
        rejected: list[tuple[tuple[int, int, str], Mapping[str, Any], _ParallelNodeOutcome]] = []
        for raw_usage in raw_usages:
            if not isinstance(raw_usage, Mapping):
                continue
            node_id = _text(raw_usage.get("node_id"))
            attempt_id = _text(raw_usage.get("attempt_id"))
            source = outcomes_by_lineage.get((node_id, attempt_id))
            exceeded_dimensions = raw_usage.get("exceeded_dimensions")
            if (
                source is None
                or _text(raw_usage.get("budget_id")) != budget.budget_id
                or raw_usage.get("accepted") is not False
                or not isinstance(exceeded_dimensions, list)
                or not exceeded_dimensions
            ):
                continue
            rejected.append(
                (
                    (
                        int(raw_usage.get("reservation_version_after") or 0),
                        int(raw_usage.get("created_ts_ms") or 0),
                        _text(raw_usage.get("usage_id")),
                    ),
                    raw_usage,
                    source,
                )
            )
        if not rejected:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "EXCEEDED Budget has no rejected usage matching the latest Attempt lineage",
            )
        _, usage, source = min(rejected, key=lambda item: item[0])
        usage_ref = _required_record_text(usage, "usage_id")
        event = dict(source.event)
        receipt_raw = event.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        evidence_refs = tuple(
            dict.fromkeys((*_evidence_refs(receipt), usage_ref))
        )
        receipt.update(
            {
                "status": "BUDGET_BLOCKED",
                "accepted": False,
                "reason_code": GOVERNED_BUDGET_EXCEEDED,
                "evidence_refs": list(evidence_refs),
            }
        )
        event.update(
            {
                "phase": "TERMINAL",
                "done": True,
                "decision": "REJECT",
                "reason_code": GOVERNED_BUDGET_EXCEEDED,
                "execution_receipt": receipt,
            }
        )
        return _ParallelNodeOutcome(
            node_id=source.node_id,
            attempt_id=source.attempt_id,
            status="BUDGET_BLOCKED",
            accepted=False,
            reason_code=GOVERNED_BUDGET_EXCEEDED,
            event=event,
        )

    async def _validate_parallel_outcome_snapshot(
        self,
        *,
        admitted: AdmittedExecution,
        outcomes: tuple[_ParallelNodeOutcome, ...],
    ) -> None:
        expected_durable_statuses = {
            "ACCEPTED": frozenset({"ACCEPTED"}),
            "RETRY_PENDING": frozenset({"RETRY_PENDING"}),
            "FAILED": frozenset({"FAILED"}),
            "BUDGET_BLOCKED": frozenset({"FAILED"}),
            "VERIFICATION_FAILED": frozenset({"VERIFICATION_FAILED"}),
            "WAITING_ACCEPTANCE_REVIEW": frozenset(
                {"WAITING_ACCEPTANCE_REVIEW"}
            ),
            "WAITING_APPROVAL": frozenset({"WAITING_APPROVAL"}),
            "CANCELLED": frozenset({"CANCELLED"}),
            # Recovery may be detected before the active Attempt/Lease can be
            # closed, or after a reservation conflict has already failed it.
            "RECOVERY_REQUIRED": frozenset({"RUNNING", "FAILED"}),
        }
        expected_attempt_statuses = {
            "ACCEPTED": frozenset({"SUCCEEDED"}),
            "WAITING_ACCEPTANCE_REVIEW": frozenset({"SUCCEEDED"}),
            "VERIFICATION_FAILED": frozenset({"SUCCEEDED"}),
            "FAILED": frozenset({"FAILED"}),
            "BUDGET_BLOCKED": frozenset({"FAILED"}),
            "RETRY_PENDING": frozenset({"FAILED"}),
            "CANCELLED": frozenset({"CANCELLED"}),
            "RECOVERY_REQUIRED": frozenset({"RUNNING", "FAILED"}),
        }
        admitted_node_ids = {binding.node_id for binding in admitted.nodes}
        observed_node_ids = [outcome.node_id for outcome in outcomes]
        if (
            len(set(observed_node_ids)) != len(observed_node_ids)
            or not set(observed_node_ids).issubset(admitted_node_ids)
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel outcome set does not match the admitted TaskGraph",
            )
        snapshot = _mapping(
            await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            ),
            field_name="parallel outcome scheduler snapshot",
        )
        attempt_rows = snapshot.get("attempts")
        if not isinstance(attempt_rows, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel outcome snapshot has no durable Attempts",
            )
        latest_attempt_by_node: dict[str, dict[str, Any]] = {}
        for raw_attempt in attempt_rows:
            if not isinstance(raw_attempt, Mapping):
                continue
            attempt_row = dict(raw_attempt)
            node_id = _text(attempt_row.get("node_id"))
            current = latest_attempt_by_node.get(node_id)
            sort_key = (
                int(attempt_row.get("attempt_no") or 0),
                _text(attempt_row.get("attempt_id")),
            )
            current_key = (
                int((current or {}).get("attempt_no") or 0),
                _text((current or {}).get("attempt_id")),
            )
            if current is None or sort_key > current_key:
                latest_attempt_by_node[node_id] = attempt_row
        for outcome in outcomes:
            allowed = expected_durable_statuses.get(outcome.status)
            if allowed is None:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel TaskNode {outcome.node_id} returned an unknown outcome",
                )
            durable_node = _mapping(
                await _maybe_await(
                    self._store.get_node(
                        admitted.graph_id,
                        admitted.graph_revision,
                        outcome.node_id,
                    )
                ),
                field_name=f"parallel outcome node {outcome.node_id}",
            )
            durable_status = _status(durable_node)
            if durable_status not in allowed:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    (
                        f"parallel TaskNode {outcome.node_id} receipt status "
                        f"{outcome.status} conflicts with durable status {durable_status}"
                    ),
                )
            durable_attempt = _mapping(
                await _maybe_await(self._store.get_attempt(outcome.attempt_id)),
                field_name=f"parallel outcome attempt {outcome.attempt_id}",
            )
            allowed_attempt_statuses = expected_attempt_statuses.get(outcome.status)
            if (
                allowed_attempt_statuses is None
                or _text(durable_attempt.get("graph_id")) != admitted.graph_id
                or int(durable_attempt.get("graph_revision") or 0)
                != admitted.graph_revision
                or _text(durable_attempt.get("node_id")) != outcome.node_id
                or _status(durable_attempt) not in allowed_attempt_statuses
                or _text(
                    latest_attempt_by_node.get(outcome.node_id, {}).get("attempt_id")
                )
                != outcome.attempt_id
            ):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel TaskNode {outcome.node_id} receipt Attempt lineage drifted",
                )

    async def _settle_parallel_terminal(
        self,
        *,
        admitted: AdmittedExecution,
        outcomes: tuple[_ParallelNodeOutcome, ...],
        budget: BudgetRuntimeContext,
        graph_target_status: str,
        release_budget: bool,
        decision_kind: str,
        selected_node_id: str,
    ) -> tuple[BudgetRuntimeContext, str, str]:
        graph = _mapping(
            await _maybe_await(
                self._store.get_graph(admitted.graph_id, admitted.graph_revision)
            ),
            field_name="parallel settlement graph",
        )
        durable_nodes: dict[str, dict[str, Any]] = {}
        for binding in admitted.nodes:
            durable_nodes[binding.node_id] = _mapping(
                await _maybe_await(
                    self._store.get_node(
                        admitted.graph_id,
                        admitted.graph_revision,
                        binding.node_id,
                    )
                ),
                field_name=f"parallel settlement node {binding.node_id}",
            )
        budget = await self._budgets.refresh(budget)
        settlement_snapshot = _mapping(
            await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            ),
            field_name="parallel settlement scheduler snapshot",
        )
        raw_attempts = settlement_snapshot.get("attempts")
        if not isinstance(raw_attempts, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement snapshot has no durable Attempt journal",
            )
        latest_attempt_by_node: dict[str, dict[str, Any]] = {}
        for raw_attempt in raw_attempts:
            if not isinstance(raw_attempt, Mapping):
                continue
            attempt = dict(raw_attempt)
            node_id = _text(attempt.get("node_id"))
            if not node_id:
                continue
            current = latest_attempt_by_node.get(node_id)
            attempt_key = (
                int(attempt.get("attempt_no") or 0),
                _text(attempt.get("attempt_id")),
            )
            current_key = (
                int((current or {}).get("attempt_no") or 0),
                _text((current or {}).get("attempt_id")),
            )
            if current is None or attempt_key > current_key:
                latest_attempt_by_node[node_id] = attempt
        expected_node_states = {
            node_id: {
                "status": _status(node),
                "status_version": _version(node),
            }
            for node_id, node in sorted(durable_nodes.items())
        }
        decision_outcomes: list[dict[str, Any]] = []
        rejected_budget_usage_refs: dict[tuple[str, str], str] = {}
        all_budget_blocked_outcomes = tuple(
            outcome for outcome in outcomes if outcome.status == "BUDGET_BLOCKED"
        )
        budget_blocked_outcomes = tuple(
            outcome
            for outcome in all_budget_blocked_outcomes
            if outcome.node_id == selected_node_id
        )
        if all_budget_blocked_outcomes:
            if decision_kind != "BUDGET" or len(budget_blocked_outcomes) != 1:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel Budget decision must select one blocked outcome",
                )
            raw_usages = settlement_snapshot.get("budget_usage")
            if not isinstance(raw_usages, list):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel Budget decision has no durable usage ledger",
                )
            for outcome in budget_blocked_outcomes:
                candidates: list[tuple[tuple[int, int, str], Mapping[str, Any]]] = []
                for raw_usage in raw_usages:
                    if not isinstance(raw_usage, Mapping):
                        continue
                    exceeded_dimensions = raw_usage.get("exceeded_dimensions")
                    if (
                        _text(raw_usage.get("budget_id")) != budget.budget_id
                        or _text(raw_usage.get("node_id")) != outcome.node_id
                        or _text(raw_usage.get("attempt_id")) != outcome.attempt_id
                        or raw_usage.get("accepted") is not False
                        or not isinstance(exceeded_dimensions, list)
                        or not exceeded_dimensions
                    ):
                        continue
                    candidates.append(
                        (
                            (
                                int(raw_usage.get("reservation_version_after") or 0),
                                int(raw_usage.get("created_ts_ms") or 0),
                                _text(raw_usage.get("usage_id")),
                            ),
                            raw_usage,
                        )
                    )
                if not candidates:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "parallel Budget decision lacks rejected durable usage lineage",
                    )
                _, selected_usage = min(candidates, key=lambda item: item[0])
                rejected_budget_usage_refs[(outcome.node_id, outcome.attempt_id)] = (
                    _required_record_text(selected_usage, "usage_id")
                )
        decision_terminal_statuses = frozenset(
            {
                "ACCEPTED",
                "FAILED",
                "RETRY_PENDING",
                "VERIFICATION_FAILED",
                "WAITING_ACCEPTANCE_REVIEW",
                "CANCELLED",
            }
        )
        decision_outcome_node_ids: set[str] = set()
        for binding in admitted.nodes:
            durable_status = _status(durable_nodes[binding.node_id])
            if durable_status not in decision_terminal_statuses:
                continue
            durable_attempt = latest_attempt_by_node.get(binding.node_id)
            if durable_attempt is None:
                continue
            attempt_id = _required_record_text(durable_attempt, "attempt_id")
            node_admission = admitted.for_node(binding.node_id)
            reservation = await self._try_operation(node_admission.reservation_id)
            durable_evidence = tuple(
                dict.fromkeys(
                    reference
                    for reference in (
                        *_record_evidence_refs(durable_nodes[binding.node_id]),
                        _text(durable_attempt.get("error_ref")),
                        _text((reservation or {}).get("receipt_ref")),
                        rejected_budget_usage_refs.get(
                            (binding.node_id, attempt_id)
                        ),
                    )
                    if reference
                )
            )
            decision_status = (
                "BUDGET_BLOCKED"
                if (
                    decision_kind == "BUDGET"
                    and binding.node_id == selected_node_id
                )
                else durable_status
            )
            decision_outcome_node_ids.add(binding.node_id)
            decision_outcomes.append(
                {
                    "node_id": binding.node_id,
                    "attempt_id": attempt_id,
                    "status": decision_status,
                    "accepted": decision_status == "ACCEPTED",
                    "reason_code": _canonical_parallel_outcome_reason_code(
                        decision_status
                    ),
                    "evidence_refs": list(durable_evidence),
                }
            )
        required_outcome_node_ids = {outcome.node_id for outcome in outcomes}
        if not required_outcome_node_ids.issubset(decision_outcome_node_ids):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel decision cannot reconstruct every durable batch outcome",
            )
        graph_version = _version(graph)
        budget_version = budget.version
        graph_outbox = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_GRAPH",
            aggregate_id=admitted.graph_id,
            aggregate_version=graph_version + 1,
            event_type=f"TASK_GRAPH_{graph_target_status}",
        )
        budget_outbox = (
            self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget.budget_id,
                aggregate_version=budget_version + 1,
                event_type="BUDGET_RESERVATION_RELEASED",
            )
            if release_budget
            else None
        )
        decision_payload = {
            "schema_version": "Natsume.task_runtime.parallel_settlement.v2",
            "decision_kind": decision_kind,
            "selected_node_id": selected_node_id,
            "graph_id": admitted.graph_id,
            "graph_revision": admitted.graph_revision,
            "graph_status_before": _status(graph),
            "graph_status_version_before": graph_version,
            "graph_target_status": graph_target_status,
            "budget_id": budget.budget_id,
            "budget_status_before": budget.status,
            "budget_status_version_before": budget_version,
            "budget_target_status": (
                BudgetReservationStatus.RELEASED.value if release_budget else None
            ),
            "node_states": expected_node_states,
            "outcomes": decision_outcomes,
            "graph_outbox_id": graph_outbox.outbox_id,
            "budget_outbox_id": (
                budget_outbox.outbox_id if budget_outbox is not None else None
            ),
        }
        try:
            decision = normalize_parallel_settlement_decision(decision_payload)
        except ParallelSettlementContractError as exc:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel settlement decision is invalid: {exc}",
            ) from exc
        decision_ref = decision.decision_ref
        graph_outbox = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_GRAPH",
            aggregate_id=admitted.graph_id,
            aggregate_version=graph_version + 1,
            event_type=f"TASK_GRAPH_{graph_target_status}",
            evidence_refs=(decision_ref,),
        )
        budget_outbox = (
            self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget.budget_id,
                aggregate_version=budget_version + 1,
                event_type="BUDGET_RESERVATION_RELEASED",
                evidence_refs=(decision_ref,),
            )
            if release_budget
            else None
        )
        arguments = {
            "budget_id": budget.budget_id,
            "expected_graph_version": graph_version,
            "expected_budget_version": budget_version,
            "expected_node_states": expected_node_states,
            "graph_target_status": graph_target_status,
            "budget_target_status": (
                BudgetReservationStatus.RELEASED.value if release_budget else None
            ),
            "decision_ref": decision_ref,
            "decision_payload": decision.payload,
            "graph_outbox": graph_outbox,
            "budget_outbox": budget_outbox,
        }
        try:
            settled_raw = await _maybe_await(
                self._store.settle_parallel_graph_budget(
                    admitted.graph_id,
                    admitted.graph_revision,
                    **arguments,
                )
            )
        except Exception as first_exc:
            first_reason = _exception_reason(first_exc, "")
            if first_reason == _TASK_STORE_OWNERSHIP_CONFLICT or (
                first_reason == _TASK_STORE_CAS_CONFLICT
                and await self._parallel_settlement_conflict_is_in_progress(
                    admitted=admitted
                )
            ):
                raise GovernedRuntimeError(
                    GOVERNED_EXECUTION_IN_PROGRESS,
                    "parallel TaskGraph gained an in-progress child before settlement",
                ) from first_exc
            try:
                settled_raw = await _maybe_await(
                    self._store.settle_parallel_graph_budget(
                        admitted.graph_id,
                        admitted.graph_revision,
                        **arguments,
                    )
                )
            except Exception as replay_exc:
                replay_reason = _exception_reason(replay_exc, "")
                if replay_reason == _TASK_STORE_OWNERSHIP_CONFLICT or (
                    replay_reason == _TASK_STORE_CAS_CONFLICT
                    and await self._parallel_settlement_conflict_is_in_progress(
                        admitted=admitted
                    )
                ):
                    raise GovernedRuntimeError(
                        GOVERNED_EXECUTION_IN_PROGRESS,
                        "parallel TaskGraph gained an in-progress child before settlement",
                    ) from replay_exc
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel Graph/Budget settlement could not be committed or exactly replayed",
                ) from replay_exc
            _logger.warning(
                "Replayed committed parallel Graph/Budget settlement",
                extra={
                    "graph_id": admitted.graph_id,
                    "graph_revision": admitted.graph_revision,
                    "decision_ref": decision_ref,
                    "first_exception_type": type(first_exc).__name__,
                },
            )
        settled = _mapping(settled_raw, field_name="parallel Graph/Budget settlement")
        settled_graph = _mapping(
            settled.get("graph"),
            field_name="parallel Graph/Budget settlement graph",
        )
        settled_budget = _mapping(
            settled.get("budget"),
            field_name="parallel Graph/Budget settlement budget",
        )
        settled_decision = _mapping(
            settled.get("decision"),
            field_name="parallel Graph/Budget settlement decision",
        )
        expected_budget_status = (
            BudgetReservationStatus.RELEASED.value if release_budget else budget.status
        )
        if (
            _status(settled_graph) != graph_target_status
            or _status(settled_budget) != expected_budget_status
            or _text(settled_decision.get("decision_ref")) != decision_ref
            or settled_decision.get("payload") != decision.payload
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement returned an invalid Graph/Budget terminal state",
            )
        await self._runtime_outbox.publish_pending()
        return budget.refreshed(settled_budget), _status(settled_graph), decision_ref

    async def _complete_parallel_success(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        budget: BudgetRuntimeContext,
        node_grant_refs: Mapping[str, str],
    ) -> tuple[BudgetRuntimeContext, dict[str, Any]]:
        """Commit the B5 all-accepted outer success boundary."""

        result_node_id = _text(admitted.result_node_id)
        if not result_node_id or self._projection_reader is None:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel success requires an explicit result node and projection reader",
            )
        result_admission = admitted.for_node(result_node_id)
        graph = _mapping(
            await _maybe_await(
                self._store.get_graph(admitted.graph_id, admitted.graph_revision)
            ),
            field_name="parallel success graph",
        )
        durable_nodes = {
            binding.node_id: _mapping(
                await _maybe_await(
                    self._store.get_node(
                        admitted.graph_id,
                        admitted.graph_revision,
                        binding.node_id,
                    )
                ),
                field_name=f"parallel success node {binding.node_id}",
            )
            for binding in admitted.nodes
        }
        if any(_status(node) != "ACCEPTED" for node in durable_nodes.values()):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel success requires every durable TaskNode to be ACCEPTED",
            )
        expected_node_states = {
            node_id: {
                "status": _status(node),
                "status_version": _version(node),
            }
            for node_id, node in sorted(durable_nodes.items())
        }
        snapshot = _mapping(
            await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            ),
            field_name="parallel success scheduler snapshot",
        )
        raw_attempts = snapshot.get("attempts")
        if not isinstance(raw_attempts, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel success snapshot has no durable Attempt journal",
            )
        latest_attempt_by_node: dict[str, dict[str, Any]] = {}
        for raw_attempt in raw_attempts:
            if not isinstance(raw_attempt, Mapping):
                continue
            attempt = dict(raw_attempt)
            node_id = _text(attempt.get("node_id"))
            if not node_id:
                continue
            current = latest_attempt_by_node.get(node_id)
            key = (
                int(attempt.get("attempt_no") or 0),
                _text(attempt.get("attempt_id")),
            )
            current_key = (
                int((current or {}).get("attempt_no") or 0),
                _text((current or {}).get("attempt_id")),
            )
            if current is None or key > current_key:
                latest_attempt_by_node[node_id] = attempt

        accepted_outcomes: list[dict[str, Any]] = []
        projections: dict[str, dict[str, Any]] = {}
        receipt_refs: dict[str, str] = {}
        for binding in admitted.nodes:
            attempt = latest_attempt_by_node.get(binding.node_id)
            if attempt is None or _status(attempt) != "SUCCEEDED":
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel success node lacks latest SUCCEEDED Attempt: {binding.node_id}",
                )
            attempt_id = _required_record_text(attempt, "attempt_id")
            reservation = await self._try_operation(
                admitted.for_node(binding.node_id).reservation_id
            )
            receipt_ref = _text((reservation or {}).get("receipt_ref"))
            if _status(reservation or {}) != "COMMITTED" or not receipt_ref:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel success node lacks committed operation receipt: {binding.node_id}",
                )
            loaded = await _maybe_await(self._projection_reader.load(receipt_ref))
            if not isinstance(loaded, Mapping):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel success node lacks durable worker projection: {binding.node_id}",
                )
            try:
                projection = validate_worker_projection_payload(
                    loaded,
                    expected_projection_id=receipt_ref,
                )
            except WorkerProjectionStoreError as exc:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel success projection is not canonical: {binding.node_id}",
                ) from exc
            expected_route, expected_target = _projection_route_authority(
                binding=binding,
                work_order=work_order,
                command=command,
            )
            expected_node_grant_ref = _text(
                node_grant_refs.get(binding.node_id)
            )
            payload_digest = _text(projection.get("payload_digest"))
            if (
                not expected_node_grant_ref
                or _text(projection.get("command_id")) != admitted.command_id
                or _text(projection.get("request_id")) != command.request_id
                or _text(projection.get("graph_id")) != admitted.graph_id
                or int(projection.get("graph_revision") or 0)
                != admitted.graph_revision
                or _text(projection.get("node_id")) != binding.node_id
                or _text(projection.get("attempt_id")) != attempt_id
                or _text(projection.get("route_type")).lower() != expected_route
                or _text(projection.get("target_id")) != expected_target
                or _text(projection.get("node_grant_ref"))
                != expected_node_grant_ref
                or _text(projection.get("decision")).upper() != "APPROVE"
            ):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel success projection authority drifted: {binding.node_id}",
                )
            projections[binding.node_id] = projection
            receipt_refs[binding.node_id] = receipt_ref
            accepted_outcomes.append(
                {
                    "node_id": binding.node_id,
                    "attempt_id": attempt_id,
                    "receipt_ref": receipt_ref,
                    "payload_sha256": payload_digest,
                }
            )

        result_attempt_id = _required_record_text(
            latest_attempt_by_node[result_node_id],
            "attempt_id",
        )
        result_receipt_ref = receipt_refs[result_node_id]
        result_projection = projections[result_node_id]
        budget = await self._budgets.refresh(budget)
        graph_status = _status(graph)
        if graph_status == "COMPLETED":
            raw_decision = await _maybe_await(
                self._store.get_latest_parallel_success_decision(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            )
            if not isinstance(raw_decision, Mapping):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "completed parallel success has no canonical decision",
                )
            try:
                decision = normalize_parallel_success_decision(
                    _mapping(
                        raw_decision.get("payload"),
                        field_name="parallel success replay payload",
                    )
                )
            except (ParallelSuccessContractError, TypeError) as exc:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel success replay decision is invalid: {exc}",
                ) from exc
            frozen_outcomes = sorted(
                accepted_outcomes,
                key=lambda item: item["node_id"],
            )
            if (
                decision.payload["completion_policy"]
                != TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value
                or decision.payload["result_node_id"] != result_node_id
                or decision.payload["result_attempt_id"] != result_attempt_id
                or decision.payload["result_receipt_ref"] != result_receipt_ref
                or decision.payload["node_states"] != expected_node_states
                or decision.payload["accepted_outcomes"] != frozen_outcomes
                or decision.payload["budget_id"] != budget.budget_id
                or budget.status != "CONSUMED"
                or budget.version
                != int(decision.payload["budget_status_version_before"]) + 1
            ):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "completed parallel success differs from its frozen authority",
                )
            evidence_refs = tuple(
                dict.fromkeys(
                    (
                        decision.decision_ref,
                        result_receipt_ref,
                        *(
                            str(effect["effect_id"])
                            for effect in decision.payload["effect_intents"]
                        ),
                    )
                )
            )
            await self._runtime_outbox.publish_pending()
            public_projection = dict(result_projection)
            public_projection["budget"] = budget.execution_payload()
            public_projection["parallel_success"] = {
                "graph_status": "COMPLETED",
                "decision_ref": decision.decision_ref,
                "result_node_id": result_node_id,
            }
            receipt = build_execution_receipt(
                result_admission,
                attempt_id=result_attempt_id,
                receipt_ref=result_receipt_ref,
                payload_digest=_text(result_projection.get("payload_digest")) or None,
                status="ACCEPTED",
                verification_status="PASS",
                accepted=True,
                replayed=True,
                evidence_refs=tuple(
                    dict.fromkeys(
                        (
                            *_record_evidence_refs(durable_nodes[result_node_id]),
                            *evidence_refs,
                        )
                    )
                ),
                projection=public_projection,
            )
            return budget, approved_commit_event(public_projection, receipt)

        if graph_status != "ACTIVE" or budget.status != "ACTIVE":
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "all-accepted Graph/Budget are not in a committable state",
            )
        try:
            effect_intents = compile_graph_acceptance_effects(
                command=command,
                work_order=work_order,
                node_id=result_node_id,
                attempt_id=result_attempt_id,
                worker_commit=result_projection,
            )
        except BaseException as exc:
            raise GovernedRuntimeError(
                _exception_reason(exc, "ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID"),
                "parallel success effects could not be compiled from the durable result projection",
            ) from exc
        effect_summaries = [
            {
                "effect_id": effect.effect_id,
                "node_id": effect.node_id,
                "attempt_id": effect.attempt_id,
                "commit_scope": str(getattr(effect.commit_scope, "value", effect.commit_scope)),
                "effect_type": effect.effect_type,
                "payload_ref": effect.payload_ref,
                "payload_sha256": effect.payload_sha256,
                "idempotency_key": effect.idempotency_key,
            }
            for effect in effect_intents
        ]
        if graph_status == "ACTIVE" and budget.status == "ACTIVE":
            graph_version = _version(graph)
            budget_version = budget.version
            graph_outbox_seed = self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="TASK_GRAPH",
                aggregate_id=admitted.graph_id,
                aggregate_version=graph_version + 1,
                event_type="TASK_GRAPH_COMPLETED",
            )
            budget_outbox_seed = self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget.budget_id,
                aggregate_version=budget_version + 1,
                event_type="BUDGET_RESERVATION_CONSUMED",
            )
            try:
                decision = normalize_parallel_success_decision(
                    {
                        "schema_version": "Natsume.task_runtime.parallel_success.v1",
                        "graph_id": admitted.graph_id,
                        "graph_revision": admitted.graph_revision,
                        "graph_status_before": "ACTIVE",
                        "graph_status_version_before": graph_version,
                        "graph_target_status": "COMPLETED",
                        "completion_policy": (
                            TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value
                        ),
                        "budget_id": budget.budget_id,
                        "budget_status_before": "ACTIVE",
                        "budget_status_version_before": budget_version,
                        "budget_target_status": "CONSUMED",
                        "result_node_id": result_node_id,
                        "result_attempt_id": result_attempt_id,
                        "result_receipt_ref": result_receipt_ref,
                        "node_states": expected_node_states,
                        "accepted_outcomes": accepted_outcomes,
                        "effect_intents": effect_summaries,
                        "graph_outbox_id": graph_outbox_seed.outbox_id,
                        "budget_outbox_id": budget_outbox_seed.outbox_id,
                    }
                )
            except ParallelSuccessContractError as exc:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel success decision is invalid: {exc}",
                ) from exc
        evidence_refs = tuple(
            dict.fromkeys(
                (
                    decision.decision_ref,
                    result_receipt_ref,
                    *(effect.effect_id for effect in effect_intents),
                )
            )
        )
        graph_outbox = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_GRAPH",
            aggregate_id=admitted.graph_id,
            aggregate_version=graph_version + 1,
            event_type="TASK_GRAPH_COMPLETED",
            evidence_refs=evidence_refs,
        )
        budget_outbox = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id=budget.budget_id,
            aggregate_version=budget_version + 1,
            event_type="BUDGET_RESERVATION_CONSUMED",
            evidence_refs=evidence_refs,
        )
        arguments = {
            "budget_id": budget.budget_id,
            "expected_graph_version": graph_version,
            "expected_budget_version": budget_version,
            "expected_node_states": expected_node_states,
            "decision_ref": decision.decision_ref,
            "decision_payload": decision.payload,
            "graph_outbox": graph_outbox,
            "budget_outbox": budget_outbox,
            "effect_intents": effect_intents,
        }
        try:
            settled_raw = await _maybe_await(
                self._store.complete_parallel_graph_success(
                    admitted.graph_id,
                    admitted.graph_revision,
                    **arguments,
                )
            )
        except Exception as first_exc:
            try:
                settled_raw = await _maybe_await(
                    self._store.complete_parallel_graph_success(
                        admitted.graph_id,
                        admitted.graph_revision,
                        **arguments,
                    )
                )
            except Exception as replay_exc:
                reason = _exception_reason(replay_exc, "")
                try:
                    latest_graph = await _maybe_await(
                        self._store.get_graph(
                            admitted.graph_id,
                            admitted.graph_revision,
                        )
                    )
                except Exception:
                    latest_graph = None
                if isinstance(latest_graph, Mapping) and _status(latest_graph) == "COMPLETED":
                    # Both responses may be lost after the atomic transaction.
                    # Re-enter through the completed branch, which reads the
                    # frozen 0091 authority and never recompiles effects.
                    return await self._complete_parallel_success(
                        admitted=admitted,
                        command=command,
                        work_order=work_order,
                        budget=budget,
                        node_grant_refs=node_grant_refs,
                    )
                if reason == _TASK_STORE_OWNERSHIP_CONFLICT:
                    raise GovernedRuntimeError(
                        GOVERNED_EXECUTION_IN_PROGRESS,
                        "parallel success still has an active durable owner",
                    ) from replay_exc
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel success could not be committed or exactly replayed",
                ) from replay_exc
            _logger.warning(
                "Replayed committed parallel success",
                extra={
                    "graph_id": admitted.graph_id,
                    "graph_revision": admitted.graph_revision,
                    "decision_ref": decision.decision_ref,
                    "first_exception_type": type(first_exc).__name__,
                },
            )
        settled = _mapping(settled_raw, field_name="parallel success settlement")
        settled_graph = _mapping(settled.get("graph"), field_name="parallel success graph")
        settled_budget = _mapping(settled.get("budget"), field_name="parallel success budget")
        settled_decision = _mapping(
            settled.get("decision"),
            field_name="parallel success decision",
        )
        if (
            _status(settled_graph) != "COMPLETED"
            or _status(settled_budget) != "CONSUMED"
            or _text(settled_decision.get("decision_ref")) != decision.decision_ref
            or settled_decision.get("payload") != decision.payload
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel success returned an invalid terminal authority",
            )
        budget = budget.refreshed(settled_budget)
        await self._runtime_outbox.publish_pending()
        public_projection = dict(result_projection)
        public_projection["budget"] = budget.execution_payload()
        public_projection["parallel_success"] = {
            "graph_status": "COMPLETED",
            "decision_ref": decision.decision_ref,
            "result_node_id": result_node_id,
        }
        receipt = build_execution_receipt(
            result_admission,
            attempt_id=result_attempt_id,
            receipt_ref=result_receipt_ref,
            payload_digest=_text(result_projection.get("payload_digest")) or None,
            status="ACCEPTED",
            verification_status="PASS",
            accepted=True,
            replayed=bool(settled.get("replayed")),
            evidence_refs=tuple(
                dict.fromkeys(
                    (
                        *_record_evidence_refs(durable_nodes[result_node_id]),
                        *evidence_refs,
                    )
                )
            ),
            projection=public_projection,
        )
        return budget, approved_commit_event(public_projection, receipt)

    @staticmethod
    def _parallel_event_with_budget(
        event: Mapping[str, Any],
        *,
        budget: BudgetRuntimeContext,
        graph_status: str,
        decision_ref: str | None,
    ) -> dict[str, Any]:
        projected = dict(event)
        receipt_raw = projected.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        projection_raw = receipt.get("projection")
        projection = dict(projection_raw) if isinstance(projection_raw, Mapping) else {}
        projection["budget"] = budget.execution_payload()
        projection["parallel_settlement"] = {
            "graph_status": graph_status,
            "decision_ref": decision_ref,
        }
        if decision_ref:
            receipt["evidence_refs"] = list(
                dict.fromkeys((*_evidence_refs(receipt), decision_ref))
            )
        receipt["projection"] = projection
        projected["execution_receipt"] = receipt
        return projected

    async def _execute_node_stream(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        budget: BudgetRuntimeContext | None = None,
        node_grant_ref: str | None = None,
        defer_shared_terminal: bool = False,
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

        replay_receipt = await self._terminal_replay(admitted, graph=graph, node=node)
        if replay_receipt is not None:
            await self._runtime_outbox.publish_pending()
            yield terminal_event(replay_receipt)
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
            if defer_shared_terminal:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel node execution requires an outer-activated TaskGraph",
                )
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
            failure_ref = await self._mark_pre_reservation_failure(
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
        if not precharge.accepted:
            receipt = await self._reject_budget_charge(
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
                failure_ref = await self._mark_pre_reservation_failure(
                    admitted,
                    graph=graph,
                    node=node,
                    attempt=attempt,
                    attempt_id=attempt_id,
                    fencing_token=fencing_token,
                    reason_code=reason_code,
                    defer_graph_terminal=defer_shared_terminal,
                )
                budget = await self._finalize_node_budget(
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
                    failure_ref = await self._mark_pre_reservation_failure(
                        admitted,
                        graph=graph,
                        node=node,
                        attempt=current_attempt,
                        attempt_id=attempt_id,
                        fencing_token=fencing_token,
                        reason_code=GOVERNED_DURABLE_STATE_CONFLICT,
                        evidence_refs=(error_ref,),
                        defer_graph_terminal=defer_shared_terminal,
                    )
                    budget = await self._finalize_node_budget(
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
            budget = await self._finalize_node_budget(
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
            failure_ref = await self._mark_pre_reservation_failure(
                admitted,
                graph=graph,
                node=node,
                attempt=attempt,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                reason_code=reason_code,
                defer_graph_terminal=defer_shared_terminal,
            )
            budget = await self._finalize_node_budget(
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
            await self._mark_worker_failure(
                admitted,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
                graph_target=(None if defer_shared_terminal else "BLOCKED"),
            )
            budget = await self._finalize_node_budget(
                budget,
                target_status=BudgetReservationStatus.RELEASED,
                defer_shared_terminal=defer_shared_terminal,
            )
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
            "node_input_refs": list(_node_input_refs(node)),
            "node_acceptance_criteria": list(admitted.active_node.acceptance_criteria),
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
            await _stop_heartbeat_after_interruption(heartbeat)
            raise
        except Exception as exc:
            await _stop_heartbeat_after_interruption(heartbeat)
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
            async for raw_event in _worker_event_stream(
                worker,
                command=command,
                work_order=work_order,
                execution_context=execution_context,
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
                raise worker_error
        if worker_error is not None and not isinstance(worker_error, Exception):
            raise worker_error
        if heartbeat_error is not None and not isinstance(heartbeat_error, Exception):
            raise heartbeat_error
        if heartbeat_error is not None:
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
                },
                exc_info=(
                    type(heartbeat_error),
                    heartbeat_error,
                    heartbeat_error.__traceback__,
                ),
            )
            await self._runtime_outbox.publish_pending(best_effort=True)
            receipt = build_execution_receipt(
                admitted,
                attempt_id=attempt_id,
                status="RECOVERY_REQUIRED",
                verification_status="NOT_RUN",
                accepted=False,
                reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
                projection={
                    "heartbeat_phase": _heartbeat_failure_phase(heartbeat_error),
                    "budget": budget.execution_payload(),
                },
            )
            yield terminal_event(receipt)
            return
        if worker_error is not None:
            exc = worker_error
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
                    admitted,
                    charge,
                    attempt_id=attempt_id,
                    fencing_token=fencing_token,
                    defer_shared_terminal=defer_shared_terminal,
                )
                if defer_shared_terminal:
                    receipt = build_execution_receipt(
                        admitted,
                        attempt_id=attempt_id,
                        status="RECOVERY_REQUIRED",
                        verification_status="NOT_RUN",
                        accepted=False,
                        reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
                        evidence_refs=(charge.evidence_ref,),
                        projection={
                            "worker_failure_reason_code": _exception_reason(
                                exc,
                                GOVERNED_WORKER_FAILED,
                            ),
                            "reservation_status": "RESERVED",
                            "budget": charge.context.execution_payload(),
                        },
                    )
                yield terminal_event(receipt)
                return
            worker_failure_reason = _exception_reason(exc, GOVERNED_WORKER_FAILED)
            if worker_failure_reason == GOVERNED_WORKER_SELF_CANCELLED:
                # The reservation may already represent an external side
                # effect.  A worker cannot convert its own interruption into
                # graph/business cancellation authority, and it also cannot
                # safely declare the operation failed.  Leave the durable
                # Attempt/Lease/reservation for normal recovery fencing.
                await self._runtime_outbox.publish_pending(best_effort=True)
                receipt = build_execution_receipt(
                    admitted,
                    attempt_id=attempt_id,
                    status="RECOVERY_REQUIRED",
                    verification_status="NOT_RUN",
                    accepted=False,
                    reason_code=GOVERNED_OPERATION_RECOVERY_REQUIRED,
                    evidence_refs=(charge.evidence_ref,),
                    projection={
                        "worker_failure_reason_code": worker_failure_reason,
                        "reservation_status": "RESERVED",
                        "budget": budget.execution_payload(),
                    },
                )
                yield terminal_event(receipt)
                return
            await self._mark_worker_failure(
                admitted,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                reason_code=worker_failure_reason,
                graph_target=(None if defer_shared_terminal else "FAILED"),
            )
            budget = await self._finalize_node_budget(
                budget,
                target_status=BudgetReservationStatus.RELEASED,
                defer_shared_terminal=defer_shared_terminal,
            )
            receipt = build_execution_receipt(
                admitted,
                attempt_id=attempt_id,
                status=("RECOVERY_REQUIRED" if defer_shared_terminal else "FAILED"),
                verification_status="NOT_RUN",
                accepted=False,
                reason_code=(
                    GOVERNED_OPERATION_RECOVERY_REQUIRED
                    if defer_shared_terminal
                    else worker_failure_reason
                ),
                evidence_refs=(charge.evidence_ref,),
                projection={
                    "worker_failure_reason_code": worker_failure_reason,
                    "reservation_status": "RESERVED",
                    "budget": budget.execution_payload(),
                },
            )
            yield terminal_event(receipt)
            return

        reservation_version = heartbeat.latest_reservation_version
        if reservation_version is None:
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                "operation reservation was detached from its execution heartbeat",
            )
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
                )
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
                graph_target=(None if defer_shared_terminal else "FAILED"),
            )
            budget = await self._finalize_node_budget(
                budget,
                target_status=BudgetReservationStatus.RELEASED,
                defer_shared_terminal=defer_shared_terminal,
            )
            receipt = build_execution_receipt(
                admitted,
                attempt_id=attempt_id,
                status="FAILED",
                verification_status="NOT_RUN",
                accepted=False,
                reason_code=_exception_reason(exc, GOVERNED_BUDGET_STATE_CONFLICT),
                projection={"budget": budget.execution_payload()},
            )
            yield terminal_event(receipt)
            return
        budget = charge.context
        if not charge.accepted:
            receipt = await self._reject_budget_charge(
                admitted,
                charge,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                defer_shared_terminal=defer_shared_terminal,
            )
            yield terminal_event(receipt)
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
            budget = await self._finalize_node_budget(
                budget,
                target_status=BudgetReservationStatus.RELEASED,
                defer_shared_terminal=defer_shared_terminal,
            )
            if not defer_shared_terminal:
                graph = await self._transition_graph(admitted, graph, "FAILED")
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
            if not defer_shared_terminal:
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

        node = await self._transition_node(
            admitted,
            node,
            "ACCEPTED",
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
                        await self._runtime_outbox.publish_pending()
                        receipt = build_execution_receipt(
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
                        yield terminal_event(receipt)
                        return
                else:
                    graph = await self._transition_graph(admitted, graph, "COMPLETED")
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


    async def _matching_parallel_decision(
        self,
        admitted: AdmittedExecution,
        *,
        graph: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        graph_status = _status(graph)
        if (
            len(admitted.nodes) <= 1
            or graph_status not in {"BLOCKED", "FAILED", "CANCELLED"}
        ):
            return None
        raw = await _maybe_await(
            self._store.get_latest_parallel_settlement_decision(
                admitted.graph_id,
                admitted.graph_revision,
            )
        )
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement decision record is invalid",
            )
        payload = raw.get("payload")
        try:
            decision = normalize_parallel_settlement_decision(payload)
        except (ParallelSettlementContractError, TypeError) as exc:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel settlement replay decision is invalid: {exc}",
            ) from exc
        if (
            _text(raw.get("decision_ref")) != decision.decision_ref
            or decision.payload["graph_id"] != admitted.graph_id
            or decision.payload["graph_revision"] != admitted.graph_revision
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement replay decision identity drifted",
            )
        if (
            decision.payload["graph_target_status"] != graph_status
            or decision.payload["graph_status_version_before"] + 1
            != _version(graph)
        ):
            return None
        selected = self._selected_parallel_decision_outcome(
            {"payload": decision.payload}
        )
        selected_admission = admitted.for_node(
            _required_record_text(selected, "node_id")
        )
        attempts = await self._attempts_for_node(selected_admission)
        if (
            not attempts
            or _required_record_text(attempts[-1], "attempt_id")
            != _required_record_text(selected, "attempt_id")
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement replay selected Attempt is not latest",
            )
        record = dict(raw)
        record["decision_ref"] = decision.decision_ref
        record["payload"] = decision.payload
        return record

    @staticmethod
    def _selected_parallel_decision_outcome(
        decision: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = _mapping(
            decision.get("payload"),
            field_name="parallel settlement replay payload",
        )
        selected_node_id = _required_record_text(payload, "selected_node_id")
        raw_outcomes = payload.get("outcomes")
        if not isinstance(raw_outcomes, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement replay outcomes are invalid",
            )
        selected = [
            dict(outcome)
            for outcome in raw_outcomes
            if isinstance(outcome, Mapping)
            and _text(outcome.get("node_id")) == selected_node_id
        ]
        if len(selected) != 1:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement replay selected outcome is missing",
            )
        return selected[0]

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
            graph_status in {"ACTIVE", "BLOCKED"}
            and node_status == "WAITING_ACCEPTANCE_REVIEW"
        )
        terminal = (
            graph_status in {"COMPLETED", "FAILED", "CANCELLED"}
            or node_status in {
                "ACCEPTED",
                "FAILED",
                "RETRY_PENDING",
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
        decision = await self._matching_parallel_decision(admitted, graph=graph)
        if decision is not None:
            selected = self._selected_parallel_decision_outcome(decision)
            selected_node_id = _required_record_text(selected, "node_id")
            if selected_node_id != admitted.node_id:
                decision = None
            elif _required_record_text(selected, "attempt_id") != attempt_id:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel settlement replay selected Attempt drifted",
                )
        decision_ref = (
            _required_record_text(decision, "decision_ref")
            if decision is not None
            else None
        )
        if decision_ref is not None:
            projected = dict(projection or {})
            projected["parallel_settlement"] = {
                "graph_status": graph_status,
                "decision_ref": decision_ref,
            }
            projection = projected
        return build_execution_receipt(
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
            evidence_refs=tuple(
                dict.fromkeys(
                    reference
                    for reference in (
                        *_record_evidence_refs(node),
                        _text(latest.get("error_ref")),
                        receipt_ref,
                        decision_ref,
                    )
                    if reference
                )
            ),
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
                    outbox=self._runtime_outbox.build(
                        command_id=admitted.command_id,
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
                    outbox=self._runtime_outbox.build(
                        command_id=admitted.command_id,
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
                outbox=self._runtime_outbox.build(
                    command_id=admitted.command_id,
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
                    outbox=self._runtime_outbox.build(
                        command_id=admitted.command_id,
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
                    outbox=self._runtime_outbox.build(
                        command_id=admitted.command_id,
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

    async def _finalize_node_budget(
        self,
        budget: BudgetRuntimeContext,
        *,
        target_status: BudgetReservationStatus,
        defer_shared_terminal: bool,
    ) -> BudgetRuntimeContext:
        if defer_shared_terminal:
            return await self._budgets.refresh(budget)
        return await self._budgets.finalize(
            budget,
            target_status=target_status,
        )

    async def _reject_budget_charge(
        self,
        admitted: AdmittedExecution,
        charge: BudgetCharge,
        *,
        attempt_id: str,
        fencing_token: int,
        pre_reservation_graph: Mapping[str, Any] | None = None,
        pre_reservation_node: Mapping[str, Any] | None = None,
        pre_reservation_attempt: Mapping[str, Any] | None = None,
        defer_shared_terminal: bool = False,
    ) -> GovernedExecutionReceipt:
        pre_reservation_state = (
            pre_reservation_graph,
            pre_reservation_node,
            pre_reservation_attempt,
        )
        if all(isinstance(item, Mapping) for item in pre_reservation_state):
            failure_ref = await self._mark_pre_reservation_failure(
                admitted,
                graph=pre_reservation_graph,
                node=pre_reservation_node,
                attempt=pre_reservation_attempt,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                reason_code=GOVERNED_BUDGET_EXCEEDED,
                graph_target=(None if defer_shared_terminal else "BLOCKED"),
                evidence_refs=(charge.evidence_ref, charge.usage_id),
                defer_graph_terminal=defer_shared_terminal,
            )
        else:
            await self._mark_worker_failure(
                admitted,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                reason_code=GOVERNED_BUDGET_EXCEEDED,
                graph_target=(None if defer_shared_terminal else "BLOCKED"),
                evidence_refs=(charge.evidence_ref, charge.usage_id),
            )
            failure_ref = None
        budget = await self._finalize_node_budget(
            charge.context,
            target_status=BudgetReservationStatus.RELEASED,
            defer_shared_terminal=defer_shared_terminal,
        )
        await self._runtime_outbox.publish_pending()
        receipt_evidence = tuple(
            item for item in (charge.evidence_ref, charge.usage_id, failure_ref) if item
        )
        return build_execution_receipt(
            admitted,
            attempt_id=attempt_id,
            status="BUDGET_BLOCKED",
            verification_status="NOT_RUN",
            accepted=False,
            reason_code=GOVERNED_BUDGET_EXCEEDED,
            evidence_refs=receipt_evidence,
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
        graph_target: str | None = "FAILED",
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
            if graph_target is not None:
                graph = await _maybe_await(
                    self._store.get_graph(admitted.graph_id, admitted.graph_revision)
                )
                if _status(graph) == "ACTIVE":
                    await self._transition_graph(admitted, graph, graph_target)
        finally:
            await self._runtime_outbox.publish_pending(best_effort=True)

    async def _mark_pre_reservation_failure(
        self,
        admitted: AdmittedExecution,
        *,
        graph: Mapping[str, Any],
        node: Mapping[str, Any],
        attempt: Mapping[str, Any],
        attempt_id: str,
        fencing_token: int,
        reason_code: str,
        graph_target: str | None = "FAILED",
        evidence_refs: Sequence[str] = (),
        defer_graph_terminal: bool = False,
    ) -> str:
        """Close the execution authority atomically before reservation.

        The exact same mutation is replayed once because the first transaction
        may have committed before its response was lost.  There is deliberately
        no sequential fallback: a partial terminal state would be less safe
        than surfacing a durable conflict.
        """

        error_ref = "taskerror://" + hashlib.sha256(
            f"{attempt_id}\n{reason_code}".encode("utf-8")
        ).hexdigest()
        arguments = {
            "owner_id": self._owner_id,
            "fencing_token": fencing_token,
            "expected_attempt_version": _version(attempt),
            "expected_node_version": _version(node),
            "expected_graph_version": _version(graph),
            "error_ref": error_ref,
            "attempt_outbox": self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="TASK_ATTEMPT",
                aggregate_id=attempt_id,
                aggregate_version=_version(attempt) + 1,
                event_type="TASK_ATTEMPT_FAILED",
                attempt_id=attempt_id,
                evidence_refs=(error_ref, *evidence_refs),
            ),
        }
        if defer_graph_terminal:
            graph_target = None
            fail_attempt = self._store.fail_running_attempt_node_bundle
        else:
            if graph_target is None:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "inline failure requires a Graph terminal target",
                )
            arguments["graph_target_status"] = graph_target
            fail_attempt = self._store.fail_running_attempt_bundle
        try:
            result_raw = await _maybe_await(
                fail_attempt(attempt_id, **arguments)
            )
        except Exception as first_exc:
            try:
                result_raw = await _maybe_await(
                    fail_attempt(attempt_id, **arguments)
                )
            except Exception as replay_exc:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "pre-reservation terminal failure could not be committed or exactly replayed",
                ) from replay_exc
            _logger.warning(
                "Replayed committed pre-reservation terminal failure",
                extra={
                    "attempt_id": attempt_id,
                    "first_exception_type": type(first_exc).__name__,
                },
            )
        result = _mapping(result_raw, field_name="atomic terminal failure")
        failed_attempt = _mapping(
            result.get("attempt"),
            field_name="atomic terminal failure attempt",
        )
        failed_node = _mapping(
            result.get("node"),
            field_name="atomic terminal failure node",
        )
        failed_graph = _mapping(
            result.get("graph"),
            field_name="atomic terminal failure graph",
        )
        released_lease = _mapping(
            result.get("lease"),
            field_name="atomic terminal failure lease",
        )
        if (
            _status(failed_attempt) != "FAILED"
            or _status(failed_node) != "FAILED"
            or _status(failed_graph)
            != ("ACTIVE" if defer_graph_terminal else graph_target)
            or _status(released_lease) != "RELEASED"
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "atomic terminal failure returned an incomplete durable state",
            )
        await self._runtime_outbox.publish_pending()
        return error_ref

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


def _projection_route_authority(
    *,
    binding: AdmittedTaskNode,
    work_order: Any,
    command: HostCommand,
) -> tuple[str, str]:
    """Resolve a worker projection route from frozen graph/work-order facts."""

    try:
        frozen = GovernedWorkerRoute.parse(binding.worker_route)
    except Exception as exc:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"parallel success TaskNode route is invalid: {binding.node_id}",
        ) from exc
    expected_target = _text(frozen.target_id or command.target_id)
    if not expected_target:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"parallel success TaskNode target is missing: {binding.node_id}",
        )

    metadata = getattr(work_order, "metadata", {})
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    planning_lineage = metadata.get("planning_lineage")
    planning_lineage = (
        dict(planning_lineage) if isinstance(planning_lineage, Mapping) else {}
    )
    raw_bindings = planning_lineage.get("node_bindings")
    if isinstance(raw_bindings, list) and raw_bindings:
        matches = [
            dict(candidate)
            for candidate in raw_bindings
            if isinstance(candidate, Mapping)
            and _text(candidate.get("node_id")) == binding.node_id
        ]
        if len(matches) != 1:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel success WorkOrder node binding is ambiguous: {binding.node_id}",
            )
        work_order_route = _text(matches[0].get("worker_route")).lower()
        work_order_target = _text(matches[0].get("target_id"))
        if (
            work_order_route != frozen.kind
            or work_order_target != expected_target
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel success WorkOrder route binding drifted: {binding.node_id}",
            )
    return frozen.kind, expected_target




















def _canonical_parallel_outcome_reason_code(status: str) -> str | None:
    normalized = _text(status).upper()
    reasons = {
        "ACCEPTED": None,
        "BUDGET_BLOCKED": GOVERNED_BUDGET_EXCEEDED,
        "FAILED": "GOVERNED_PARALLEL_NODE_FAILED",
        "VERIFICATION_FAILED": "GOVERNED_PARALLEL_VERIFICATION_FAILED",
        "WAITING_ACCEPTANCE_REVIEW": GOVERNED_REVIEW_REQUIRED,
        "RETRY_PENDING": GOVERNED_EXECUTION_IN_PROGRESS,
        "CANCELLED": "GOVERNED_PARALLEL_NODE_CANCELLED",
    }
    if normalized not in reasons:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"parallel decision outcome status is unsupported: {normalized}",
        )
    return reasons[normalized]


















def _retry_attempt_available(
    node: Mapping[str, Any],
    *,
    attempt_no: int,
) -> bool:
    payload = node.get("node")
    retry_policy = payload.get("retry_policy") if isinstance(payload, Mapping) else None
    max_attempts = retry_policy.get("max_attempts") if isinstance(retry_policy, Mapping) else None
    if (
        isinstance(attempt_no, bool)
        or not isinstance(attempt_no, int)
        or attempt_no < 1
        or isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts < 1
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "durable TaskNode has an invalid retry attempt policy",
        )
    return attempt_no < max_attempts


def _retry_usage_id(
    *,
    budget_id: str,
    attempt_id: str,
    node_only: bool = False,
) -> str:
    material = f"{_text(budget_id)}\n{_text(attempt_id)}\nretry"
    if node_only:
        material += "\nnode-only"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"budget-usage://{digest}"


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






def _exception_reason(exc: BaseException, default: str) -> str:
    return _text(getattr(exc, "reason_code", None)) or _text(default) or exc.__class__.__name__.upper()


def _heartbeat_failure_phase(exc: BaseException) -> str:
    if isinstance(exc, DurableExecutionHeartbeatError):
        return _text(exc.phase) or "renewal"
    return _text(getattr(exc, "_heartbeat_phase", None)) or "lifecycle"


async def _stop_heartbeat_after_interruption(
    heartbeat: DurableExecutionHeartbeat,
) -> None:
    """Best-effort join without replacing the caller's original failure."""

    try:
        await heartbeat.stop()
    except BaseException:
        return


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
