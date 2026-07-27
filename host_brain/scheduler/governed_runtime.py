"""Governed application boundary for durable scheduler occurrences.

This module deliberately reuses ``GovernedCommandApplication`` and
``GovernedTaskCoordinator``.  It owns scheduler-specific policy, contract
construction and replay, but none of the Task Runtime state machine.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
import hashlib
import inspect
import json
import re
from typing import Any

from natsume_contracts.task import HostCommand, HostCommandSurface, build_host_command

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphCompletionPolicy,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetExceededAction,
    BudgetPolicy,
)
from host_brain.contracts.agent_runtime.control_route_execution_policy import (
    CONTROL_ROUTE_BUDGET_MODE,
    CONTROL_ROUTE_EXECUTION_POLICY_KEY,
    ControlRouteExecutionPolicyError,
    freeze_control_route_execution_policy,
    require_control_route_execution_policy,
)
from host_brain.persistence.task_repository import OutboxMessage
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.contracts.agent_runtime.work_order_replay import (
    normalized_work_order_replay_payload,
)
from host_brain.orchestration import (
    DurableGovernorGrantResolver,
    GovernedCommandApplication,
    GovernorGrantError,
)
from host_brain.orchestration.node_grants import (
    DurableNodeGrantVerifier,
    NodeBoundGrantService,
)
from host_brain.scheduler.command_service import (
    ClaimResultStorePort,
    SCHEDULER_COMMAND_REPLAY,
    SCHEDULER_RUNTIME_AUTHORITY_INVALID,
    SCHEDULER_RUNTIME_DEFINITION_DRIFT,
    SCHEDULER_RUNTIME_DEFINITION_MISSING,
    SCHEDULER_RUNTIME_DRAIN_TIMEOUT,
    SCHEDULER_RUNTIME_GOVERNOR_GRANT_MISSING,
    SCHEDULER_RUNTIME_GOVERNOR_REJECTED,
    SCHEDULER_RUNTIME_INACTIVE,
    SCHEDULER_RUNTIME_INGRESS_COMPLETION_FAILED,
    SCHEDULER_RUNTIME_MODE_BLOCKED,
    SCHEDULER_RUNTIME_RESOURCE_BLOCKED,
    SCHEDULER_RUNTIME_TASK_DISABLED,
    GovernedSchedulerRuntimeError,
    SchedulerCommandService,
    SchedulerDefinitionRepositoryPort,
    _build_governor_intent,
    _require_scheduler_command_surface,
    _scheduler_session_id,
)
from host_brain.scheduler.accepted_effect_runtime import (
    SchedulerAcceptedEffectRuntime,
)
from host_brain.scheduler.contracts import (
    SchedulerDefinitionSnapshot,
    SchedulerOccurrence,
    occurrence_work_order_id,
)
from host_brain.scheduler.operation_plan import (
    SCHEDULER_OPERATION_EFFECT_TYPE,
    SchedulerOperationPlanAcceptance,
    SchedulerOperationPlanExecutor,
    SchedulerOperationPlanVerifier,
)
from host_brain.scheduler.worker_registry import (
    SchedulerWorkerRegistry,
    SchedulerWorkerResolver,
)
from host_brain.task_runtime import GovernedTaskCoordinator
from host_brain.task_runtime.governed_workers import GovernedFrozenNodeWorker
from host_brain.task_runtime.review_runtime import TaskNodeReviewRuntime

_DEFAULT_DRAIN_TIMEOUT_S = 120.0
_SCHEDULER_BUDGET_LIMITS = BudgetAmount(
    token_count=32_768,
    tool_call_count=32,
    time_ms=300_000,
    concurrency=1,
    retry_count=8,
    model_cost_micros=1_000_000,
)
_STABLE_COMMAND_FIELDS = (
    "schema_version",
    "command_id",
    "surface",
    "request_id",
    "session_id",
    "principal_id",
    "ingress_claim_ref",
    "ingress_audit_ref",
    "input_ref",
    "input_sha256",
    "root_work_order_id",
    "graph_id",
    "graph_revision",
    "route_type",
    "target_id",
    "business_scope",
    "operation_fingerprint",
)
_DEFAULT_MODE_PRIORITY = {
    "normal": frozenset({"HIGH", "MEDIUM", "LOW"}),
    "sleep": frozenset({"HIGH", "MEDIUM"}),
    "gaming": frozenset({"HIGH"}),
    "focus": frozenset({"HIGH"}),
    "maintenance": frozenset(),
    "locked": frozenset(),
}

class SchedulerDefinitionPolicyEvaluator:
    """Evaluate server-owned definition policy before Governor and worker."""

    def __init__(
        self,
        *,
        current_mode: Callable[[], str],
        resource_gate: Callable[
            [SchedulerDefinitionSnapshot, str], Mapping[str, Any] | bool
        ]
        | None = None,
    ) -> None:
        if not callable(current_mode):
            raise TypeError("current_mode must be callable")
        if resource_gate is not None and not callable(resource_gate):
            raise TypeError("resource_gate must be callable")
        self._current_mode = current_mode
        self._resource_gate = resource_gate

    def require_allowed(self, definition: SchedulerDefinitionSnapshot) -> str:
        if not definition.enabled:
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_TASK_DISABLED,
                f"scheduler task is disabled: {definition.task_id}",
            )
        mode = str(self._current_mode() or "").strip().lower()
        if mode not in _DEFAULT_MODE_PRIORITY:
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_MODE_BLOCKED,
                f"unknown runtime mode: {mode or '<empty>'}",
            )
        allowlist = definition.mode_allowlist
        allowed = (
            mode in allowlist
            if allowlist is not None
            else definition.priority in _DEFAULT_MODE_PRIORITY[mode]
        )
        if not allowed:
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_MODE_BLOCKED,
                f"task {definition.task_id} is blocked in mode {mode}",
            )
        # Force all frozen policy fields through their validators here.  The
        # optional dynamic gate receives only the durable snapshot.
        definition.retry_policy
        definition.worker_key
        resource_profile = definition.resource_profile
        if self._resource_gate is not None:
            raw = self._resource_gate(definition, mode)
            if isinstance(raw, Mapping):
                resource_allowed = bool(raw.get("allowed", raw.get("decision") == "allow"))
            else:
                resource_allowed = raw is True
            if not resource_allowed:
                raise GovernedSchedulerRuntimeError(
                    SCHEDULER_RUNTIME_RESOURCE_BLOCKED,
                    f"resource policy blocked task {definition.task_id}",
                )
        elif resource_profile.get("requires_explicit_gate") is True:
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_RESOURCE_BLOCKED,
                f"task {definition.task_id} requires a composition resource gate",
            )
        return mode

class GovernedSchedulerRuntime:
    """Lifecycle-owned facade around the shared governed command application."""

    def __init__(
        self,
        *,
        application: GovernedCommandApplication,
        runtime_router: CanonicalRuntimeRouter,
        command_repository: Any,
        governor_grants: DurableGovernorGrantResolver,
        runtime_context_application: Any | None = None,
        accepted_effect_runtime: SchedulerAcceptedEffectRuntime,
        drain_timeout_s: float = _DEFAULT_DRAIN_TIMEOUT_S,
    ) -> None:
        self._application = application
        self._runtime_router = runtime_router
        self._commands = command_repository
        self._governor_grants = governor_grants
        self._runtime_context_application = runtime_context_application
        if not isinstance(accepted_effect_runtime, SchedulerAcceptedEffectRuntime):
            raise TypeError("accepted_effect_runtime must be SchedulerAcceptedEffectRuntime")
        self._accepted_effect_runtime = accepted_effect_runtime
        self._drain_timeout_s = max(0.1, float(drain_timeout_s))
        self._condition = asyncio.Condition()
        self._active = False
        self._accepting = False
        self._in_flight = 0

    async def activate(self) -> None:
        async with self._condition:
            self._active = True
            self._accepting = True

    async def close_admission(self) -> None:
        async with self._condition:
            self._accepting = False

    async def drain(self) -> None:
        async def _wait() -> None:
            async with self._condition:
                await self._condition.wait_for(lambda: self._in_flight == 0)

        try:
            await asyncio.wait_for(_wait(), timeout=self._drain_timeout_s)
        except TimeoutError as exc:
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_DRAIN_TIMEOUT,
                f"scheduler runtime still has {self._in_flight} in-flight command(s)",
            ) from exc

    async def aclose(self) -> None:
        await self.close_admission()
        await self.drain()
        async with self._condition:
            self._active = False

    async def execute_stream(
        self,
        *,
        occurrence: SchedulerOccurrence,
        definition: SchedulerDefinitionSnapshot,
        authority: Mapping[str, Any],
        governor_grant: str,
        received_at_ms: int,
        command_surface: HostCommandSurface = HostCommandSurface.SCHEDULER,
    ) -> AsyncIterator[dict[str, Any]]:
        async with self._condition:
            if not self._active or not self._accepting:
                raise GovernedSchedulerRuntimeError(
                    SCHEDULER_RUNTIME_INACTIVE,
                    "scheduler runtime is not accepting commands",
                )
            self._in_flight += 1
        try:
            surface = _require_scheduler_command_surface(command_surface)
            request_id = str(authority.get("request_id") or "").strip()
            try:
                self._governor_grants.require(
                    request_id=request_id,
                    expected_ref=governor_grant,
                )
            except GovernorGrantError as exc:
                raise GovernedSchedulerRuntimeError(
                    SCHEDULER_RUNTIME_GOVERNOR_GRANT_MISSING,
                    exc.detail,
                ) from exc
            existing = self._commands.get_by_request(
                surface=surface.value,
                request_id=request_id,
            )
            if existing is None:
                frozen_runtime_context = self._freeze_runtime_context(
                    occurrence=occurrence,
                    definition=definition,
                    authority=authority,
                    received_at_ms=received_at_ms,
                )
            else:
                existing_order = existing.get("work_order")
                existing_input = getattr(existing_order, "input_pack", None)
                if not isinstance(existing_input, Mapping):
                    raise GovernedSchedulerRuntimeError(
                        SCHEDULER_RUNTIME_AUTHORITY_INVALID,
                        "durable scheduler replay lacks a frozen input pack",
                    )
                frozen_runtime_context = _require_existing_runtime_context(
                    existing_input,
                    runtime_context_application=self._runtime_context_application,
                )
            candidate_command, candidate_order, candidate_graph = _build_contracts(
                occurrence=occurrence,
                definition=definition,
                authority=authority,
                governor_grant=governor_grant,
                received_at_ms=received_at_ms,
                frozen_runtime_context=frozen_runtime_context,
                command_surface=surface,
            )
            if existing is None:
                command = candidate_command
                work_order = candidate_order
                graph = candidate_graph
                assignment = self._runtime_router.assign_new_task(
                    work_order.work_order_id,
                    assigned_at_ms=received_at_ms,
                )
            else:
                command = existing.get("command")
                work_order = existing.get("work_order")
                assignment = existing.get("assignment")
                graph = existing.get("graph")
                if not isinstance(command, HostCommand):
                    raise GovernedSchedulerRuntimeError(
                        SCHEDULER_RUNTIME_AUTHORITY_INVALID,
                        "durable scheduler replay lacks a typed HostCommand",
                    )
                _assert_stable_replay(candidate_command, command)
                if not isinstance(work_order, WorkOrderV3):
                    raise GovernedSchedulerRuntimeError(
                        SCHEDULER_RUNTIME_AUTHORITY_INVALID,
                        "durable scheduler replay has no typed WorkOrderV3",
                    )
                _assert_stable_work_order_replay(
                    candidate_order,
                    work_order,
                )
                if assignment is None or graph is None:
                    raise GovernedSchedulerRuntimeError(
                        SCHEDULER_RUNTIME_AUTHORITY_INVALID,
                        "durable scheduler replay is incomplete",
                    )
            _require_scheduler_control_policy(
                command=command,
                work_order=work_order,
                graph=graph,
            )
            async for event in self._application.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            ):
                if bool(event.get("done")):
                    yield await self._accepted_effect_runtime.dispatch(
                        event,
                        command=command,
                    )
                else:
                    yield event
        finally:
            async with self._condition:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._condition.notify_all()

    def _freeze_runtime_context(
        self,
        *,
        occurrence: SchedulerOccurrence,
        definition: SchedulerDefinitionSnapshot,
        authority: Mapping[str, Any],
        received_at_ms: int,
    ) -> dict[str, Any]:
        service = self._runtime_context_application
        freeze = getattr(service, "freeze_request", None)
        if not callable(freeze):
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_AUTHORITY_INVALID,
                "Scheduler Runtime Context authority is unavailable",
            )
        template = definition.intent_template
        template_args = (
            dict(template.get("args") or {})
            if isinstance(template.get("args"), Mapping)
            else {}
        )
        person_id = str(
            template_args.get("person_id")
            or template_args.get("user_id")
            or occurrence.principal_id
            or authority.get("principal_id")
            or "scheduler"
        ).strip() or "scheduler"
        skill_name = str(template.get("skill_name") or definition.task_id).strip()
        try:
            frozen = freeze(
                request_body=None,
                person_id=person_id,
                ts_ms=int(received_at_ms),
                intent_draft={
                    "route": "scheduler",
                    "task_kind": "PROACTIVE"
                    if skill_name == "proactive_talk"
                    else "SCHEDULER",
                    "target_id": skill_name,
                    "task": f"Execute scheduled task {occurrence.task_id}",
                },
                governor_result={
                    "risk_level": max(
                        1,
                        min(
                            5,
                            int(definition.definition.get("risk_level") or 1),
                        ),
                    ),
                },
                command_authority=dict(authority),
                emotion_context=None,
                emotion_snapshot=None,
                bond_summary=None,
            )
            payload = frozen.to_dict()
            return _require_existing_runtime_context(
                payload,
                runtime_context_application=service,
            )
        except Exception as exc:
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_AUTHORITY_INVALID,
                "scheduler runtime context could not be frozen",
            ) from exc

    async def continue_review_admission(
        self,
        record: Mapping[str, Any],
        claim: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Re-enter one exact durable Scheduler admission after Review."""

        async with self._condition:
            if not self._active or not self._accepting:
                raise GovernedSchedulerRuntimeError(
                    SCHEDULER_RUNTIME_INACTIVE,
                    "scheduler runtime is not accepting Review continuation",
                )
            self._in_flight += 1
        try:
            durable = dict(record or {})
            command = durable.get("command")
            work_order = durable.get("work_order")
            assignment = durable.get("assignment")
            graph = durable.get("graph")
            if (
                not isinstance(command, HostCommand)
                or command.surface
                not in {
                    HostCommandSurface.SCHEDULER,
                    HostCommandSurface.OPERATOR,
                }
                or not isinstance(work_order, WorkOrderV3)
                or assignment is None
                or graph is None
            ):
                raise GovernedSchedulerRuntimeError(
                    SCHEDULER_RUNTIME_AUTHORITY_INVALID,
                    "Review continuation Scheduler admission is incomplete or misrouted",
                )
            _require_scheduler_control_policy(
                command=command,
                work_order=work_order,
                graph=graph,
            )
            operation = self._application.execute_review_continuation(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
                continuation_claim=dict(claim or {}),
            )
            result = await _review_continuation_result(operation)
            return await self._accepted_effect_runtime.dispatch(
                result,
                command=command,
            )
        finally:
            async with self._condition:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._condition.notify_all()

    async def continue_operation_reacquisition(
        self,
        record: Mapping[str, Any],
        _operation: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Re-enter one exact Scheduler admission after signed no-effect recovery."""

        return await self.continue_startup_admission(record)

    async def continue_startup_admission(
        self,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Re-enter one exact Scheduler-owned admission selected by a survivor."""

        async with self._condition:
            if not self._active or not self._accepting:
                raise GovernedSchedulerRuntimeError(
                    SCHEDULER_RUNTIME_INACTIVE,
                    "scheduler runtime is not accepting startup recovery",
                )
            self._in_flight += 1
        try:
            durable = dict(record or {})
            command = durable.get("command")
            work_order = durable.get("work_order")
            assignment = durable.get("assignment")
            graph = durable.get("graph")
            if (
                not isinstance(command, HostCommand)
                or command.surface
                not in {
                    HostCommandSurface.SCHEDULER,
                    HostCommandSurface.OPERATOR,
                }
                or not isinstance(work_order, WorkOrderV3)
                or assignment is None
                or graph is None
            ):
                raise GovernedSchedulerRuntimeError(
                    SCHEDULER_RUNTIME_AUTHORITY_INVALID,
                    "Operation recovery Scheduler admission is incomplete or misrouted",
                )
            _require_scheduler_control_policy(
                command=command,
                work_order=work_order,
                graph=graph,
            )
            result = await _review_continuation_result(
                self._application.execute_stream(
                    command=command,
                    work_order=work_order,
                    assignment=assignment,
                    graph=graph,
                )
            )
            return await self._accepted_effect_runtime.dispatch(
                result,
                command=command,
            )
        finally:
            async with self._condition:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._condition.notify_all()

    async def request_graph_cancellation(
        self,
        record: Mapping[str, Any],
        *,
        authority: Mapping[str, Any],
        source: str,
        reason_code: str,
    ) -> dict[str, Any]:
        """Cancel one exact Scheduler admission through verified authority."""

        async with self._condition:
            if not self._active or not self._accepting:
                raise GovernedSchedulerRuntimeError(
                    SCHEDULER_RUNTIME_INACTIVE,
                    "scheduler runtime is not accepting cancellation commands",
                )
            self._in_flight += 1
        try:
            command = dict(record or {}).get("command")
            if (
                not isinstance(command, HostCommand)
                or command.surface
                not in {
                    HostCommandSurface.SCHEDULER,
                    HostCommandSurface.OPERATOR,
                }
            ):
                raise GovernedSchedulerRuntimeError(
                    SCHEDULER_RUNTIME_AUTHORITY_INVALID,
                    "cancellation admission is not owned by the Scheduler runtime",
                )
            return await self._application.request_graph_cancellation(
                command_id=command.command_id,
                graph_id=command.graph_id,
                graph_revision=command.graph_revision,
                authority=dict(authority or {}),
                source=source,
                reason_code=reason_code,
            )
        finally:
            async with self._condition:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._condition.notify_all()

    def existing_command(
        self,
        *,
        request_id: str,
        command_surface: HostCommandSurface = HostCommandSurface.SCHEDULER,
    ) -> Mapping[str, Any] | None:
        surface = _require_scheduler_command_surface(command_surface)
        return self._commands.get_by_request(
            surface=surface.value,
            request_id=str(request_id or "").strip(),
        )

    def health(self) -> dict[str, Any]:
        return {
            "active": self._active,
            "accepting": self._accepting,
            "in_flight": self._in_flight,
        }

def build_governed_scheduler_runtime(
    *,
    task_runtime_container: Any,
    ledger: Any,
    token_service: Any,
    result_repository: Any,
    runtime_router: CanonicalRuntimeRouter,
    worker_registry: SchedulerWorkerRegistry,
    owner_id: str,
    definition_repository: SchedulerDefinitionRepositoryPort | None = None,
    cancellation_authority_verifier: Any | None = None,
    runtime_context_application: Any | None = None,
    action_preview_service: Any | None = None,
    acceptance_effect_dispatcher: Any | None = None,
) -> GovernedSchedulerRuntime:
    definitions = definition_repository or getattr(
        task_runtime_container,
        "scheduler_definition_repository",
        None,
    )
    if definitions is None:
        raise TypeError("scheduler definition repository is required")
    if not isinstance(worker_registry, SchedulerWorkerRegistry):
        raise TypeError("scheduler worker registry is required")
    if not callable(getattr(runtime_context_application, "freeze_request", None)) or (
        not callable(getattr(runtime_context_application, "require_frozen", None))
    ):
        raise TypeError("scheduler Runtime Context authority is required")
    if not callable(getattr(action_preview_service, "issue_receipt", None)) or (
        not callable(getattr(action_preview_service, "require_execution", None))
    ):
        raise TypeError("scheduler Action Preview authority is required")
    review_store = getattr(task_runtime_container, "review_store", None)
    continuation_store = getattr(
        task_runtime_container,
        "review_continuation_store",
        None,
    )
    if review_store is None or continuation_store is None:
        raise TypeError("scheduler Task Runtime Review stores are required")
    node_grant_service = NodeBoundGrantService(
        ledger=ledger,
        token_service=token_service,
        review_store=continuation_store,
    )
    node_grant_verifier = DurableNodeGrantVerifier(
        ledger=ledger,
        review_store=continuation_store,
    )
    projection_store = getattr(
        task_runtime_container,
        "worker_projection_store",
        None,
    )
    if projection_store is None:
        raise TypeError("scheduler Task Runtime worker projection store is required")
    canonical_worker = GovernedFrozenNodeWorker(
        route_kind="delegate",
        executor=SchedulerOperationPlanExecutor(
            occurrence_repository=definitions,
            definition_repository=definitions,
        ),
        node_grants=node_grant_verifier,
        projections=projection_store,
        action_preview_service=action_preview_service,
        allowed_acceptance_effect_types=frozenset(
            {SCHEDULER_OPERATION_EFFECT_TYPE}
        ),
        preview_purpose="scheduler.execute",
    )
    verifier = SchedulerOperationPlanVerifier(
        projection_store=projection_store,
        occurrence_repository=definitions,
        definition_repository=definitions,
    )
    acceptance = SchedulerOperationPlanAcceptance(
        projection_store=projection_store,
        occurrence_repository=definitions,
        definition_repository=definitions,
    )
    command_repository = task_runtime_container.command_repository
    coordinator = GovernedTaskCoordinator(
        task_store=task_runtime_container.task_store,
        internal_agent_checkpoint_uow=(
            task_runtime_container.internal_agent_checkpoint_uow
        ),
        outbox_publisher=task_runtime_container.outbox_publisher,
        outbox_factory=OutboxMessage,
        worker_resolver=SchedulerWorkerResolver(canonical_worker=canonical_worker),
        verifier=verifier,
        acceptance=acceptance,
        projection_reader=projection_store,
        review_runtime=TaskNodeReviewRuntime.for_task_store(
            review_store,
            task_runtime_container.task_store,
        ),
        node_grant_verifier=node_grant_verifier,
        acceptance_effects_enabled=True,
        atomic_graph_success_enabled=True,
        execution_registry=getattr(
            task_runtime_container,
            "graph_execution_registry",
            None,
        ),
        owner_id=owner_id,
    )
    application = GovernedCommandApplication(
        command_repository=command_repository,
        coordinator=coordinator,
        node_grant_service=node_grant_service,
        cancellation_authority_verifier=cancellation_authority_verifier,
    )
    accepted_effect_runtime = SchedulerAcceptedEffectRuntime(
        dispatcher=acceptance_effect_dispatcher,
        result_repository=result_repository,
        occurrence_repository=definitions,
    )
    return GovernedSchedulerRuntime(
        application=application,
        runtime_router=runtime_router,
        command_repository=command_repository,
        governor_grants=DurableGovernorGrantResolver(ledger),
        runtime_context_application=runtime_context_application,
        accepted_effect_runtime=accepted_effect_runtime,
    )

def _build_contracts(
    *,
    occurrence: SchedulerOccurrence,
    definition: SchedulerDefinitionSnapshot,
    authority: Mapping[str, Any],
    governor_grant: str,
    received_at_ms: int,
    frozen_runtime_context: Mapping[str, Any] | None = None,
    command_surface: HostCommandSurface = HostCommandSurface.SCHEDULER,
) -> tuple[HostCommand, WorkOrderV3, TaskGraph]:
    surface = _require_scheduler_command_surface(command_surface)
    if occurrence.task_id != definition.task_id or (
        occurrence.definition_revision != definition.definition_revision
    ):
        raise GovernedSchedulerRuntimeError(
            SCHEDULER_RUNTIME_DEFINITION_DRIFT,
            "occurrence and definition revision differ",
        )
    request_id = str(authority.get("request_id") or "").strip()
    principal_id = str(authority.get("principal_id") or "").strip()
    claim_ref = str(authority.get("claim_ref") or "").strip()
    audit_ref = str(authority.get("audit_ref") or "").strip()
    suffix = hashlib.sha256(occurrence.occurrence_id.encode("utf-8")).hexdigest()[:40]
    graph_id = f"graph-scheduler-{suffix}"
    node_id = f"node-scheduler-{suffix}"
    work_order_id = occurrence_work_order_id(occurrence.occurrence_id)
    definition_ref = _definition_ref(definition)
    retry_policy = definition.retry_policy
    risk_level = max(1, min(5, int(definition.definition.get("risk_level") or 1)))
    worker_route = f"delegate:{definition.worker_key}"
    business_scope = (
        f"operator:scheduler.task.trigger:{_scope_token(occurrence.task_id)}"
        if surface is HostCommandSurface.OPERATOR
        else f"scheduler:{_scope_token(occurrence.task_id)}"
    )
    fingerprint_material = {
            "definition_revision": definition.definition_revision,
            "occurrence_id": occurrence.occurrence_id,
            "payload_sha256": occurrence.payload_sha256,
            "worker_key": definition.worker_key,
    }
    if surface is HostCommandSurface.OPERATOR:
        fingerprint_material["command_surface"] = surface.value
        fingerprint_material["operator_command_id"] = "scheduler.task.trigger"
    operation_fingerprint = _sha256_json(fingerprint_material)
    output_contract = {
        "type": "scheduler_operation_plan",
        "required": ["acceptance_effect_intents", "receipt_ref"],
    }
    operator_surface = surface is HostCommandSurface.OPERATOR
    review_ref = (
        f"task-review-policy://operator/scheduler-task-trigger/{suffix}"
        if operator_surface
        else f"review-waiver://governor-grant/{suffix}"
    )
    control_command_id = (
        "scheduler.task.trigger"
        if operator_surface
        else "scheduler.occurrence.execute"
    )
    control_policy = freeze_control_route_execution_policy(
        surface=surface,
        control_command_id=control_command_id,
        target_id=definition.worker_key,
        review_required=operator_surface,
    )
    budget_policy = BudgetPolicy(
        scope=f"work-order:{work_order_id}",
        limits=_SCHEDULER_BUDGET_LIMITS,
        on_exceeded=BudgetExceededAction.BLOCK,
        metadata={
            "policy": "governed_scheduler_default",
            "policy_version": "governed_scheduler_budget.v1",
            "route_type": "delegate",
            "fail_closed": True,
            "source": "scheduler_runtime_composition",
            "measurement_mode": CONTROL_ROUTE_BUDGET_MODE,
            "control_policy_ref": control_policy["policy_ref"],
        },
    )
    budget_policy.validate()
    work_order = WorkOrderV3(
        work_order_id=work_order_id,
        domain="organ_ops",
        run_mode="worker",
        objective=f"Prepare durable scheduled operation {occurrence.task_id}",
        input_pack={
            "occurrence_id": occurrence.occurrence_id,
            "definition_ref": definition_ref,
            **dict(frozen_runtime_context or {}),
            CONTROL_ROUTE_EXECUTION_POLICY_KEY: dict(control_policy),
        },
        allowed_actions=(f"delegate:{definition.worker_key}",),
        forbidden_actions=(
            "change_scheduler_definition",
            "bypass_governor",
            "reroute",
            "replan",
            "cross_executor_resume",
        ),
        acceptance_criteria=("scheduler operation plan is canonical",),
        verification_plan={"required": True, "type": "scheduler_operation_plan"},
        approval_policy={
            "required": True,
            "governor_grant_ref": governor_grant,
            "on_missing": "BLOCK",
        },
        review_policy={
            **(
                {
                    "review_required": True,
                    "durable_review_required": True,
                    "mode": "operator_command_pre_execution",
                    "reason": "authenticated_operator_write",
                    "command_id": "scheduler.task.trigger",
                    "side_effects": [f"delegate:{definition.worker_key}"],
                    "evidence_refs": [
                        claim_ref,
                        audit_ref,
                        governor_grant,
                    ],
                }
                if operator_surface
                else {
                    "review_required": False,
                    "durable_review_required": False,
                    "mode": "governor_grant_review_waiver",
                    "reason": "approved_scheduler_occurrence",
                    "waiver_authority": "governor_grant",
                    "waiver_ref": review_ref,
                }
            ),
        },
        metadata={
            "definition_revision": definition.definition_revision,
            "priority": definition.priority,
            "worker_key": definition.worker_key,
            "resource_profile": definition.resource_profile,
            "retry_policy": retry_policy,
            "risk_level": risk_level,
            "command_surface": surface.value,
            "control_command_id": control_command_id,
            CONTROL_ROUTE_EXECUTION_POLICY_KEY: dict(control_policy),
            "operator_command_id": (
                "scheduler.task.trigger" if operator_surface else None
            ),
            "planning_lineage": {
                "graph_id": graph_id,
                "root_work_order_id": work_order_id,
                "revision": 1,
                "parent_revision": None,
                "approval_ref": governor_grant,
                "review_ref": review_ref,
                "node_bindings": [
                    {
                        "node_id": node_id,
                        "objective": (
                            f"Prepare durable scheduled operation {occurrence.task_id}"
                        ),
                        "worker_route": "delegate",
                        "target_id": definition.worker_key,
                    }
                ],
            },
        },
        root_work_order_id=work_order_id,
        parent_work_order_id=None,
        task_graph_id=graph_id,
        task_node_id=node_id,
        graph_revision=1,
        attempt=0,
        capability_requirements=(definition.worker_key,),
        risk_level=risk_level,
        input_refs=(
            occurrence.occurrence_id,
            definition_ref,
            governor_grant,
            control_policy["policy_ref"],
        ),
        output_contract=output_contract,
        budget_policy=budget_policy,
        business_idempotency_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
        status_version=0,
        approval_ref=governor_grant,
        review_ref=review_ref,
    )
    work_order.validate()
    node = TaskNode(
        node_id=node_id,
        dependencies=(),
        route=worker_route,
        input_refs=(
            occurrence.occurrence_id,
            definition_ref,
            governor_grant,
            control_policy["policy_ref"],
        ),
        output_contract=output_contract,
        retry_policy=retry_policy,
        risk_level=risk_level,
        acceptance_criteria=("scheduler operation plan is canonical",),
        status=TaskNodeStatus.READY,
        status_version=0,
    )
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id=work_order_id,
        revision=1,
        planner_ref=f"planner://governed-scheduler/{suffix}",
        status=TaskGraphStatus.CREATED,
        created_at=int(received_at_ms),
        result_node_id=node_id,
        completion_policy=TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1,
        nodes=(node,),
    )
    command = build_host_command(
        surface=surface,
        request_id=request_id,
        session_id=_scheduler_session_id(occurrence.occurrence_id),
        principal_id=principal_id,
        ingress_claim_ref=claim_ref,
        ingress_audit_ref=audit_ref,
        governor_grant_ref=governor_grant,
        input_ref=occurrence.occurrence_id,
        input_sha256=f"sha256:{str(authority['body_sha256']).removeprefix('sha256:')}",
        root_work_order_id=work_order_id,
        graph_id=graph_id,
        graph_revision=1,
        route_type="delegate",
        target_id=definition.worker_key,
        business_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
        created_at_ms=int(received_at_ms),
    )
    return command, work_order, graph

def _require_existing_runtime_context(
    input_pack: Mapping[str, Any],
    *,
    runtime_context_application: Any | None,
) -> dict[str, Any]:
    """Reopen the exact admitted context without resolving a newer Scene."""

    payload = dict(input_pack or {})
    if payload.get("runtime_context_schema") is None:
        return {}
    require = getattr(runtime_context_application, "require_frozen", None)
    if not callable(require):
        raise GovernedSchedulerRuntimeError(
            SCHEDULER_RUNTIME_AUTHORITY_INVALID,
            "durable scheduler context cannot be verified without its app-local authority",
        )
    try:
        frozen = require(payload)
        exact = frozen.to_dict()
    except Exception as exc:
        raise GovernedSchedulerRuntimeError(
            SCHEDULER_RUNTIME_AUTHORITY_INVALID,
            "durable scheduler runtime context is missing or drifted",
        ) from exc
    return {
        key: value
        for key, value in exact.items()
        if key not in {"occurrence_id", "definition_ref"}
    }

def _assert_stable_replay(candidate: HostCommand, durable: HostCommand) -> None:
    left = candidate.to_dict()
    right = durable.to_dict()
    drift = [field for field in _STABLE_COMMAND_FIELDS if left.get(field) != right.get(field)]
    if drift:
        raise GovernedSchedulerRuntimeError(
            SCHEDULER_RUNTIME_DEFINITION_DRIFT,
            "scheduler replay changed stable semantics: " + ", ".join(drift),
        )


def _assert_stable_work_order_replay(
    candidate: WorkOrderV3,
    durable: WorkOrderV3,
) -> None:
    candidate_payload = normalized_work_order_replay_payload(candidate)
    durable_payload = normalized_work_order_replay_payload(durable)
    drift = [
        field_name
        for field_name in candidate_payload
        if candidate_payload.get(field_name)
        != durable_payload.get(field_name)
    ]
    if drift:
        raise GovernedSchedulerRuntimeError(
            SCHEDULER_RUNTIME_DEFINITION_DRIFT,
            "scheduler WorkOrder replay changed stable semantics: "
            + ", ".join(drift),
        )


def _require_scheduler_control_policy(
    *,
    command: HostCommand,
    work_order: WorkOrderV3,
    graph: TaskGraph,
) -> None:
    try:
        require_control_route_execution_policy(
            command=command,
            work_order=work_order,
            graph=graph,
        )
    except ControlRouteExecutionPolicyError as exc:
        raise GovernedSchedulerRuntimeError(
            SCHEDULER_RUNTIME_AUTHORITY_INVALID,
            exc.detail,
        ) from exc


async def _review_continuation_result(operation: Any) -> dict[str, Any]:
    current = await operation if inspect.isawaitable(operation) else operation
    if isinstance(current, Mapping):
        return dict(current)
    if not hasattr(current, "__aiter__"):
        raise GovernedSchedulerRuntimeError(
            SCHEDULER_RUNTIME_AUTHORITY_INVALID,
            "Review continuation returned no durable Scheduler result",
        )
    terminal: dict[str, Any] | None = None
    async for raw_event in current:
        if not isinstance(raw_event, Mapping):
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_AUTHORITY_INVALID,
                "Review continuation emitted a non-mapping Scheduler event",
            )
        event = dict(raw_event)
        if bool(event.get("done")):
            terminal = event
    if terminal is None:
        raise GovernedSchedulerRuntimeError(
            SCHEDULER_RUNTIME_AUTHORITY_INVALID,
            "Review continuation emitted no durable Scheduler terminal result",
        )
    return terminal

def _definition_ref(definition: SchedulerDefinitionSnapshot) -> str:
    return (
        f"scheduler-definition://{_ref_token(definition.task_id)}/"
        f"{definition.definition_revision}"
    )

def _scope_token(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9._:@/-]+", "-", str(value or "").strip()).strip("-")
    return text[:180] or "task"

def _ref_token(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9._~:@/-]+", "-", str(value or "").strip()).strip("-")
    return text[:180] or "scheduler"

def _sha256_json(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()

__all__ = [
    "SCHEDULER_COMMAND_REPLAY",
    "SCHEDULER_RUNTIME_AUTHORITY_INVALID",
    "SCHEDULER_RUNTIME_DEFINITION_DRIFT",
    "SCHEDULER_RUNTIME_DEFINITION_MISSING",
    "SCHEDULER_RUNTIME_DRAIN_TIMEOUT",
    "SCHEDULER_RUNTIME_GOVERNOR_GRANT_MISSING",
    "SCHEDULER_RUNTIME_GOVERNOR_REJECTED",
    "SCHEDULER_RUNTIME_INACTIVE",
    "SCHEDULER_RUNTIME_INGRESS_COMPLETION_FAILED",
    "SCHEDULER_RUNTIME_MODE_BLOCKED",
    "SCHEDULER_RUNTIME_RESOURCE_BLOCKED",
    "SCHEDULER_RUNTIME_TASK_DISABLED",
    "GovernedSchedulerRuntime",
    "GovernedSchedulerRuntimeError",
    "SchedulerCommandService",
    "SchedulerDefinitionPolicyEvaluator",
    "build_governed_scheduler_runtime",
]
