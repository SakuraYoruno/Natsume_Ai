"""Production adapter from Host surfaces to the universal governed runtime.

Direct chat/skill/delegate routes and searched multi-node plans all enter the
same immutable HostCommand/WorkOrderV3/TaskGraph admission and coordinator.
Route workers receive only frozen node input; planning never runs inside a
worker or during graph execution.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
import inspect
from typing import Any

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.persistence.task_repository import OutboxMessage
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.orchestration import (
    GovernedCommandApplication,
    governor_grant_ref,
)
from host_brain.orchestration.node_grants import (
    DurableNodeGrantVerifier,
    NodeBoundGrantService,
)
from host_brain.orchestration.host_route_contracts import (
    build_direct_host_route_contracts,
)
from host_brain.orchestration.failure_replan_intent import (
    failure_replan_reference_from_intent,
)
from host_brain.orchestration.capability_plan_search import (
    build_capability_plan_search,
    is_capability_plan_intent,
)
from host_brain.orchestration.planning_admission import GovernedPlanningAdmissionService
from host_brain.orchestration.planning_execution import (
    GovernedPlanningExecutionService,
    PlanningExecutionAuthority,
)
from host_brain.orchestration.planning_graph_compiler import PlanningGraphCompiler
from host_brain.orchestration.planning_recovery import (
    GovernedPlanningRecoveryService,
)
from host_brain.app.governed_route_executors import (
    FrozenChatRouteExecutor,
    FrozenDelegateRouteExecutor,
)
from host_brain.app.governed_tool_runtime import (
    DurableToolTaskAuthorityVerifier,
    FrozenToolRouteExecutor,
    ToolHostRequest,
)
from host_brain.app.governed_delegate_dispatch import (
    GovernedDelegateDispatchError,
)
from host_brain.app.host_startup_graph_continuation import (
    HostStartupGraphContinuation,
)
from host_brain.app.periodic_durable_continuation import (
    PeriodicDurableContinuation,
)
from host_brain.app.governed_frozen_skill_runtime import (
    GOVERNED_HOST_GOVERNOR_GRANT_MISSING,
    GOVERNED_HOST_ROUTE_UNSUPPORTED,
    GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE,
    _CompositeProjectionReader,
    _DurableSkillProjectionReader,
    _FrozenSkillWorker,
    _WorkerCommitAcceptance,
    _WorkerCommitVerifier,
    _require_frozen_skill_dependencies,
)
from host_brain.app.governed_host_contracts import (
    _build_contracts,
    _build_frozen_context,
    _ingress_references,
    _require_existing_plan_replay,
    _resolve_host_command_principal,
    _resolve_host_command_surface,
    _resolve_host_input_sha256,
)
from host_brain.app.governed_task_authority import (
    GOVERNED_HOST_AUTHORITY_INVALID,
    GovernedHostAdapterError,
    assert_stable_command_replay as _assert_stable_command_replay,
    assert_stable_work_order_replay as _assert_stable_work_order_replay,
    assert_work_order_command_binding as _assert_work_order_command_binding,
    require_fail_closed_direct_skill_work_order as _require_fail_closed_direct_skill_work_order,  # noqa: F401
)
from host_brain.app.governed_skill_execution_ports import (
    build_governed_skill_execution_ports,
)
from host_brain.task_runtime import GovernedTaskCoordinator
from host_brain.task_runtime.governed_workers import GovernedFrozenNodeWorker
from host_brain.task_runtime.review_runtime import TaskNodeReviewRuntime
from host_brain.task_runtime.worker_registry import GovernedWorkerRegistry
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
)
from host_brain.runtime.context_application import RuntimeContextApplicationService
from natsume_contracts.task import HostCommand, HostCommandSurface


GOVERNED_HOST_RUNTIME_INACTIVE = "GOVERNED_HOST_RUNTIME_INACTIVE"
GOVERNED_HOST_DRAIN_TIMEOUT = "GOVERNED_HOST_DRAIN_TIMEOUT"
GOVERNED_HOST_ACCEPTANCE_EFFECTS_UNAVAILABLE = (
    "GOVERNED_HOST_ACCEPTANCE_EFFECTS_UNAVAILABLE"
)
GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE = (
    "GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE"
)
GOVERNED_HOST_PLANNING_SEARCH_REQUIRED = (
    "GOVERNED_HOST_PLANNING_SEARCH_REQUIRED"
)
_DEFAULT_DRAIN_TIMEOUT_S = 120.0
_DEFAULT_SURVIVOR_INTERVAL_MS = 5_000
_HOST_SURVIVOR_CONTINUATION_FAILED = (
    "GOVERNED_HOST_SURVIVOR_CONTINUATION_FAILED"
)


class GovernedHostRuntime:
    """Lifecycle-aware application facade published by the ASGI composition."""

    def __init__(
        self,
        *,
        application: GovernedCommandApplication,
        runtime_router: CanonicalRuntimeRouter,
        command_repository: Any,
        task_store: Any | None = None,
        supported_routes: frozenset[str] = frozenset({"skill"}),
        planning_enabled: bool = False,
        acceptance_effect_dispatcher: AcceptanceEffectDispatcher | None = None,
        delegate_target_validator: Callable[[str], Any] | None = None,
        planning_skill_registry: Any | None = None,
        runtime_context_application: RuntimeContextApplicationService | None = None,
        drain_timeout_s: float = _DEFAULT_DRAIN_TIMEOUT_S,
        survivor_interval_ms: int = _DEFAULT_SURVIVOR_INTERVAL_MS,
    ) -> None:
        self._application = application
        self._runtime_router = runtime_router
        self._command_repository = command_repository
        self._task_store = task_store
        self._supported_routes = frozenset(
            str(route or "").strip().lower() for route in supported_routes
        )
        self._planning_enabled = bool(planning_enabled)
        self._acceptance_effect_dispatcher = acceptance_effect_dispatcher
        self._delegate_target_validator = delegate_target_validator
        self._planning_skill_registry = planning_skill_registry
        if runtime_context_application is not None and not isinstance(
            runtime_context_application,
            RuntimeContextApplicationService,
        ):
            raise TypeError(
                "runtime_context_application must be RuntimeContextApplicationService"
        )
        self._runtime_context_application = runtime_context_application
        self._drain_timeout_s = max(0.1, float(drain_timeout_s))
        self._lifecycle = asyncio.Condition()
        self._activation_lock = asyncio.Lock()
        self._in_flight = 0
        self._active = False
        self._accepting = False
        self._startup_recovery_completed = False
        self._startup_continuation = (
            HostStartupGraphContinuation(
                task_store=task_store,
                command_repository=command_repository,
                resume_admission=self._resume_startup_admission,
            )
            if task_store is not None
            and callable(getattr(task_store, "read_scheduler_snapshot", None))
            and callable(
                getattr(
                    command_repository,
                    "list_nonterminal_admissions",
                    None,
                )
            )
            else None
        )
        self._survivor_continuation = (
            PeriodicDurableContinuation(
                runner=self._startup_continuation.recover,
                task_name="governed-host-survivor-continuation",
                failure_reason_code=_HOST_SURVIVOR_CONTINUATION_FAILED,
                interval_ms=int(survivor_interval_ms),
            )
            if self._startup_continuation is not None
            else None
        )

    async def activate(self) -> None:
        # RuntimeGroup may receive an already-active Host instance while it
        # composes Review continuation.  Durable graph recovery is invoked by
        # the Host lifespan only after the complete RuntimeGroup and shared
        # Acceptance Effect owner are live.
        async with self._activation_lock:
            async with self._lifecycle:
                if self._active:
                    return
            async with self._lifecycle:
                self._active = True
                self._accepting = True
                self._startup_recovery_completed = False

    @property
    def startup_continuation_report(self) -> tuple[dict[str, Any], ...]:
        """Expose the bounded, durable Host restart continuation evidence."""

        continuation = self._startup_continuation
        return () if continuation is None else continuation.report

    @property
    def survivor_continuation_health(self) -> dict[str, Any]:
        """Expose only lifecycle/scan health, never a second task truth."""

        continuation = self._survivor_continuation
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

    async def recover_startup_graphs(self) -> tuple[dict[str, Any], ...]:
        """Run the mandatory boot pass, then own periodic survivor scans.

        The method is intentionally not folded into ``activate``.  Direct
        test or compatibility callers can activate a Host façade before the
        shared Acceptance Effect owner exists; re-entering a graph there would
        create a second, partial startup authority.  ASGI calls this only
        after the runtime group has activated every governed surface.
        """

        async with self._activation_lock:
            async with self._lifecycle:
                if not self._active or not self._accepting:
                    raise GovernedHostAdapterError(
                        GOVERNED_HOST_RUNTIME_INACTIVE,
                        "governed Host runtime is not active for startup continuation",
                    )
                if self._startup_recovery_completed:
                    return self.startup_continuation_report
            supervisor = self._survivor_continuation
            if supervisor is None:
                self._startup_recovery_completed = True
                return ()
            report = await supervisor.run_once()
            self._startup_recovery_completed = True
            await supervisor.start()
            return report

    def wake_survivor_continuation(self) -> None:
        """Request a coalesced exact scan after a relevant durable transition."""

        continuation = self._survivor_continuation
        if continuation is not None:
            continuation.wake()

    async def close_admission(self) -> None:
        async with self._lifecycle:
            self._accepting = False
        continuation = self._survivor_continuation
        if continuation is not None:
            continuation.request_stop()

    async def drain(self) -> None:
        async def _wait_until_idle() -> None:
            async with self._lifecycle:
                await self._lifecycle.wait_for(lambda: self._in_flight == 0)

        try:
            await asyncio.wait_for(_wait_until_idle(), timeout=self._drain_timeout_s)
        except TimeoutError as exc:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_DRAIN_TIMEOUT,
                f"governed Host runtime still has {self._in_flight} in-flight command(s)",
            ) from exc
        continuation = self._survivor_continuation
        if continuation is not None:
            try:
                await continuation.join(timeout_s=self._drain_timeout_s)
            except RuntimeError as exc:
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_DRAIN_TIMEOUT,
                    "governed Host survivor continuation did not quiesce",
                ) from exc

    async def aclose(self) -> None:
        await self.close_admission()
        await self.drain()
        async with self._lifecycle:
            self._active = False

    def supports(self, intent_draft: Mapping[str, Any] | None) -> bool:
        draft = dict(intent_draft or {})
        route = str(draft.get("route") or "").strip().lower()
        if route == "plan":
            # A caller-provided step list is not a search space.  The only
            # currently enabled Host plan route reconstructs competing
            # candidates from the app-composed registry and compiles the
            # selected result into a TaskGraph.  Keep pure legacy adapters
            # outside this production boundary until another domain supplies
            # the same catalog-backed authority.
            return (
                self._planning_enabled
                and self._planning_skill_registry is not None
                and is_capability_plan_intent(draft)
            )
        if route not in self._supported_routes:
            return False
        if route == "chat":
            return True
        return bool(
            str(
                draft.get("target_id")
                or draft.get("skill_id")
                or draft.get("agent_id")
                or ""
            ).strip()
        )

    async def replay_tool_stream(
        self,
        *,
        request_id: str,
        target_id: str,
        session_id: str,
        principal_id: str,
        command_authority: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """Replay one exact Secure-Ingress Tool admission without re-governing.

        Secure Ingress has already established that the duplicate body is
        byte-identical.  This method reopens only the canonical HostCommand;
        it never invokes Governor, ToolCatalog selection or ToolBroker itself.
        """

        async with self._lifecycle:
            if not self._active or not self._accepting:
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_RUNTIME_INACTIVE,
                    "governed Host runtime is not accepting Tool replays",
                )
            self._in_flight += 1
        try:
            request_ref = str(request_id or "").strip()
            target = str(target_id or "").strip()
            session = str(session_id or "").strip()
            principal = str(principal_id or "").strip()
            authority = dict(command_authority or {})
            record = self._command_repository.get_by_request(
                surface=HostCommandSurface.TOOL.value,
                request_id=request_ref,
            )
            if not isinstance(record, Mapping):
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_AUTHORITY_INVALID,
                    "duplicate Tool ingress has no durable HostCommand",
                )
            command = record.get("command")
            validate_claim = getattr(
                self._command_repository,
                "validate_ingress_claim_result",
                None,
            )
            if isinstance(command, HostCommand) and callable(validate_claim):
                record = validate_claim(command.command_id)
                command = record.get("command")
            work_order = record.get("work_order")
            assignment = record.get("assignment")
            graph = record.get("graph")
            body_digest = str(authority.get("body_sha256") or "").strip().lower()
            if body_digest and not body_digest.startswith("sha256:"):
                body_digest = "sha256:" + body_digest
            if (
                not isinstance(command, HostCommand)
                or command.surface is not HostCommandSurface.TOOL
                or command.request_id != request_ref
                or command.route_type != "tool"
                or command.target_id != target
                or command.session_id != session
                or command.principal_id != principal
                or command.ingress_claim_ref
                != str(authority.get("ingress_claim_ref") or "").strip()
                or command.ingress_audit_ref
                != str(authority.get("ingress_audit_ref") or "").strip()
                or command.input_sha256 != body_digest
                or not isinstance(work_order, WorkOrderV3)
                or assignment is None
                or graph is None
            ):
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_AUTHORITY_INVALID,
                    "duplicate Tool ingress differs from its canonical command",
                )
            intent = work_order.input_pack.get("intent_draft")
            if (
                not isinstance(intent, Mapping)
                or str(intent.get("route") or "").strip().lower() != "tool"
                or str(intent.get("target_id") or "").strip() != target
            ):
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_AUTHORITY_INVALID,
                    "durable Tool WorkOrder route binding is invalid",
                )
            async for event in self._application.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            ):
                yield await self._dispatch_accepted_effects(event)
        finally:
            async with self._lifecycle:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._lifecycle.notify_all()
            self.wake_survivor_continuation()

    async def request_graph_cancellation(
        self,
        record: Mapping[str, Any],
        *,
        authority: Mapping[str, Any],
        source: str,
        reason_code: str,
    ) -> dict[str, Any]:
        """Cancel one exact Host-surface admission through verified authority."""

        async with self._lifecycle:
            if not self._active or not self._accepting:
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_RUNTIME_INACTIVE,
                    "governed Host runtime is not accepting cancellation commands",
                )
            self._in_flight += 1
        try:
            durable = dict(record or {})
            command = durable.get("command")
            if (
                not isinstance(command, HostCommand)
                or command.surface
                not in {
                    HostCommandSurface.CHAT,
                    HostCommandSurface.HOST_INPUT,
                    HostCommandSurface.TOOL,
                    HostCommandSurface.GATEWAY,
                    HostCommandSurface.OPERATOR,
                    HostCommandSurface.SCHEDULER,
                }
            ):
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_AUTHORITY_INVALID,
                    "cancellation admission is not owned by the Host runtime",
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
            async with self._lifecycle:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._lifecycle.notify_all()
            self.wake_survivor_continuation()

    async def execute_stream(
        self,
        *,
        request_body: Any,
        request_id: str,
        session_id: str,
        ts_ms: int,
        intent_draft: Mapping[str, Any],
        governor_result: Mapping[str, Any],
        person_id: str,
        sanitized_message: str,
        emotion_context: Mapping[str, Any] | None = None,
        emotion_snapshot: Mapping[str, Any] | None = None,
        bond_summary: Mapping[str, Any] | None = None,
        retrieval_evidence_items: Sequence[Mapping[str, Any]] = (),
        retrieval_summary: Mapping[str, Any] | None = None,
        llm_runtime: Mapping[str, Any] | None = None,
        pii_ingress_stats: Mapping[str, Any] | None = None,
        motive_state: Mapping[str, Any] | None = None,
        render_target_lang: str = "zh",
        command_authority: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async with self._lifecycle:
            if not self._active or not self._accepting:
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_RUNTIME_INACTIVE,
                    "governed Host runtime is not accepting commands",
                )
            self._in_flight += 1
        try:
            route = str(intent_draft.get("route") or "").strip().lower()
            if route == "plan" and not is_capability_plan_intent(intent_draft):
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_PLANNING_SEARCH_REQUIRED,
                    "Host Plan requires a catalog-backed PlanningSearch intent; "
                    "planned_steps/steps cannot authorize execution",
                )
            if not self.supports(intent_draft):
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_ROUTE_UNSUPPORTED,
                    "Host route has no frozen governed worker",
                )
            if str(governor_result.get("decision") or "").strip().upper() != "APPROVE":
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_GOVERNOR_GRANT_MISSING,
                    "an approved official Governor decision is required",
                )
            if route == "tool":
                authority = dict(command_authority or {})
                ingress_claim_ref, ingress_audit_ref = _ingress_references(authority)
                surface = _resolve_host_command_surface(
                    request_body=request_body,
                    authority=authority,
                )
                if (
                    not isinstance(request_body, ToolHostRequest)
                    or surface is not HostCommandSurface.TOOL
                    or not ingress_claim_ref
                    or not ingress_audit_ref
                ):
                    raise GovernedHostAdapterError(
                        GOVERNED_HOST_AUTHORITY_INVALID,
                        "Tool route requires the server-owned TOOL surface and exact Secure Ingress claim/audit",
                    )
            frozen_context = _build_frozen_context(
                request_body=request_body,
                surface_user_message=sanitized_message,
                emotion_context=emotion_context,
                emotion_snapshot=emotion_snapshot,
                bond_summary=bond_summary,
                retrieval_evidence_items=retrieval_evidence_items,
                retrieval_summary=retrieval_summary,
                llm_runtime=llm_runtime,
                pii_ingress_stats=pii_ingress_stats,
                motive_state=motive_state,
                render_target_lang=render_target_lang,
            )
            existing_request_id = request_id
            replan_reference = (
                failure_replan_reference_from_intent(intent_draft)
                if route == "plan"
                else None
            )
            if replan_reference is not None:
                existing_request_id = (
                    f"{request_id}.graph-revision-"
                    f"{replan_reference.previous_revision + 1}"
                )
            surface = _resolve_host_command_surface(
                request_body=request_body,
                authority=dict(command_authority or {}),
            )
            existing_request = self._command_repository.get_by_request(
                surface=surface.value,
                request_id=existing_request_id,
            )
            context_application = self._runtime_context_application
            if context_application is not None:
                existing_order = (
                    existing_request.get("work_order")
                    if isinstance(existing_request, Mapping)
                    else None
                )
                if isinstance(existing_order, WorkOrderV3):
                    runtime_context = context_application.require_frozen(
                        existing_order.input_pack
                    )
                else:
                    runtime_context = context_application.freeze_request(
                        request_body=request_body,
                        person_id=person_id,
                        ts_ms=int(ts_ms),
                        intent_draft=intent_draft,
                        governor_result=governor_result,
                        command_authority=command_authority,
                        emotion_context=emotion_context,
                        emotion_snapshot=emotion_snapshot,
                        bond_summary=bond_summary,
                    )
                overlap = sorted(set(frozen_context).intersection(runtime_context.payload))
                if overlap:
                    raise GovernedHostAdapterError(
                        GOVERNED_HOST_AUTHORITY_INVALID,
                        "runtime context overlaps frozen Host fields: "
                        + ",".join(overlap),
                    )
                frozen_context.update(runtime_context.to_dict())
            if route == "plan":
                if isinstance(existing_request, Mapping):
                    _require_existing_plan_replay(
                        existing_request,
                        request_body=request_body,
                        request_id=existing_request_id,
                        session_id=session_id,
                        person_id=person_id,
                        sanitized_message=sanitized_message,
                        governor_result=governor_result,
                        command_authority=command_authority,
                        expected_revision=(
                            replan_reference.previous_revision + 1
                            if replan_reference is not None
                            else 1
                        ),
                    )
                    async for event in self._application.execute_stream(
                        command=existing_request["command"],
                        work_order=existing_request["work_order"],
                        assignment=existing_request["assignment"],
                        graph=existing_request["graph"],
                    ):
                        yield await self._dispatch_accepted_effects(event)
                    return
                async for event in self._execute_plan_stream(
                    request_body=request_body,
                    request_id=request_id,
                    session_id=session_id,
                    ts_ms=ts_ms,
                    intent_draft=intent_draft,
                    governor_result=governor_result,
                    person_id=person_id,
                    sanitized_message=sanitized_message,
                    command_authority=command_authority,
                    frozen_context=frozen_context,
                ):
                    yield event
                return
            if route == "skill":
                skill_risk_level, skill_requires_review = (
                    self._direct_skill_execution_policy(intent_draft)
                )
                candidate_command, candidate_work_order, candidate_graph = _build_contracts(
                    request_body=request_body,
                    request_id=request_id,
                    session_id=session_id,
                    ts_ms=ts_ms,
                    intent_draft=intent_draft,
                    governor_result=governor_result,
                    person_id=person_id,
                    sanitized_message=sanitized_message,
                    emotion_context=emotion_context,
                    render_target_lang=render_target_lang,
                    command_authority=command_authority,
                    frozen_context=frozen_context,
                    skill_risk_level=skill_risk_level,
                    skill_requires_durable_review=skill_requires_review,
                )
            else:
                if route == "delegate":
                    self._validate_delegate_target(intent_draft)
                authority = dict(command_authority or {})
                surface = _resolve_host_command_surface(
                    request_body=request_body,
                    authority=authority,
                )
                ingress_claim_ref, ingress_audit_ref = _ingress_references(authority)
                # Older admitted Chat/Delegate intents predate the explicit
                # action/target projection.  Preserve their established Host
                # contract while the pure builder remains strict for new
                # callers and direct contract tests.
                direct_intent = dict(intent_draft or {})
                direct_intent.setdefault("action", "exec_intent")
                direct_intent.setdefault("target", "host")
                contracts = build_direct_host_route_contracts(
                    surface=surface,
                    request_id=request_id,
                    session_id=session_id,
                    principal_id=_resolve_host_command_principal(
                        person_id=person_id,
                        authority=authority,
                    ),
                    ts_ms=ts_ms,
                    intent_draft=direct_intent,
                    governor_result=governor_result,
                    sanitized_message=sanitized_message,
                    person_id=person_id,
                    emotion_context=emotion_context,
                    render_target_lang=render_target_lang,
                    input_sha256=_resolve_host_input_sha256(
                        sanitized_message=sanitized_message,
                        authority=authority,
                    ),
                    ingress_claim_ref=ingress_claim_ref,
                    ingress_audit_ref=ingress_audit_ref,
                    operator_command_id=(
                        str(authority.get("operator_command_id") or "").strip()
                        or None
                    ),
                    frozen_context=frozen_context,
                )
                candidate_command = contracts.command
                candidate_work_order = contracts.work_order
                candidate_graph = contracts.graph
            existing = existing_request or self._command_repository.get_by_request(
                surface=candidate_command.surface.value,
                request_id=candidate_command.request_id,
            )
            if existing is None:
                command = candidate_command
                work_order = candidate_work_order
                graph = candidate_graph
                assignment = self._runtime_router.assign_new_task(
                    work_order.work_order_id,
                    assigned_at_ms=ts_ms,
                )
            else:
                command = existing.get("command")
                work_order = existing.get("work_order")
                assignment = existing.get("assignment")
                graph = existing.get("graph")
                if not isinstance(command, HostCommand):
                    raise GovernedHostAdapterError(
                        GOVERNED_HOST_AUTHORITY_INVALID,
                        "durable replay record has no typed HostCommand",
                    )
                _assert_stable_command_replay(candidate_command, command)
                if not isinstance(work_order, WorkOrderV3):
                    raise GovernedHostAdapterError(
                        GOVERNED_HOST_AUTHORITY_INVALID,
                        "durable replay record has no explicit WorkOrderV3",
                    )
                _assert_stable_work_order_replay(
                    candidate_work_order,
                    work_order,
                    candidate_command=candidate_command,
                    durable_command=command,
                )
                _assert_work_order_command_binding(work_order, command)
                if assignment is None or graph is None:
                    raise GovernedHostAdapterError(
                        GOVERNED_HOST_AUTHORITY_INVALID,
                        "durable replay record is incomplete",
                    )
            async for event in self._application.execute_stream(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            ):
                yield await self._dispatch_accepted_effects(event)
        finally:
            async with self._lifecycle:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._lifecycle.notify_all()
            self.wake_survivor_continuation()

    def _direct_skill_execution_policy(
        self,
        intent_draft: Mapping[str, Any],
    ) -> tuple[int, bool]:
        registry = self._planning_skill_registry
        require = getattr(registry, "require", None)
        target = str(
            intent_draft.get("target_id") or intent_draft.get("skill_id") or ""
        ).strip()
        if not callable(require) or not target:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "direct Skill admission has no app-local SkillRegistry policy",
            )
        try:
            metadata = require(target)
            risk_level = int(getattr(metadata, "risk_level"))
        except Exception as exc:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "direct Skill admission could not freeze canonical risk metadata",
            ) from exc
        if not 1 <= risk_level <= 5:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "direct Skill canonical risk level is invalid",
            )
        return risk_level, bool(
            getattr(metadata, "require_confirm", False) or risk_level >= 4
        )

    async def continue_review_admission(
        self,
        record: Mapping[str, Any],
        claim: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Re-enter one exact durable Host admission after Review resolution."""

        async with self._lifecycle:
            if not self._active or not self._accepting:
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_RUNTIME_INACTIVE,
                    "governed Host runtime is not accepting Review continuation",
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
                    HostCommandSurface.CHAT,
                    HostCommandSurface.HOST_INPUT,
                    HostCommandSurface.TOOL,
                    HostCommandSurface.GATEWAY,
                    HostCommandSurface.OPERATOR,
                    HostCommandSurface.SCHEDULER,
                }
                or not isinstance(work_order, WorkOrderV3)
                or assignment is None
                or graph is None
            ):
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_AUTHORITY_INVALID,
                    "Review continuation Host admission is incomplete or misrouted",
                )
            operation = self._application.execute_review_continuation(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
                continuation_claim=dict(claim or {}),
            )
            return await _review_continuation_result(
                operation,
                transform=self._dispatch_accepted_effects,
            )
        finally:
            async with self._lifecycle:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._lifecycle.notify_all()
            self.wake_survivor_continuation()

    async def continue_operation_reacquisition(
        self,
        record: Mapping[str, Any],
        _operation: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Re-enter one exact Host admission after signed no-effect recovery."""

        async with self._lifecycle:
            if not self._active or not self._accepting:
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_RUNTIME_INACTIVE,
                    "governed Host runtime is not accepting Operation recovery",
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
                    HostCommandSurface.CHAT,
                    HostCommandSurface.HOST_INPUT,
                    HostCommandSurface.TOOL,
                    HostCommandSurface.GATEWAY,
                    HostCommandSurface.OPERATOR,
                    HostCommandSurface.SCHEDULER,
                }
                or not isinstance(work_order, WorkOrderV3)
                or assignment is None
                or graph is None
            ):
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_AUTHORITY_INVALID,
                    "Operation recovery Host admission is incomplete or misrouted",
                )
            return dict(
                await self._resume_startup_admission(
                    command,
                    work_order,
                    assignment,
                    graph,
                )
            )
        finally:
            async with self._lifecycle:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._lifecycle.notify_all()
            self.wake_survivor_continuation()

    async def _resume_startup_admission(
        self,
        command: HostCommand,
        work_order: WorkOrderV3,
        assignment: Any,
        graph: Any,
    ) -> Mapping[str, Any]:
        """Re-enter an already-admitted Host graph without rebuilding input.

        This is intentionally narrower than ``execute_stream``: startup owns
        no raw request, Governor decision, or plan search authority.  The
        continuation receives only the exact durable command tuple selected by
        ``HostStartupGraphContinuation`` and still sends accepted effects to
        the shared app-local dispatcher.
        """

        terminal: dict[str, Any] | None = None
        terminal_count = 0
        async for event in self._application.execute_stream(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        ):
            if bool(event.get("done")):
                terminal_count += 1
                terminal = await self._dispatch_accepted_effects(event)
        if terminal_count != 1 or terminal is None:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "startup continuation produced no unique durable terminal result",
            )
        return terminal

    async def _execute_plan_stream(
        self,
        *,
        request_body: Any,
        request_id: str,
        session_id: str,
        ts_ms: int,
        intent_draft: Mapping[str, Any],
        governor_result: Mapping[str, Any],
        person_id: str,
        sanitized_message: str,
        command_authority: Mapping[str, Any] | None,
        frozen_context: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        ingress = dict(command_authority or {})
        input_sha256 = _resolve_host_input_sha256(
            sanitized_message=sanitized_message,
            authority=ingress,
        )
        input_ref = f"input://sha256/{input_sha256.removeprefix('sha256:')}"
        if not is_capability_plan_intent(intent_draft):
            raise GovernedHostAdapterError(
                GOVERNED_HOST_PLANNING_SEARCH_REQUIRED,
                "Host Plan requires a catalog-backed PlanningSearch intent; "
                "planned_steps/steps cannot authorize execution",
            )
        if self._planning_skill_registry is None:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "capability planning requires the app-composed SkillRegistry",
            )
        adapter = build_capability_plan_search(
            intent_draft,
            governor_result=governor_result,
            skill_registry=self._planning_skill_registry,
            request_id=request_id,
            input_refs=(input_ref,),
            frozen_context=frozen_context,
        )
        compilation_context_resolver = adapter.compilation_context_for
        ingress_claim_ref, ingress_audit_ref = _ingress_references(ingress)
        authority = PlanningExecutionAuthority(
            surface=_resolve_host_command_surface(
                request_body=request_body,
                authority=ingress,
            ),
            base_command_request_id=request_id,
            session_id=session_id,
            principal_id=_resolve_host_command_principal(
                person_id=person_id,
                authority=ingress,
            ),
            input_ref=input_ref,
            input_sha256=input_sha256,
            governor_grant_ref=governor_grant_ref(request_id, governor_result),
            target_id="planning_search",
            ingress_claim_ref=ingress_claim_ref,
            ingress_audit_ref=ingress_audit_ref,
        )
        service = GovernedPlanningExecutionService(
            search=adapter,  # type: ignore[arg-type]
            compiler=PlanningGraphCompiler(),
            admission=GovernedPlanningAdmissionService(
                command_repository=self._command_repository,
                runtime_router=self._runtime_router,
            ),
            command_application=self._application,
            delegate_target_validator=self._delegate_target_validator,
            compilation_context_resolver=compilation_context_resolver,
        )
        replan = failure_replan_reference_from_intent(intent_draft)
        if replan is None:
            prepared = await service.prepare_initial(
                adapter.request,
                authority=authority,
                created_at=int(ts_ms),
            )
        else:
            # A failure reference selects only a stored immutable revision.
            # The next catalog-backed search is still independent, while
            # failure evidence and quiescence come from TaskStore.
            if self._task_store is None:
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_AUTHORITY_INVALID,
                    "failure replan requires the canonical durable TaskStore",
                )
            prepared = await GovernedPlanningRecoveryService(
                execution_service=service,
                task_store=self._task_store,
            ).prepare_failure_replan(
                adapter.request,
                authority=authority,
                graph_id=replan.graph_id,
                previous_revision=replan.previous_revision,
                created_at=int(ts_ms),
            )
        async for event in service.execute_stream(prepared):
            yield await self._dispatch_accepted_effects(event)

    def _validate_delegate_target(self, intent_draft: Mapping[str, Any]) -> None:
        validator = self._delegate_target_validator
        target_id = str(
            intent_draft.get("target_id")
            or intent_draft.get("agent_id")
            or ""
        ).strip()
        if not target_id:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE,
                "delegate route has no target_id",
            )
        if validator is None:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE,
                "delegate target registry is not composed",
            )
        try:
            result = validator(target_id)
        except GovernedDelegateDispatchError as exc:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE,
                exc.detail,
            ) from exc
        except Exception as exc:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE,
                "delegate target validation failed",
            ) from exc
        if inspect.isawaitable(result):
            raise GovernedHostAdapterError(
                GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE,
                "delegate target validator must be synchronous before admission",
            )

    async def _dispatch_accepted_effects(
        self,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        projected = dict(event)
        dispatcher = self._acceptance_effect_dispatcher
        receipt = projected.get("execution_receipt")
        if dispatcher is None or not isinstance(receipt, Mapping):
            return projected
        if not bool(receipt.get("accepted")) or str(receipt.get("status") or "") != "ACCEPTED":
            return projected
        graph_id = str(receipt.get("graph_id") or "").strip()
        graph_revision = int(receipt.get("graph_revision") or 0)
        if not graph_id or graph_revision < 1:
            return projected
        results = await dispatcher.dispatch_graph(
            graph_id=graph_id,
            graph_revision=graph_revision,
        )
        statuses = [str(row.get("status") or "") for row in results]
        recovery_pending = sum(
            bool(row.get("recovery_pending"))
            or str(row.get("dispatch_state") or "") == "RECOVERY_PENDING"
            for row in results
        )
        audit_refs = dict(projected.get("audit_refs") or {})
        audit_refs["acceptance_effects"] = {
            "count": len(results),
            "committed": statuses.count("COMMITTED"),
            "failed": statuses.count("FAILED"),
            "pending": sum(status not in {"COMMITTED", "FAILED"} for status in statuses),
            "recovery_pending": recovery_pending,
            "dispatch_status": (
                "FAILED"
                if "FAILED" in statuses
                else "RECOVERY_PENDING"
                if recovery_pending
                else "COMMITTED"
                if results and all(status == "COMMITTED" for status in statuses)
                else "NO_EFFECTS"
            ),
        }
        projected["audit_refs"] = audit_refs
        return projected


def build_governed_host_runtime(
    *,
    task_runtime_container: Any,
    ledger: Any,
    skill_registry: Any,
    token_service: Any,
    capability_broker: Any,
    model_attempt_service: Any,
    production_authority_verifier: Any,
    resource_manager: Any | None,
    episodic_store: Any | None,
    semantic_recaller: Any | None,
    agent_artifact_store: Any | None = None,
    agent_workspace_runtime: Any | None = None,
    tool_binding_resolver: Any | None = None,
    tool_execution_port_factory: Any | None = None,
    tool_catalog: Any | None = None,
    cancellation_authority_verifier: Any | None = None,
    runtime_router: CanonicalRuntimeRouter,
    owner_id: str,
    bounded_fanout_enabled: bool = False,
    fanout_event_buffer_size: int = 32,
    atomic_graph_success_enabled: bool = True,
    allow_uncomposed_acceptance_effects: bool = False,
    acceptance_effect_dispatcher: AcceptanceEffectDispatcher | None = None,
    runtime_context_application: RuntimeContextApplicationService | None = None,
    intent_admission_verifier: Any | None = None,
    degradation_capability_port: Any | None = None,
    action_preview_service: Any | None = None,
    surface_runtime_ports: Any | None = None,
) -> GovernedHostRuntime:
    from host_brain.app.route_execution_ports import (  # noqa: PLC0415
        build_governed_route_execution_ports,
        dispatch_governed_delegate,
        stream_governed_chat_with_context,
    )
    from host_brain.app.governed_delegate_dispatch import (  # noqa: PLC0415
        build_governed_delegate_dispatcher,
    )

    if not callable(
        getattr(
            task_runtime_container.command_repository,
            "list_nonterminal_admissions",
            None,
        )
    ):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "production governed Host runtime requires a surface-filtered "
            "nonterminal admission inventory",
        )
    node_grant_service = NodeBoundGrantService(
        ledger=ledger,
        token_service=token_service,
        review_store=task_runtime_container.review_continuation_store,
    )
    node_grant_verifier = DurableNodeGrantVerifier(
        ledger=ledger,
        review_store=task_runtime_container.review_continuation_store,
    )
    delegate_dispatcher = build_governed_delegate_dispatcher(
        node_grants=node_grant_verifier,
    )
    skill_execution_ports = build_governed_skill_execution_ports(
        skill_registry=skill_registry,
        capability_broker=capability_broker,
        model_attempt_service=model_attempt_service,
        production_authority_verifier=production_authority_verifier,
        resource_manager=resource_manager,
        episodic_store=episodic_store,
        semantic_recaller=semantic_recaller,
        agent_artifact_store=agent_artifact_store,
        agent_workspace_runtime=agent_workspace_runtime,
        degradation_capability_port=degradation_capability_port,
    )
    if action_preview_service is None and runtime_context_application is not None:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "runtime-context production requires the lifespan-owned "
            "ActionPreviewService; a worker-local authority is forbidden",
        )
    projection_store = getattr(task_runtime_container, "worker_projection_store", None)
    canonical = _FrozenSkillWorker(
        ledger=ledger,
        execution_ports=skill_execution_ports,
        node_grant_verifier=node_grant_verifier,
        projections=projection_store,
        action_preview_service=action_preview_service,
        review_store=getattr(task_runtime_container, "review_store", None),
    )
    chat_worker = None
    delegate_worker = None
    tool_worker = None
    tool_ports = (
        tool_binding_resolver,
        tool_execution_port_factory,
        tool_catalog,
    )
    if any(value is not None for value in tool_ports) and not all(
        value is not None for value in tool_ports
    ):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "Tool binding, app-local execution port factory and ToolCatalog "
            "must be composed together",
        )
    if projection_store is not None:
        route_execution_ports = build_governed_route_execution_ports(
            governed_delegate_dispatcher=delegate_dispatcher,
            surface_runtime_ports=surface_runtime_ports,
        )
        chat_worker = GovernedFrozenNodeWorker(
            route_kind="chat",
            executor=FrozenChatRouteExecutor(
                ports=route_execution_ports,
                stream_chat_with_context=stream_governed_chat_with_context,
            ),
            node_grants=node_grant_verifier,
            projections=projection_store,
        )
        delegate_worker = GovernedFrozenNodeWorker(
            route_kind="delegate",
            executor=FrozenDelegateRouteExecutor(
                ports=route_execution_ports,
                dispatch_to_sub_agent=dispatch_governed_delegate,
            ),
            node_grants=node_grant_verifier,
            projections=projection_store,
            action_preview_service=action_preview_service,
        )
        if all(value is not None for value in tool_ports):
            live_tool_authority = DurableToolTaskAuthorityVerifier(
                task_store=task_runtime_container.task_store,
                node_grants=node_grant_verifier,
            )
            execution_port = tool_execution_port_factory(
                authority_verifier=live_tool_authority.verify,
            )
            tool_worker = GovernedFrozenNodeWorker(
                route_kind="tool",
                executor=FrozenToolRouteExecutor(
                    binding_resolver=tool_binding_resolver,
                    execution_port=execution_port,
                    catalog=tool_catalog,
                    ledger=ledger,
                ),
                node_grants=node_grant_verifier,
                projections=projection_store,
                action_preview_service=action_preview_service,
            )
    worker_registry = GovernedWorkerRegistry(
        chat=chat_worker,
        skill=canonical,
        delegate=delegate_worker,
        tool=tool_worker,
    )
    coordinator = GovernedTaskCoordinator(
        task_store=task_runtime_container.task_store,
        internal_agent_checkpoint_uow=(
            task_runtime_container.internal_agent_checkpoint_uow
        ),
        outbox_publisher=task_runtime_container.outbox_publisher,
        outbox_factory=OutboxMessage,
        worker_resolver=worker_registry,
        verifier=_WorkerCommitVerifier(),
        acceptance=_WorkerCommitAcceptance(),
        projection_reader=_CompositeProjectionReader(
            skill=_DurableSkillProjectionReader(ledger),
            workers=projection_store,
        ),
        review_runtime=(
            TaskNodeReviewRuntime(task_runtime_container.review_store)
            if getattr(task_runtime_container, "review_store", None) is not None
            else None
        ),
        node_grant_verifier=node_grant_verifier,
        acceptance_effects_enabled=True,
        bounded_fanout_enabled=bool(bounded_fanout_enabled),
        fanout_event_buffer_size=int(fanout_event_buffer_size),
        atomic_graph_success_enabled=bool(atomic_graph_success_enabled),
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
        intent_admission_verifier=intent_admission_verifier,
    )
    effect_dispatcher = acceptance_effect_dispatcher
    if effect_dispatcher is None and not allow_uncomposed_acceptance_effects:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_ACCEPTANCE_EFFECTS_UNAVAILABLE,
            "production governed runtime requires the app-local shared "
            "Acceptance Effect dispatcher",
        )
    if effect_dispatcher is not None and not isinstance(
        effect_dispatcher,
        AcceptanceEffectDispatcher,
    ):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_ACCEPTANCE_EFFECTS_UNAVAILABLE,
            "Acceptance Effect dispatcher is not the app-local durable dispatcher",
        )
    return GovernedHostRuntime(
        application=application,
        runtime_router=runtime_router,
        command_repository=task_runtime_container.command_repository,
        task_store=task_runtime_container.task_store,
        supported_routes=frozenset(
            {"skill"}
            | ({"chat", "delegate"} if projection_store is not None else set())
            | ({"tool"} if tool_worker is not None else set())
        ),
        planning_enabled=projection_store is not None,
        acceptance_effect_dispatcher=effect_dispatcher,
        delegate_target_validator=delegate_dispatcher.require_target,
        planning_skill_registry=skill_registry,
        runtime_context_application=runtime_context_application,
    )


async def _review_continuation_result(
    operation: Any,
    *,
    transform: Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    current = await operation if inspect.isawaitable(operation) else operation
    if isinstance(current, Mapping):
        return await transform(dict(current))
    if not hasattr(current, "__aiter__"):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "Review continuation returned no durable Host result",
        )
    terminal: dict[str, Any] | None = None
    async for raw_event in current:
        if not isinstance(raw_event, Mapping):
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "Review continuation emitted a non-mapping Host event",
            )
        event = await transform(dict(raw_event))
        if bool(event.get("done")):
            terminal = event
    if terminal is None:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "Review continuation emitted no durable Host terminal result",
        )
    return terminal


__all__ = [
    "GOVERNED_HOST_AUTHORITY_INVALID",
    "GOVERNED_HOST_DELEGATE_TARGET_UNAVAILABLE",
    "GOVERNED_HOST_GOVERNOR_GRANT_MISSING",
    "GOVERNED_HOST_ROUTE_UNSUPPORTED",
    "GOVERNED_HOST_RUNTIME_INACTIVE",
    "GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE",
    "GovernedHostAdapterError",
    "GovernedHostRuntime",
    "build_governed_host_runtime",
]
