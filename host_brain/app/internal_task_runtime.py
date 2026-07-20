"""Governed Task Runtime adapter for authenticated internal Agent WorkOrders."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
import hashlib
import inspect
import json
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
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.workspace_runtime import DurableWorkspaceRuntime
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetExceededAction,
    BudgetPolicy,
)
from host_brain.persistence.task_repository import OutboxMessage
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder, WorkOrderV3
from host_brain.orchestration import (
    DurableGovernorGrantResolver,
    GovernedCommandApplication,
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
from host_brain.task_runtime import GovernedTaskCoordinator
from host_brain.task_runtime.review_runtime import TaskNodeReviewRuntime
from host_brain.task_runtime.governed_workers import GovernedFrozenNodeWorker
from host_brain.task_runtime.worker_registry import (
    GovernedWorkerRegistryError,
    GovernedWorkerRoute,
)
from host_brain.app.internal_startup_checkpoint_continuation import (
    InternalStartupCheckpointContinuation,
)
from natsume_contracts.task import HostCommand, HostCommandSurface, build_host_command


INTERNAL_RUNTIME_INACTIVE = "INTERNAL_RUNTIME_INACTIVE"
INTERNAL_RUNTIME_AUTHORITY_INVALID = "INTERNAL_RUNTIME_AUTHORITY_INVALID"
INTERNAL_RUNTIME_GOVERNOR_GRANT_MISSING = "INTERNAL_RUNTIME_GOVERNOR_GRANT_MISSING"
INTERNAL_RUNTIME_DRAIN_TIMEOUT = "INTERNAL_RUNTIME_DRAIN_TIMEOUT"
INTERNAL_RUNTIME_GOVERNOR_REJECTED = "INTERNAL_RUNTIME_GOVERNOR_REJECTED"
INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED = (
    "INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED"
)
INTERNAL_RUNTIME_STARTUP_CONTINUATION_DEFERRED = (
    "INTERNAL_RUNTIME_STARTUP_CONTINUATION_DEFERRED"
)
INTERNAL_AGENT_WORKER_ROUTE = "delegate"
_INTERNAL_TASK_NODE_ROUTE = "delegate:agent_runtime"

_DEFAULT_DRAIN_TIMEOUT_S = 120.0
_INTERNAL_AGENT_BUDGET_LIMITS = BudgetAmount(
    token_count=65_536,
    tool_call_count=64,
    time_ms=300_000,
    concurrency=1,
    retry_count=8,
    model_cost_micros=2_000_000,
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
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


class GovernedInternalRuntimeError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or INTERNAL_RUNTIME_AUTHORITY_INVALID)
        self.detail = str(detail or "governed internal runtime failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


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
        if route.canonical != _INTERNAL_TASK_NODE_ROUTE:
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
        drain_timeout_s: float = _DEFAULT_DRAIN_TIMEOUT_S,
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
        self._startup_checkpoint_continuation = InternalStartupCheckpointContinuation(
            task_store=startup_task_store,
            artifact_store=startup_artifact_store,
            claim_result_store=startup_claim_result_store,
            command_repository=command_repository,
            application=application,
            accepted_effect_runtime=accepted_effect_runtime,
            expected_node_route=_INTERNAL_TASK_NODE_ROUTE,
            authority_invalid_code=INTERNAL_RUNTIME_AUTHORITY_INVALID,
            ingress_completion_failed_code=INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
            deferred_code=INTERNAL_RUNTIME_STARTUP_CONTINUATION_DEFERRED,
            error_factory=GovernedInternalRuntimeError,
        )
        self._drain_timeout_s = max(0.1, float(drain_timeout_s))
        self._condition = asyncio.Condition()
        self._active = False
        self._accepting = False
        self._in_flight = 0

    async def activate(self) -> None:
        worker = self._terminal_continuation_worker
        if worker is not None:
            await asyncio.to_thread(worker.run_once)
        await self._recover_startup_sealed_checkpoints()
        if worker is not None:
            await asyncio.to_thread(worker.run_once)
            worker.start()
            self._terminal_worker_quiesced = False
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
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_DRAIN_TIMEOUT,
                f"internal runtime still has {self._in_flight} in-flight command(s)",
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
                if order is None or assignment is None or graph is None:
                    raise GovernedInternalRuntimeError(
                        INTERNAL_RUNTIME_AUTHORITY_INVALID,
                        "durable internal replay record is incomplete",
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

    def _wake_terminal_continuation(self) -> None:
        worker = self._terminal_continuation_worker
        if worker is not None:
            worker.wake()

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
        if not isinstance(durable_command, HostCommand):
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
                "terminal internal command has no durable HostCommand",
            )
        claim_ref = str(durable_command.ingress_claim_ref or "").strip()
        if not claim_ref:
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
                "terminal internal command has no durable claim reference",
            )
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
    )
    node_grant_verifier = DurableNodeGrantVerifier(
        ledger=ledger,
        review_store=continuation_store,
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
        acceptance=InternalAgentProposalAcceptance(
            projection_store=projection_store,
        ),
        projection_reader=projection_store,
        review_runtime=TaskNodeReviewRuntime(review_store),
        node_grant_verifier=node_grant_verifier,
        acceptance_effects_enabled=True,
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
            reconciler=InternalArtifactTerminalReconciler(
                artifact_store=artifact_store,
                workspace_runtime=workspace_runtime,
                task_store=task_runtime_container.task_store,
                command_repository=task_runtime_container.command_repository,
                projection_store=projection_store,
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
    )


def _build_contracts(
    *,
    work_order: AgentWorkOrder,
    request_id: str,
    session_id: str,
    ts_ms: int,
    governor_grant_ref: str,
    command_authority: Mapping[str, Any],
    frozen_runtime_context: Mapping[str, Any],
) -> tuple[HostCommand, WorkOrderV3, TaskGraph]:
    authority = dict(command_authority or {})
    principal_id = _required_identifier(
        authority.get("principal_id"),
        field_name="principal_id",
    )
    claim_ref = _required_reference(
        authority.get("ingress_claim_ref") or authority.get("claim_ref"),
        field_name="ingress_claim_ref",
    )
    audit_ref = _required_reference(
        authority.get("ingress_audit_ref") or authority.get("audit_ref"),
        field_name="ingress_audit_ref",
    )
    grant_ref = _required_reference(
        governor_grant_ref,
        field_name="governor_grant_ref",
    )
    request_ref = _required_identifier(request_id, field_name="request_id")
    session_ref = _identifier_or_digest(session_id, prefix="internal-session")
    input_sha = str(
        authority.get("input_sha256") or authority.get("body_sha256") or ""
    ).strip().lower()
    if input_sha.startswith("sha256:"):
        input_sha = input_sha.split(":", 1)[1]
    if not re.fullmatch(r"[0-9a-f]{64}", input_sha):
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "secure ingress body_sha256 is missing or invalid",
        )
    canonical_input_sha = f"sha256:{input_sha}"
    input_ref = f"internal-input://sha256/{input_sha}"
    suffix = hashlib.sha256(
        f"internal\n{request_ref}".encode("utf-8")
    ).hexdigest()[:40]
    graph_id = f"graph-internal-{suffix}"
    node_id = f"node-internal-{suffix}"
    risk_level = max(
        1,
        min(5, int(dict(work_order.metadata or {}).get("risk_level") or 1)),
    )
    business_scope = f"internal-agent:{_scope_token(work_order.domain)}"
    operation_fingerprint = _sha256_json(
        {
            "principal_id": principal_id,
            "input_sha256": canonical_input_sha,
            "work_order": work_order.to_dict(),
        }
    )
    output_contract = {
        "type": "internal_agent_result_proposal",
        "required": ["acceptance_effect_intents", "receipt_ref"],
    }
    review_policy, review_ref = _canonical_review_policy(
        work_order,
        suffix=suffix,
    )
    approval_policy = {
        **dict(work_order.approval_policy or {}),
        "required": True,
        "governor_grant_ref": grant_ref,
        "on_missing": "BLOCK",
    }
    budget_policy = BudgetPolicy(
        scope=f"work-order:{work_order.work_order_id}",
        limits=_INTERNAL_AGENT_BUDGET_LIMITS,
        on_exceeded=BudgetExceededAction.BLOCK,
        metadata={
            "policy": "governed_internal_agent_default",
            "policy_version": "governed_internal_agent_budget.v1",
            "route_type": "delegate",
            "fail_closed": True,
            "source": "internal_task_runtime_composition",
        },
    )
    budget_policy.validate()
    context_payload = dict(frozen_runtime_context or {})
    overlap = sorted(set(work_order.input_pack).intersection(context_payload))
    if overlap:
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "caller Internal input overlaps frozen Runtime Context: "
            + ",".join(overlap),
        )
    canonical_order = WorkOrderV3(
        work_order_id=work_order.work_order_id,
        domain=work_order.domain,
        run_mode=work_order.run_mode,
        objective=work_order.objective,
        workspace=(
            {
                "strategy": "durable_workspace",
                "workspace_id": DurableWorkspaceRuntime.workspace_id_for(
                    graph_id=graph_id,
                    graph_revision=1,
                ),
            }
            if work_order.requires_write
            else deepcopy(dict(work_order.workspace or {}))
        ),
        input_pack={
            **deepcopy(dict(work_order.input_pack or {})),
            **deepcopy(context_payload),
        },
        allowed_actions=tuple(work_order.allowed_actions or ()),
        forbidden_actions=tuple(work_order.forbidden_actions or ()),
        acceptance_criteria=tuple(
            work_order.acceptance_criteria or ("result executed",)
        ),
        verification_plan=deepcopy(dict(work_order.verification_plan or {})),
        approval_policy=approval_policy,
        review_policy=review_policy,
        metadata={
            **deepcopy(dict(work_order.metadata or {})),
            "surface": HostCommandSurface.INTERNAL.value,
            "planning_lineage": {
                "graph_id": graph_id,
                "root_work_order_id": work_order.work_order_id,
                "revision": 1,
                "parent_revision": None,
                "approval_ref": grant_ref,
                "review_ref": review_ref,
                "node_bindings": [
                    {
                        "node_id": node_id,
                        "objective": work_order.objective,
                        "worker_route": "delegate",
                        "target_id": "agent_runtime",
                    }
                ],
            },
        },
        root_work_order_id=work_order.work_order_id,
        parent_work_order_id=None,
        task_graph_id=graph_id,
        task_node_id=node_id,
        graph_revision=1,
        attempt=0,
        capability_requirements=("agent_runtime",),
        risk_level=risk_level,
        input_refs=(input_ref, grant_ref),
        output_contract=output_contract,
        budget_policy=budget_policy,
        business_idempotency_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
        status_version=0,
        approval_ref=grant_ref,
        review_ref=review_ref,
    )
    canonical_order.validate()
    node = TaskNode(
        node_id=node_id,
        dependencies=(),
        route=_INTERNAL_TASK_NODE_ROUTE,
        input_refs=(input_ref, grant_ref),
        output_contract=output_contract,
        retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
        risk_level=risk_level,
        acceptance_criteria=canonical_order.acceptance_criteria,
        status=TaskNodeStatus.READY,
        status_version=0,
    )
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id=work_order.work_order_id,
        revision=1,
        planner_ref=f"planner://governed-internal/{suffix}",
        status=TaskGraphStatus.CREATED,
        created_at=int(ts_ms),
        result_node_id=node_id,
        nodes=(node,),
    )
    command = build_host_command(
        surface=HostCommandSurface.INTERNAL,
        request_id=request_ref,
        session_id=session_ref,
        principal_id=principal_id,
        ingress_claim_ref=claim_ref,
        ingress_audit_ref=audit_ref,
        governor_grant_ref=grant_ref,
        input_ref=input_ref,
        input_sha256=canonical_input_sha,
        root_work_order_id=work_order.work_order_id,
        graph_id=graph_id,
        graph_revision=1,
        route_type=INTERNAL_AGENT_WORKER_ROUTE,
        target_id="agent_runtime",
        business_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
        created_at_ms=int(ts_ms),
    )
    return command, canonical_order, graph


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


def _sanitize_work_order(value: AgentWorkOrder | Mapping[str, Any]) -> AgentWorkOrder:
    order = value if isinstance(value, AgentWorkOrder) else AgentWorkOrder.from_mapping(value)
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


def _canonical_review_policy(
    work_order: AgentWorkOrder,
    *,
    suffix: str,
) -> tuple[dict[str, Any], str]:
    policy = deepcopy(dict(work_order.review_policy or {}))
    approval = dict(work_order.approval_policy or {})
    required = bool(
        work_order.review_required
        or policy.get("durable_review_required")
        or policy.get("require_durable_review")
        or approval.get("require_review_token")
        or approval.get("review_id")
    )
    if required:
        for key in ("waiver_authority", "waiver_ref"):
            policy.pop(key, None)
        policy.update(
            {
                "review_required": True,
                "durable_review_required": True,
                "mode": "durable_task_node_review",
                "reason": "internal_agent_review_policy",
            }
        )
        return policy, f"review-policy://internal-agent/{suffix}"
    review_ref = f"review-waiver://governor-grant/{suffix}"
    policy.update(
        {
            "review_required": False,
            "durable_review_required": False,
            "require_durable_review": False,
            "mode": "governor_grant_review_waiver",
            "reason": "approved_internal_agent_execution",
            "waiver_authority": "governor_grant",
            "waiver_ref": review_ref,
        }
    )
    return policy, review_ref


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


async def _review_continuation_result(operation: Any) -> dict[str, Any]:
    current = await operation if inspect.isawaitable(operation) else operation
    if isinstance(current, Mapping):
        return dict(current)
    if not hasattr(current, "__aiter__"):
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "Review continuation returned no durable Internal result",
        )
    terminal: dict[str, Any] | None = None
    async for raw_event in current:
        if not isinstance(raw_event, Mapping):
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_AUTHORITY_INVALID,
                "Review continuation emitted a non-mapping Internal event",
            )
        event = dict(raw_event)
        if bool(event.get("done")):
            terminal = event
    if terminal is None:
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "Review continuation emitted no durable Internal terminal result",
        )
    return terminal


def _required_identifier(value: Any, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            f"{field_name} is not a bounded identifier",
        )
    return normalized


def _required_reference(value: Any, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if "://" not in normalized or len(normalized) > 512:
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            f"{field_name} is not a durable reference",
        )
    return normalized


def _identifier_or_digest(value: Any, *, prefix: str) -> str:
    normalized = str(value or "").strip()
    if _IDENTIFIER.fullmatch(normalized):
        return normalized
    return f"{prefix}-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _scope_token(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(value or "agent").strip().lower())
    return normalized.strip("-")[:64] or "agent"


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:40]}"


def _sha256_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        deepcopy(dict(value)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "INTERNAL_AGENT_WORKER_ROUTE",
    "INTERNAL_RUNTIME_AUTHORITY_INVALID",
    "INTERNAL_RUNTIME_DRAIN_TIMEOUT",
    "INTERNAL_RUNTIME_GOVERNOR_GRANT_MISSING",
    "INTERNAL_RUNTIME_GOVERNOR_REJECTED",
    "INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED",
    "INTERNAL_RUNTIME_INACTIVE",
    "GovernedInternalRuntime",
    "GovernedInternalRuntimeError",
    "IngressClaimResultStore",
    "InternalAgentCommandService",
    "build_governed_internal_runtime",
]
