"""Governed Task Runtime adapter for authenticated internal Agent WorkOrders."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
import hashlib
import re
from typing import Any, Protocol

from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.accepted_effect_runtime import (
    InternalAcceptedEffectRuntime,
)
from host_brain.agent_runtime.internal_result_proposal import (
    INTERNAL_AGENT_RESULT_COMMIT_EFFECT_TYPE,
    InternalAgentProposalAcceptance,
    InternalAgentProposalExecutor,
    InternalAgentProposalVerifier,
)
from host_brain.agent_runtime.internal_artifact_terminal_reconciliation import (
    InternalArtifactTerminalContinuationWorker,
    InternalArtifactTerminalReconciler,
)
from host_brain.agent_runtime.skill_artifact_terminal_reconciliation import (
    AgentArtifactTerminalReconcilerGroup,
    GovernedSkillArtifactTerminalReconciler,
)
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.workspace_runtime import DurableWorkspaceRuntime
from host_brain.contracts.agent_runtime.internal_budget_claim_result import (
    build_internal_budget_rejection_result_ref,
)
from host_brain.contracts.agent_runtime.internal_nonacceptance_claim_result import (
    INTERNAL_NONACCEPTANCE_TERMINAL_REASONS,
    build_internal_nonacceptance_result_ref,
)
from host_brain.persistence.task_repository import OutboxMessage
from host_brain.contracts.agent_runtime.work_order import (
    AgentWorkOrder,
    WORK_ORDER_V3_SCHEMA_VERSION,
    WorkOrderV3,
)
from host_brain.app.governed_task_authority import (
    GovernedHostAdapterError,
    assert_stable_work_order_replay,
)
from host_brain.app.governed_control_route_policy import (
    require_governed_control_route_policy,
)
from host_brain.app.internal_task_contracts import (
    INTERNAL_RUNTIME_AUTHORITY_INVALID,
    INTERNAL_RUNTIME_DRAIN_TIMEOUT,
    INTERNAL_RUNTIME_GOVERNOR_GRANT_MISSING,
    INTERNAL_RUNTIME_GOVERNOR_REJECTED,
    INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
    INTERNAL_RUNTIME_INACTIVE,
    INTERNAL_RUNTIME_PRE_ADMITTED_WORK_ORDER_FORBIDDEN,
    INTERNAL_RUNTIME_STARTUP_CONTINUATION_DEFERRED,
    INTERNAL_TASK_NODE_ROUTE,
    GovernedInternalRuntimeError,
    build_internal_task_contracts as _build_contracts,
    identifier_or_digest as _identifier_or_digest,
    required_identifier as _required_identifier,
)
from host_brain.orchestration.governed_command import GovernedCommandApplication
from host_brain.orchestration.governor_grant import (
    DurableGovernorGrantResolver,
    GovernorGrantError,
    governor_grant_ref,
)
from host_brain.orchestration.node_grants import (
    DurableNodeGrantVerifier,
    NodeBoundGrantService,
)
from host_brain.persistence.agent_execution_repository import (
    AgentExecutionResultRepository,
)
from host_brain.task_runtime.coordinator import GovernedTaskCoordinator
from host_brain.task_runtime.review_runtime import TaskNodeReviewRuntime
from host_brain.task_runtime.governed_workers import GovernedFrozenNodeWorker
from host_brain.task_runtime.worker_registry import (
    GovernedWorkerRegistryError,
    GovernedWorkerRoute,
)
from host_brain.evolution.task_feedback import (
    decorate_task_acceptance,
)
from host_brain.app.internal_startup_checkpoint_continuation import (
    InternalStartupCheckpointContinuation,
)
from host_brain.app.internal_startup_admission import (
    collect_internal_continuation_result,
    execute_internal_startup_admission,
)
from host_brain.app.periodic_durable_continuation import (
    PeriodicDurableContinuation,
)
from natsume_contracts.task import HostCommand, HostCommandSurface

_DEFAULT_DRAIN_TIMEOUT_S = 120.0
_DEFAULT_SURVIVOR_INTERVAL_MS = 5_000
_INTERNAL_SURVIVOR_CONTINUATION_FAILED = (
    "GOVERNED_INTERNAL_SURVIVOR_CONTINUATION_FAILED"
)
_FORBIDDEN_DURABLE_KEY = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|credential|password|raw_?body|secret|signature|token)(?:$|_)",
    re.IGNORECASE,
)
_CALLER_AUTHORITY_KEYS = frozenset(
    {
        "approval_ref",
        "budget_policy",
        "business_idempotency_scope",
        "runtime_assignment",
        "runtime_owner_confirmation",
        "task_runtime_owner_confirmation",
        "confirmed_owner",
        "graph_revision",
        "lineage",
        "operation_fingerprint",
        "parent_work_order_id",
        "planning_lineage",
        "review_ref",
        "root_work_order_id",
        "task_graph_id",
        "task_node_id",
    }
)
_CALLER_APPROVAL_AUTHORITY_KEYS = frozenset(
    {
        "approval_ref",
        "confirmed",
        "governor_grant_ref",
        "review_ref",
        "review_resume_token_ref",
    }
)
_PRE_ADMITTED_WORK_ORDER_FIELDS = frozenset(
    {
        "root_work_order_id",
        "parent_work_order_id",
        "task_graph_id",
        "task_node_id",
        "graph_revision",
        "attempt",
        "capability_requirements",
        "input_refs",
        "output_contract",
        "budget_policy",
        "business_idempotency_scope",
        "operation_fingerprint",
        "status_version",
        "approval_ref",
        "review_ref",
    }
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


class IngressClaimResultStore(Protocol):
    def complete_claim(
        self,
        *,
        claim_ref: str,
        result_ref: str,
        at_ms: int | None = None,
    ) -> None: ...


class _AgentWorkerResolver:
    def __init__(self, *, canonical: Any) -> None:
        self._canonical = canonical

    def resolve(self, *, executor: str, worker_route: str) -> Any:
        route = _internal_delegate_route(worker_route)
        if route.canonical != INTERNAL_TASK_NODE_ROUTE:
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_AUTHORITY_INVALID,
                f"unsupported internal worker route: {worker_route}",
            )
        selected = str(executor or "").strip().lower()
        if selected == "canonical":
            return self._canonical
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            f"unsupported internal executor: {executor}",
        )
class GovernedInternalRuntime:
    """Lifecycle-owned application facade; HTTP routes receive it by closure."""

    def __init__(
        self,
        *,
        application: GovernedCommandApplication,
        runtime_router: CanonicalRuntimeRouter,
        command_repository: Any,
        governor_grants: DurableGovernorGrantResolver,
        runtime_context_application: Any,
        accepted_effect_runtime: InternalAcceptedEffectRuntime,
        terminal_continuation_worker: Any | None = None,
        startup_task_store: Any | None = None,
        startup_artifact_store: Any | None = None,
        startup_claim_result_store: Any | None = None,
        terminal_claim_task_store: Any | None = None,
        drain_timeout_s: float = _DEFAULT_DRAIN_TIMEOUT_S,
        survivor_interval_ms: int = _DEFAULT_SURVIVOR_INTERVAL_MS,
    ) -> None:
        self._application = application
        self._runtime_router = runtime_router
        self._commands = command_repository
        self._governor_grants = governor_grants
        if not callable(getattr(runtime_context_application, "freeze_request", None)) or (
            not callable(getattr(runtime_context_application, "require_frozen", None))
        ):
            raise TypeError("Internal Runtime Context authority is required")
        if not isinstance(accepted_effect_runtime, InternalAcceptedEffectRuntime):
            raise TypeError(
                "accepted_effect_runtime must be InternalAcceptedEffectRuntime"
            )
        self._runtime_context_application = runtime_context_application
        self._accepted_effect_runtime = accepted_effect_runtime
        if terminal_continuation_worker is not None:
            for method_name in ("run_once", "start", "wake", "stop", "join"):
                if not callable(getattr(terminal_continuation_worker, method_name, None)):
                    raise TypeError(
                        f"Internal artifact terminal worker lacks {method_name}"
                    )
        self._terminal_continuation_worker = terminal_continuation_worker
        self._terminal_worker_quiesced = terminal_continuation_worker is None
        terminal_claim_methods = (
            "resolve_internal_agent_budget_rejection_terminal",
            "try_resolve_internal_agent_nonacceptance_checkpoint_terminal",
            (
                "try_resolve_internal_agent_"
                "precheckpoint_cancellation_terminal"
            ),
            "get_graph_cancellation_for_graph",
            "get_graph_cancellation_decision_for_intent",
        )
        missing_terminal_claim_methods = (
            [
                method_name
                for method_name in terminal_claim_methods
                if not callable(getattr(terminal_claim_task_store, method_name, None))
            ]
            if terminal_claim_task_store is not None
            else []
        )
        if missing_terminal_claim_methods:
            raise TypeError(
                "Internal terminal claim TaskStore lacks: "
                + ",".join(missing_terminal_claim_methods)
            )
        self._terminal_claim_task_store = terminal_claim_task_store
        self._startup_checkpoint_continuation = InternalStartupCheckpointContinuation(
            task_store=startup_task_store,
            artifact_store=startup_artifact_store,
            claim_result_store=startup_claim_result_store,
            command_repository=command_repository,
            application=application,
            accepted_effect_runtime=accepted_effect_runtime,
            expected_node_route=INTERNAL_TASK_NODE_ROUTE,
            authority_invalid_code=INTERNAL_RUNTIME_AUTHORITY_INVALID,
            ingress_completion_failed_code=INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
            deferred_code=INTERNAL_RUNTIME_STARTUP_CONTINUATION_DEFERRED,
            error_factory=GovernedInternalRuntimeError,
        )
        self._checkpoint_survivor_continuation = (
            PeriodicDurableContinuation(
                runner=self._recover_startup_sealed_checkpoints,
                task_name="governed-internal-survivor-continuation",
                failure_reason_code=_INTERNAL_SURVIVOR_CONTINUATION_FAILED,
                interval_ms=int(survivor_interval_ms),
            )
            if startup_task_store is not None and startup_artifact_store is not None
            else None
        )
        self._drain_timeout_s = max(0.1, float(drain_timeout_s))
        self._condition = asyncio.Condition()
        self._activation_lock = asyncio.Lock()
        self._active = False
        self._accepting = False
        self._in_flight = 0

    async def activate(self) -> None:
        async with self._activation_lock:
            async with self._condition:
                if self._active:
                    return
            worker = self._terminal_continuation_worker
            if worker is not None:
                await asyncio.to_thread(worker.run_once)
            survivor = self._checkpoint_survivor_continuation
            if survivor is None:
                await self._recover_startup_sealed_checkpoints()
            else:
                await survivor.run_once()
            if worker is not None:
                await asyncio.to_thread(worker.run_once)
                worker.start()
                self._terminal_worker_quiesced = False
            if survivor is not None:
                await survivor.start()
            async with self._condition:
                self._active = True
                self._accepting = True

    async def close_admission(self) -> None:
        async with self._condition:
            self._accepting = False
        survivor = self._checkpoint_survivor_continuation
        if survivor is not None:
            survivor.request_stop()

    async def drain(self) -> None:
        async def _wait() -> None:
            async with self._condition:
                await self._condition.wait_for(lambda: self._in_flight == 0)

        try:
            await asyncio.wait_for(_wait(), timeout=self._drain_timeout_s)
        except TimeoutError as exc:
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_DRAIN_TIMEOUT,
                f"internal runtime still has {self._in_flight} in-flight command(s)",
            ) from exc
        survivor = self._checkpoint_survivor_continuation
        if survivor is not None:
            try:
                await survivor.join(timeout_s=self._drain_timeout_s)
            except RuntimeError as exc:
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_DRAIN_TIMEOUT,
                    "internal survivor continuation did not quiesce",
                ) from exc
        worker = self._terminal_continuation_worker
        if worker is not None and not self._terminal_worker_quiesced:
            worker.stop()
            await asyncio.to_thread(worker.join)
            await asyncio.to_thread(worker.run_once)
            self._terminal_worker_quiesced = True

    async def aclose(self) -> None:
        await self.close_admission()
        await self.drain()
        async with self._condition:
            self._active = False

    async def execute_stream(
        self,
        *,
        work_order: AgentWorkOrder | Mapping[str, Any],
        request_id: str,
        session_id: str,
        ts_ms: int,
        governor_grant_ref: str,
        command_authority: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        async with self._condition:
            if not self._active or not self._accepting:
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_INACTIVE,
                    "internal runtime is not accepting commands",
                )
            self._in_flight += 1
        try:
            try:
                self._governor_grants.require(
                    request_id=request_id,
                    expected_ref=governor_grant_ref,
                )
            except GovernorGrantError as exc:
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_GOVERNOR_GRANT_MISSING,
                    exc.detail,
                ) from exc
            legacy_order = _sanitize_work_order(work_order)
            existing = self._commands.get_by_request(
                surface=HostCommandSurface.INTERNAL.value,
                request_id=str(request_id or "").strip(),
            )
            if existing is not None:
                existing_command = existing.get("command")
                validate_claim = getattr(
                    self._commands,
                    "validate_ingress_claim_result",
                    None,
                )
                if isinstance(existing_command, HostCommand) and callable(validate_claim):
                    try:
                        existing = validate_claim(existing_command.command_id)
                    except Exception as exc:
                        raise GovernedInternalRuntimeError(
                            INTERNAL_RUNTIME_AUTHORITY_INVALID,
                            "durable Internal replay ingress claim is invalid",
                        ) from exc
            if existing is None:
                frozen_runtime_context = self._freeze_runtime_context(
                    work_order=legacy_order,
                    ts_ms=int(ts_ms),
                    command_authority=command_authority,
                )
            else:
                existing_order = existing.get("work_order")
                if not isinstance(existing_order, WorkOrderV3):
                    raise GovernedInternalRuntimeError(
                        INTERNAL_RUNTIME_AUTHORITY_INVALID,
                        "durable Internal replay lacks a typed WorkOrderV3",
                    )
                frozen_runtime_context = self._require_runtime_context(
                    existing_order.input_pack
                )
            candidate_command, candidate_order, candidate_graph = _build_contracts(
                work_order=legacy_order,
                request_id=request_id,
                session_id=session_id,
                ts_ms=ts_ms,
                governor_grant_ref=governor_grant_ref,
                command_authority=command_authority,
                frozen_runtime_context=frozen_runtime_context,
            )
            if existing is None:
                command = candidate_command
                order = candidate_order
                graph = candidate_graph
                assignment = self._runtime_router.assign_new_task(
                    order.work_order_id,
                    assigned_at_ms=int(ts_ms),
                )
            else:
                command = existing.get("command")
                order = existing.get("work_order")
                assignment = existing.get("assignment")
                graph = existing.get("graph")
                if not isinstance(command, HostCommand):
                    raise GovernedInternalRuntimeError(
                        INTERNAL_RUNTIME_AUTHORITY_INVALID,
                        "durable replay record has no typed HostCommand",
                    )
                _assert_stable_replay(candidate_command, command)
                if not isinstance(order, WorkOrderV3):
                    raise GovernedInternalRuntimeError(
                        INTERNAL_RUNTIME_AUTHORITY_INVALID,
                        "durable internal replay has no typed WorkOrderV3",
                    )
                try:
                    assert_stable_work_order_replay(
                        candidate_order,
                        order,
                        candidate_command=candidate_command,
                        durable_command=command,
                    )
                except GovernedHostAdapterError as exc:
                    raise GovernedInternalRuntimeError(
                        INTERNAL_RUNTIME_AUTHORITY_INVALID,
                        exc.detail,
                    ) from exc
                if assignment is None or graph is None:
                    raise GovernedInternalRuntimeError(
                        INTERNAL_RUNTIME_AUTHORITY_INVALID,
                        "durable internal replay record is incomplete",
                    )
            require_governed_control_route_policy(
                command, order, graph, GovernedInternalRuntimeError, INTERNAL_RUNTIME_AUTHORITY_INVALID
            )
            async for event in self._application.execute_stream(
                command=command,
                work_order=order,
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
            self._wake_terminal_continuation()
            async with self._condition:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._condition.notify_all()

    def _freeze_runtime_context(
        self,
        *,
        work_order: AgentWorkOrder,
        ts_ms: int,
        command_authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        authority = dict(command_authority or {})
        principal_id = _required_identifier(
            authority.get("principal_id"),
            field_name="principal_id",
        )
        try:
            frozen = self._runtime_context_application.freeze_request(
                request_body=None,
                person_id=principal_id,
                ts_ms=int(ts_ms),
                intent_draft={
                    "route": "delegate",
                    "target_id": "agent_runtime",
                    "task_kind": "INTERNAL",
                    "task": work_order.objective,
                },
                governor_result={
                    "risk_level": max(
                        1,
                        min(
                            5,
                            int(dict(work_order.metadata or {}).get("risk_level") or 1),
                        ),
                    )
                },
                command_authority=authority,
                emotion_context=None,
                emotion_snapshot=None,
                bond_summary=None,
            )
            return self._require_runtime_context(frozen.to_dict())
        except GovernedInternalRuntimeError:
            raise
        except Exception as exc:
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_AUTHORITY_INVALID,
                "Internal Runtime Context could not be frozen",
            ) from exc

    def _require_runtime_context(
        self,
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            frozen = self._runtime_context_application.require_frozen(value)
            payload = frozen.to_dict()
        except Exception as exc:
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_AUTHORITY_INVALID,
                "durable Internal Runtime Context is missing or drifted",
            ) from exc
        return dict(payload)

    async def continue_review_admission(
        self,
        record: Mapping[str, Any],
        claim: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Re-enter one exact durable Internal admission after Review."""

        async with self._condition:
            if not self._active or not self._accepting:
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_INACTIVE,
                    "internal runtime is not accepting Review continuation",
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
                or command.surface is not HostCommandSurface.INTERNAL
                or not isinstance(work_order, WorkOrderV3)
                or assignment is None
                or graph is None
            ):
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_AUTHORITY_INVALID,
                    "Review continuation Internal admission is incomplete or misrouted",
                )
            require_governed_control_route_policy(
                command, work_order, graph, GovernedInternalRuntimeError, INTERNAL_RUNTIME_AUTHORITY_INVALID
            )
            operation = self._application.execute_review_continuation(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
                continuation_claim=dict(claim or {}),
            )
            result = await collect_internal_continuation_result(operation)
            return await self._accepted_effect_runtime.dispatch(
                result,
                command=command,
            )
        finally:
            self._wake_terminal_continuation()
            async with self._condition:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._condition.notify_all()

    async def continue_startup_admission(
        self,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Re-enter one exact Internal admission selected by a survivor."""

        async with self._condition:
            if not self._active or not self._accepting:
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_INACTIVE,
                    "internal runtime is not accepting startup recovery",
                )
            self._in_flight += 1
        try:
            return await execute_internal_startup_admission(
                record=record,
                application=self._application,
                accepted_effect_runtime=self._accepted_effect_runtime,
            )
        finally:
            self._wake_terminal_continuation()
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
        """Cancel one exact Internal admission through verified authority."""

        async with self._condition:
            if not self._active or not self._accepting:
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_INACTIVE,
                    "internal runtime is not accepting cancellation commands",
                )
            self._in_flight += 1
        try:
            command = dict(record or {}).get("command")
            if (
                not isinstance(command, HostCommand)
                or command.surface is not HostCommandSurface.INTERNAL
            ):
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_AUTHORITY_INVALID,
                    "cancellation admission is not owned by the Internal runtime",
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
            self._wake_terminal_continuation()
            async with self._condition:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._condition.notify_all()

    def existing_command(self, *, request_id: str) -> Mapping[str, Any] | None:
        return self._commands.get_by_request(
            surface=HostCommandSurface.INTERNAL.value,
            request_id=str(request_id or "").strip(),
        )

    @property
    def startup_checkpoint_report(self) -> tuple[dict[str, Any], ...]:
        return self._startup_checkpoint_continuation.report

    @property
    def survivor_continuation_health(self) -> dict[str, Any]:
        continuation = self._checkpoint_survivor_continuation
        return (
            {
                "running": False,
                "stop_requested": False,
                "completed_cycles": 0,
                "last_report": (),
                "last_failure": None,
            }
            if continuation is None
            else continuation.health_snapshot()
        )

    def _wake_terminal_continuation(self) -> None:
        worker = self._terminal_continuation_worker
        if worker is not None:
            worker.wake()
        survivor = self._checkpoint_survivor_continuation
        if survivor is not None:
            survivor.wake()

    async def _recover_startup_sealed_checkpoints(
        self,
        *,
        page_limit: int = 100,
        scan_limit: int = 1_000,
    ) -> tuple[dict[str, Any], ...]:
        return await self._startup_checkpoint_continuation.recover(
            page_limit=page_limit,
            scan_limit=scan_limit,
        )

    async def exact_accepted_result_receipt(
        self,
        *,
        command_id: str,
        expected_receipt_ref: str,
    ) -> str | None:
        return await self._accepted_effect_runtime.exact_result_receipt(
            command_id=command_id,
            expected_receipt_ref=expected_receipt_ref,
        )

    async def exact_budget_rejection_claim_result_ref(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_receipt: Mapping[str, Any],
    ) -> str | None:
        """Return a claim result only for one re-opened C-boundary terminal.

        A ``BUDGET_BLOCKED`` stream event is not sufficient authority by
        itself.  The TaskStore must independently reopen the exact Internal
        checkpoint rejection (including its five terminal Outboxes) before a
        Secure Ingress claim can be completed.  All other rejected outcomes
        deliberately remain open for their own reconciliation path.
        """

        store = self._terminal_claim_task_store
        if store is None or command.surface is not HostCommandSurface.INTERNAL:
            return None
        receipt = dict(execution_receipt or {})
        if (
            str(receipt.get("status") or "").strip().upper() != "BUDGET_BLOCKED"
            or receipt.get("accepted") is not False
            or str(receipt.get("reason_code") or "").strip()
            != "GOVERNED_BUDGET_EXCEEDED"
        ):
            return None
        attempt_id = str(receipt.get("attempt_id") or "").strip()
        node_id = str(receipt.get("node_id") or "").strip()
        if (
            not attempt_id
            or node_id != str(work_order.task_node_id or "").strip()
            or str(receipt.get("command_id") or "").strip() != command.command_id
            or str(receipt.get("graph_id") or "").strip() != command.graph_id
            or receipt.get("graph_revision") != command.graph_revision
            or not command.ingress_claim_ref
        ):
            return None
        try:
            resolved = await asyncio.to_thread(
                store.resolve_internal_agent_budget_rejection_terminal,
                attempt_id,
            )
        except Exception:
            return None
        attempt = dict(resolved.get("attempt") or {})
        node = dict(resolved.get("node") or {})
        graph = dict(resolved.get("graph") or {})
        reservation = dict(resolved.get("reservation") or {})
        budget = dict(resolved.get("budget") or {})
        usage = dict(resolved.get("usage") or {})
        if (
            str(resolved.get("authority_kind") or "")
            != "INTERNAL_AGENT_BUDGET_REJECTED"
            or str(attempt.get("attempt_id") or "") != attempt_id
            or str(attempt.get("graph_id") or "") != command.graph_id
            or attempt.get("graph_revision") != command.graph_revision
            or str(attempt.get("node_id") or "") != node_id
            or str(node.get("node_id") or "") != node_id
            or str(graph.get("graph_id") or "") != command.graph_id
            or graph.get("revision") != command.graph_revision
        ):
            return None
        try:
            return build_internal_budget_rejection_result_ref(
                command_id=command.command_id,
                ingress_claim_ref=command.ingress_claim_ref,
                graph_id=command.graph_id,
                graph_revision=command.graph_revision,
                node_id=node_id,
                attempt_id=attempt_id,
                reservation_id=str(reservation.get("reservation_id") or ""),
                budget_id=str(budget.get("budget_id") or ""),
                usage_id=str(usage.get("usage_id") or ""),
            )
        except ValueError:
            return None

    async def exact_nonacceptance_checkpoint_claim_result_ref(
        self,
        *,
        command: HostCommand,
        execution_receipt: Mapping[str, Any],
    ) -> str | None:
        """Return a result only for an exact rejected committed checkpoint.

        The emitted stream receipt is only a hint.  The canonical TaskStore
        must reopen the final graph/node/attempt state and committed projection
        before the caller may settle Secure Ingress.  Worker failures and
        pre-checkpoint cancellation deliberately return ``None`` here.
        """

        store = self._terminal_claim_task_store
        if store is None or command.surface is not HostCommandSurface.INTERNAL:
            return None
        receipt = dict(execution_receipt or {})
        attempt_id = str(receipt.get("attempt_id") or "").strip()
        node_id = str(receipt.get("node_id") or "").strip()
        if (
            receipt.get("accepted") is not False
            or not attempt_id
            or not command.ingress_claim_ref
            or not node_id
            or str(receipt.get("command_id") or "").strip() != command.command_id
            or str(receipt.get("graph_id") or "").strip() != command.graph_id
            or receipt.get("graph_revision") != command.graph_revision
        ):
            return None
        try:
            resolved = await asyncio.to_thread(
                store.try_resolve_internal_agent_nonacceptance_checkpoint_terminal,
                attempt_id,
            )
        except Exception:
            return None
        if not isinstance(resolved, Mapping):
            return None
        terminal_reason = str(resolved.get("terminal_reason") or "")
        expected_receipt_status = {
            "TERMINAL_VERIFICATION_FAILED": "VERIFICATION_FAILED",
            "TERMINAL_POST_REVIEW_CANCELLED": "CANCELLED",
        }.get(terminal_reason)
        historical = resolved.get("historical_lease")
        completion = resolved.get("completion_lease")
        reservation = resolved.get("operation_reservation")
        projection = resolved.get("projection")
        if (
            terminal_reason not in INTERNAL_NONACCEPTANCE_TERMINAL_REASONS
            or str(receipt.get("status") or "").strip().upper()
            != expected_receipt_status
            or not all(
                isinstance(value, Mapping)
                for value in (historical, completion, reservation, projection)
            )
            or str(resolved.get("authority_kind") or "")
            != "INTERNAL_AGENT_NONACCEPTANCE_TERMINAL"
            or str(historical.get("attempt_id") or "") != attempt_id
            or str(completion.get("attempt_id") or "") != attempt_id
            or str(completion.get("status") or "") != "RELEASED"
            or str(reservation.get("attempt_id") or "") != attempt_id
            or str(projection.get("command_id") or "") != command.command_id
            or str(projection.get("graph_id") or "") != command.graph_id
            or int(projection.get("graph_revision") or 0)
            != command.graph_revision
            or str(projection.get("node_id") or "") != node_id
            or str(projection.get("attempt_id") or "") != attempt_id
        ):
            return None
        if terminal_reason == "TERMINAL_POST_REVIEW_CANCELLED":
            try:
                cancellation = await asyncio.to_thread(
                    store.get_graph_cancellation_for_graph,
                    command.graph_id,
                    command.graph_revision,
                )
                intent_ref = str(
                    cancellation.get("intent_ref")
                    if isinstance(cancellation, Mapping)
                    else ""
                ).strip()
                decision = await asyncio.to_thread(
                    store.get_graph_cancellation_decision_for_intent,
                    intent_ref,
                )
            except Exception:
                return None
            payload = decision.get("payload") if isinstance(decision, Mapping) else None
            if (
                not isinstance(payload, Mapping)
                or str(cancellation.get("status") or "") != "FINALIZED"
                or str(cancellation.get("graph_id") or "") != command.graph_id
                or int(cancellation.get("graph_revision") or 0)
                != command.graph_revision
                or str(decision.get("intent_ref") or "") != intent_ref
                or str(payload.get("graph_id") or "") != command.graph_id
                or int(payload.get("graph_revision") or 0)
                != command.graph_revision
                or str(payload.get("graph_target_status") or "") != "CANCELLED"
            ):
                return None
        try:
            return build_internal_nonacceptance_result_ref(
                command_id=command.command_id,
                ingress_claim_ref=command.ingress_claim_ref,
                graph_id=command.graph_id,
                graph_revision=command.graph_revision,
                node_id=node_id,
                attempt_id=attempt_id,
                historical_lease_id=str(historical.get("lease_id") or ""),
                completion_lease_id=str(completion.get("lease_id") or ""),
                reservation_id=str(reservation.get("reservation_id") or ""),
                projection_id=str(projection.get("projection_id") or ""),
                terminal_reason=terminal_reason,
            )
        except ValueError:
            return None


class InternalAgentCommandService:
    """Official Governor + durable runtime application boundary for routes."""

    def __init__(
        self,
        *,
        runtime: GovernedInternalRuntime,
        intent_engine: Any,
        claim_result_store: IngressClaimResultStore,
    ) -> None:
        if not callable(getattr(intent_engine, "evaluate_intent", None)):
            raise TypeError("intent_engine must expose evaluate_intent")
        if not callable(getattr(claim_result_store, "complete_claim", None)):
            raise TypeError("claim_result_store must expose complete_claim")
        self._runtime = runtime
        self._intent_engine = intent_engine
        self._claim_results = claim_result_store

    async def execute(
        self,
        *,
        work_order: AgentWorkOrder | Mapping[str, Any],
        request_id: str,
        session_id: str,
        ts_ms: int,
        command_authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        request_ref = _required_identifier(request_id, field_name="request_id")
        session_ref = _identifier_or_digest(session_id, prefix="internal-session")
        existing = self._runtime.existing_command(request_id=request_ref)
        if existing is not None:
            durable_command = existing.get("command")
            if not isinstance(durable_command, HostCommand) or not durable_command.governor_grant_ref:
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_GOVERNOR_GRANT_MISSING,
                    "durable internal command has no official Governor grant",
                )
            grant_ref = durable_command.governor_grant_ref
        else:
            sanitized = _sanitize_work_order(work_order)
            principal_id = _required_identifier(
                dict(command_authority or {}).get("principal_id"),
                field_name="principal_id",
            )
            intent = _build_governor_intent(
                work_order=sanitized,
                request_id=request_ref,
                session_id=session_ref,
                ts_ms=int(ts_ms),
                principal_id=principal_id,
            )
            from host_brain.app.emotion_context import (  # noqa: PLC0415
                build_chat_governance_snapshot,
            )

            decision = dict(
                self._intent_engine.evaluate_intent(
                    raw_intent=intent,
                    request_id=request_ref,
                    current_ms=int(ts_ms),
                    proposer_node_id="internal_agent_secure_ingress",
                    model_snapshot="server-owned-internal-command",
                    prompt_profile_id="internal-agent-command-v1",
                    governance_snapshot=build_chat_governance_snapshot(
                        emotion_context={
                            "mood_state": "steady",
                            "emotion_tag": "NEUTRAL",
                            "emotion_intensity": 0.0,
                            "emotion_ts_ms": int(ts_ms),
                        },
                        ts_ms=int(ts_ms),
                    ),
                )
                or {}
            )
            if str(decision.get("decision") or "").strip().upper() != "APPROVE":
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_GOVERNOR_REJECTED,
                    str(decision.get("reason_code") or "official Governor rejected internal command"),
                )
            grant_ref = governor_grant_ref(request_ref, decision)

        terminal: dict[str, Any] | None = None
        async for event in self._runtime.execute_stream(
            work_order=work_order,
            request_id=request_ref,
            session_id=session_ref,
            ts_ms=int(ts_ms),
            governor_grant_ref=grant_ref,
            command_authority=command_authority,
        ):
            if bool(event.get("done")):
                terminal = dict(event)
        if terminal is None:
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_AUTHORITY_INVALID,
                "internal runtime returned no terminal result",
            )
        receipt = dict(terminal.get("execution_receipt") or {})
        projection = (
            dict(receipt.get("projection") or {})
            if isinstance(receipt.get("projection"), Mapping)
            else {}
        )
        review = (
            dict(projection.get("review") or {})
            if isinstance(projection.get("review"), Mapping)
            else {}
        )
        waiting_review = str(receipt.get("status") or "").strip().upper() in {
            "WAITING_APPROVAL",
            "WAITING_ACCEPTANCE_REVIEW",
        }
        if waiting_review:
            if not str(review.get("review_id") or "").strip():
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
                    "waiting internal command has no durable ReviewItem",
                )
            # A POST checkpoint can already carry the worker's pre-acceptance
            # receipt.  It is still not the terminal command result, so leave
            # the ingress claim open for the fenced Review continuation.
            return terminal
        durable = self._runtime.existing_command(request_id=request_ref)
        durable_command = durable.get("command") if durable is not None else None
        durable_work_order = durable.get("work_order") if durable is not None else None
        if not isinstance(durable_command, HostCommand):
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
                "terminal internal command has no durable HostCommand",
            )
        if not isinstance(durable_work_order, WorkOrderV3):
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
                "terminal internal command has no durable WorkOrderV3",
            )
        claim_ref = str(durable_command.ingress_claim_ref or "").strip()
        if not claim_ref:
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
                "terminal internal command has no durable claim reference",
            )
        budget_terminal_ref = await self._runtime.exact_budget_rejection_claim_result_ref(
            command=durable_command,
            work_order=durable_work_order,
            execution_receipt=receipt,
        )
        if budget_terminal_ref is not None:
            try:
                self._claim_results.complete_claim(
                    claim_ref=claim_ref,
                    result_ref=budget_terminal_ref,
                    at_ms=int(ts_ms),
                )
            except Exception as exc:
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
                    "secure ingress budget terminal claim could not be completed",
            ) from exc
            return terminal
        nonacceptance_terminal_ref = (
            await self._runtime.exact_nonacceptance_checkpoint_claim_result_ref(
                command=durable_command,
                execution_receipt=receipt,
            )
        )
        if nonacceptance_terminal_ref is not None:
            try:
                self._claim_results.complete_claim(
                    claim_ref=claim_ref,
                    result_ref=nonacceptance_terminal_ref,
                    at_ms=int(ts_ms),
                )
            except Exception as exc:
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
                    "secure ingress non-acceptance terminal claim could not be completed",
                ) from exc
            return terminal
        effect_status = str(
            terminal.get("internal_agent_effect_status") or ""
        ).strip().upper()
        result_ref = str(
            terminal.get("internal_agent_result_receipt_ref") or ""
        ).strip()
        # ACCEPTED is the Task decision, not proof that its post-Acceptance
        # result promotion committed.  PENDING/FAILED/NO_EFFECTS and missing
        # attribution must leave Secure Ingress open for exact recovery.
        if effect_status != "COMMITTED" or not result_ref:
            return terminal
        exact_result_ref = await self._runtime.exact_accepted_result_receipt(
            command_id=durable_command.command_id,
            expected_receipt_ref=result_ref,
        )
        if exact_result_ref != result_ref:
            return {
                **terminal,
                "internal_agent_effect_status": "RECOVERY_REQUIRED",
                "reason_code": "INTERNAL_AGENT_RESULT_ATTRIBUTION_MISSING",
            }
        try:
            self._claim_results.complete_claim(
                claim_ref=claim_ref,
                result_ref=exact_result_ref,
                at_ms=int(ts_ms),
            )
        except Exception as exc:
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
                "secure ingress claim result could not be completed",
            ) from exc
        return terminal


def build_governed_internal_runtime(
    *,
    task_runtime_container: Any,
    ledger: Any,
    token_service: Any,
    result_repository: AgentExecutionResultRepository,
    runtime_router: CanonicalRuntimeRouter,
    owner_id: str,
    canonical_executor: AgentRuntimeExecutor | None = None,
    cancellation_authority_verifier: Any | None = None,
    runtime_context_application: Any | None = None,
    action_preview_service: Any | None = None,
    acceptance_effect_dispatcher: Any | None = None,
    artifact_store: Any | None = None,
    workspace_runtime: DurableWorkspaceRuntime | None = None,
    claim_result_store: Any | None = None,
    clock: Callable[[], int] | None = None,
) -> GovernedInternalRuntime:
    review_store = getattr(task_runtime_container, "review_store", None)
    continuation_store = getattr(
        task_runtime_container,
        "review_continuation_store",
        None,
    )
    if review_store is None or continuation_store is None:
        raise TypeError(
            "internal Agent Task Runtime Review stores are required"
        )
    node_grant_service = NodeBoundGrantService(
        ledger=ledger,
        token_service=token_service,
        review_store=continuation_store,
        clock=clock,
    )
    node_grant_verifier = DurableNodeGrantVerifier(
        ledger=ledger,
        review_store=continuation_store,
        clock=clock,
    )
    projection_store = getattr(task_runtime_container, "worker_projection_store", None)
    if projection_store is None:
        raise TypeError("Internal Task Runtime worker projection store is required")
    if not callable(getattr(runtime_context_application, "freeze_request", None)) or (
        not callable(getattr(runtime_context_application, "require_frozen", None))
    ):
        raise TypeError("Internal Runtime Context authority is required")
    if not callable(getattr(action_preview_service, "issue_receipt", None)) or (
        not callable(getattr(action_preview_service, "require_execution", None))
    ):
        raise TypeError("Internal Action Preview authority is required")
    canonical_worker = GovernedFrozenNodeWorker(
        route_kind="delegate",
        executor=InternalAgentProposalExecutor(
            command_repository=task_runtime_container.command_repository,
            executor=canonical_executor or AgentRuntimeExecutor(),
        ),
        node_grants=node_grant_verifier,
        projections=projection_store,
        action_preview_service=action_preview_service,
        allowed_acceptance_effect_types=frozenset(
            {INTERNAL_AGENT_RESULT_COMMIT_EFFECT_TYPE}
        ),
        preview_purpose="delegate.dispatch",
    )
    coordinator = GovernedTaskCoordinator(
        task_store=task_runtime_container.task_store,
        internal_agent_checkpoint_uow=(
            task_runtime_container.internal_agent_checkpoint_uow
        ),
        outbox_publisher=task_runtime_container.outbox_publisher,
        outbox_factory=OutboxMessage,
        worker_resolver=_AgentWorkerResolver(canonical=canonical_worker),
        verifier=InternalAgentProposalVerifier(
            projection_store=projection_store,
        ),
        acceptance=decorate_task_acceptance(
            InternalAgentProposalAcceptance(
                projection_store=projection_store,
            ),
            task_runtime_container=task_runtime_container,
        ),
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
        command_repository=task_runtime_container.command_repository,
        coordinator=coordinator,
        node_grant_service=node_grant_service,
        cancellation_authority_verifier=cancellation_authority_verifier,
    )
    accepted_effect_runtime = InternalAcceptedEffectRuntime(
        dispatcher=acceptance_effect_dispatcher,
        result_repository=result_repository,
    )
    terminal_worker: InternalArtifactTerminalContinuationWorker | None = None
    if artifact_store is not None or workspace_runtime is not None:
        if artifact_store is None or workspace_runtime is None:
            raise TypeError(
                "Internal artifact terminal continuation requires both store and Workspace"
            )
        terminal_worker = InternalArtifactTerminalContinuationWorker(
            reconciler=AgentArtifactTerminalReconcilerGroup(
                GovernedSkillArtifactTerminalReconciler(
                    artifact_store=artifact_store,
                    workspace_runtime=workspace_runtime,
                    task_store=task_runtime_container.task_store,
                    command_repository=task_runtime_container.command_repository,
                    projection_store=projection_store,
                ),
                InternalArtifactTerminalReconciler(
                    artifact_store=artifact_store,
                    workspace_runtime=workspace_runtime,
                    task_store=task_runtime_container.task_store,
                    command_repository=task_runtime_container.command_repository,
                    projection_store=projection_store,
                    claim_result_store=claim_result_store,
                ),
            )
        )
    return GovernedInternalRuntime(
        application=application,
        runtime_router=runtime_router,
        command_repository=task_runtime_container.command_repository,
        governor_grants=DurableGovernorGrantResolver(ledger),
        runtime_context_application=runtime_context_application,
        accepted_effect_runtime=accepted_effect_runtime,
        terminal_continuation_worker=terminal_worker,
        startup_task_store=(
            task_runtime_container.task_store if artifact_store is not None else None
        ),
        startup_artifact_store=artifact_store,
        startup_claim_result_store=claim_result_store,
        terminal_claim_task_store=task_runtime_container.task_store,
    )


def _build_governor_intent(
    *,
    work_order: AgentWorkOrder,
    request_id: str,
    session_id: str,
    ts_ms: int,
    principal_id: str,
) -> dict[str, Any]:
    principal_ref = _required_identifier(principal_id, field_name="principal_id")
    nonce = hashlib.sha256(
        f"internal-governor\n{request_id}".encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-internal-{hashlib.sha256(request_id.encode('utf-8')).hexdigest()[:40]}",
        "ts_ms": int(ts_ms),
        "session_id": session_id,
        "seq": int(ts_ms),
        "type": "control",
        "action": "exec_intent",
        "target": "system",
        "args": {
            "text": work_order.objective,
            "route": "delegate",
            "target_id": "agent_runtime",
        },
        "nonce": nonce,
        "person_id": principal_ref,
        "principal_id": principal_ref,
        "route": "delegate",
        "target_id": "agent_runtime",
    }


def parse_external_v2_work_order(value: Any) -> AgentWorkOrder:
    """Accept only a pre-admission V2 description at the Internal ingress.

    A durable ``WorkOrderV3`` already names a root, graph, node and revision.
    Re-projecting it through this ingress would discard those bindings and build
    a second graph.  The compatibility boundary therefore accepts V2 only;
    canonical V3 work orders must re-enter through their persisted command.
    """

    if isinstance(value, WorkOrderV3):
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_PRE_ADMITTED_WORK_ORDER_FORBIDDEN,
            "Internal ingress cannot re-admit a WorkOrderV3 object",
        )
    if isinstance(value, AgentWorkOrder):
        return value
    if not isinstance(value, Mapping):
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "Internal ingress work_order must be a V2 mapping",
        )
    payload = dict(value)
    schema_version = str(payload.get("schema_version") or "").strip()
    forbidden_fields = sorted(
        field_name
        for field_name in _PRE_ADMITTED_WORK_ORDER_FIELDS
        if field_name in payload
    )
    if schema_version == WORK_ORDER_V3_SCHEMA_VERSION or forbidden_fields:
        detail = "Internal ingress cannot re-admit a pre-bound WorkOrder"
        if schema_version == WORK_ORDER_V3_SCHEMA_VERSION:
            detail += " with WorkOrderV3 schema"
        if forbidden_fields:
            detail += ": " + ", ".join(forbidden_fields)
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_PRE_ADMITTED_WORK_ORDER_FORBIDDEN,
            detail,
        )
    return AgentWorkOrder.from_mapping(payload)


def _sanitize_work_order(value: Any) -> AgentWorkOrder:
    order = parse_external_v2_work_order(value)
    payload = order.to_dict()
    metadata = dict(payload.get("metadata") or {})
    for key in _CALLER_AUTHORITY_KEYS:
        metadata.pop(key, None)
    payload["metadata"] = metadata
    approval_policy = dict(payload.get("approval_policy") or {})
    for key in _CALLER_APPROVAL_AUTHORITY_KEYS:
        approval_policy.pop(key, None)
    payload["approval_policy"] = approval_policy
    safe = _safe_value(payload, path="work_order")
    if not isinstance(safe, dict):
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "work_order is not a durable mapping",
        )
    sanitized = AgentWorkOrder.from_mapping(safe)
    sanitized.validate()
    return sanitized


def _internal_delegate_route(value: object) -> GovernedWorkerRoute:
    try:
        route = GovernedWorkerRoute.parse(str(value or ""))
    except GovernedWorkerRegistryError as exc:
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "internal TaskNode route is invalid",
        ) from exc
    if route.kind != "delegate" or not route.target_id:
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "internal TaskNode route must be canonical delegate:<worker_key>",
        )
    return route


def _safe_value(value: Any, *, path: str) -> Any:
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip()
            if not key:
                continue
            if _FORBIDDEN_DURABLE_KEY.search(key) and not key.lower().endswith("_ref"):
                raise GovernedInternalRuntimeError(
                    INTERNAL_RUNTIME_AUTHORITY_INVALID,
                    f"secret-like WorkOrder field is forbidden: {path}.{key}",
                )
            safe[key] = _safe_value(raw_value, path=f"{path}.{key}")
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, path=f"{path}[]") for item in value[:2_048]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:128_000]
    return str(value)[:8_000]


def _assert_stable_replay(candidate: HostCommand, durable: HostCommand) -> None:
    candidate_payload = candidate.to_dict()
    durable_payload = durable.to_dict()
    drift = [
        field_name
        for field_name in _STABLE_COMMAND_FIELDS
        if candidate_payload.get(field_name) != durable_payload.get(field_name)
    ]
    if drift:
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "replayed internal command changed stable semantics: " + ", ".join(drift),
        )


__all__ = [
    "INTERNAL_RUNTIME_AUTHORITY_INVALID",
    "INTERNAL_RUNTIME_DRAIN_TIMEOUT",
    "INTERNAL_RUNTIME_GOVERNOR_GRANT_MISSING",
    "INTERNAL_RUNTIME_GOVERNOR_REJECTED",
    "INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED",
    "INTERNAL_RUNTIME_INACTIVE",
    "INTERNAL_RUNTIME_PRE_ADMITTED_WORK_ORDER_FORBIDDEN",
    "GovernedInternalRuntime",
    "GovernedInternalRuntimeError",
    "IngressClaimResultStore",
    "InternalAgentCommandService",
    "build_governed_internal_runtime",
    "parse_external_v2_work_order",
]
