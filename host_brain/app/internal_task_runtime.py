"""Governed Task Runtime adapter for authenticated internal Agent WorkOrders."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Protocol

from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
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
from host_brain.task_runtime.worker_budget_usage import (
    build_worker_budget_usage,
    stable_worker_usage_id,
)
from host_brain.task_runtime.worker_registry import (
    GovernedWorkerRegistryError,
    GovernedWorkerRoute,
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
INTERNAL_AGENT_WORKER_ROUTE = "internal_agent"
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


class _AgentWorker:
    def __init__(
        self,
        *,
        executor: AgentRuntimeExecutor,
        result_repository: AgentExecutionResultRepository,
        executor_name: str,
        node_grant_verifier: Any,
    ) -> None:
        if not callable(getattr(node_grant_verifier, "verify", None)):
            raise TypeError("node_grant_verifier must expose verify")
        self._executor = executor
        self._results = result_repository
        self._executor_name = str(executor_name)
        self._node_grants = node_grant_verifier

    async def execute_stream(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        if not isinstance(work_order, WorkOrderV3):
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_AUTHORITY_INVALID,
                "internal worker requires the admitted WorkOrderV3",
            )
        route = _internal_delegate_route(execution_context.get("worker_route"))
        if route.target_id != str(command.target_id or "").strip():
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_AUTHORITY_INVALID,
                "internal TaskNode delegate target differs from HostCommand target",
            )
        verified_grant = self._node_grants.verify(
            grant_ref=str(execution_context.get("node_grant_ref") or "").strip(),
            request_id=str(command.request_id or "").strip(),
            session_id=str(command.session_id or "").strip(),
            principal_id=str(command.principal_id or "").strip(),
            graph_id=str(execution_context.get("graph_id") or "").strip(),
            root_work_order_id=str(
                execution_context.get("work_order_id") or ""
            ).strip(),
            graph_revision=execution_context.get("graph_revision"),
            node_id=str(execution_context.get("node_id") or "").strip(),
            route=route.kind,
            target_id=str(route.target_id or "").strip(),
            parent_grant_ref=str(
                execution_context.get("parent_grant_ref") or ""
            ).strip(),
        )
        verified_payload = (
            verified_grant.to_dict()
            if callable(getattr(verified_grant, "to_dict", None))
            else dict(verified_grant)
            if isinstance(verified_grant, Mapping)
            else {}
        )
        if not verified_payload:
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_AUTHORITY_INVALID,
                "internal node grant verifier returned no durable projection",
            )
        legacy_order = work_order.to_agent_work_order()
        result = await asyncio.to_thread(self._executor.execute, legacy_order)
        if not isinstance(result, Mapping):
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_AUTHORITY_INVALID,
                "Agent executor returned a non-mapping result",
            )
        durable = self._results.append(
            command_id=command.command_id,
            work_order_id=work_order.work_order_id,
            result=dict(result),
        )
        status = str(durable["result_status"] or "").strip().upper()
        payload = dict(durable["result"])
        narrative = str(
            payload.get("result_text")
            or payload.get("summary")
            or payload.get("detail")
            or status
        ).strip()
        commit = {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE" if status == "EXECUTED" else "REJECT",
            "reason_code": None
            if status == "EXECUTED"
            else str(payload.get("reason_code") or f"AGENT_RESULT_{status}"),
            "request_id": command.request_id,
            "narrative": narrative,
            "route_type": INTERNAL_AGENT_WORKER_ROUTE,
            "target_id": command.target_id,
            "receipt_ref": durable["receipt_ref"],
            "result": payload,
            "audit_refs": {
                "path": "governed_task_runtime:internal_agent",
                "executor": self._executor_name,
                "command_id": command.command_id,
                "attempt_id": str(execution_context.get("attempt_id") or ""),
                "node_grant_ref": verified_payload["grant_ref"],
                "result_receipt_ref": durable["receipt_ref"],
            },
        }
        worker_budget = build_worker_budget_usage(
            usage_id=stable_worker_usage_id(
                graph_id=str(execution_context.get("graph_id") or ""),
                graph_revision=int(execution_context.get("graph_revision") or 0),
                node_id=str(execution_context.get("node_id") or ""),
                attempt_id=str(execution_context.get("attempt_id") or ""),
                route="delegate",
            ),
            observations=_trusted_agent_budget_observations(payload),
            include_agent_tool_calls=True,
            evidence_refs=(str(durable["receipt_ref"]),),
        )
        if worker_budget is not None:
            commit["budget_usage"] = worker_budget.to_payload()
        yield commit


class _AgentWorkerResolver:
    def __init__(self, *, canonical: _AgentWorker) -> None:
        self._canonical = canonical

    def resolve(self, *, executor: str, worker_route: str) -> _AgentWorker:
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


class _AgentProjectionReader:
    def __init__(self, repository: AgentExecutionResultRepository) -> None:
        self._repository = repository

    def load(self, receipt_ref: str) -> Mapping[str, Any] | None:
        durable = self._repository.get(receipt_ref)
        if durable is None:
            return None
        return _public_projection(durable)


class _AgentResultVerifier:
    def __init__(self, repository: AgentExecutionResultRepository) -> None:
        self._repository = repository

    def verify(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        evidence_refs: tuple[str, ...] | list[str],
        **_: Any,
    ) -> dict[str, Any]:
        receipt_ref = str(worker_commit.get("receipt_ref") or "").strip()
        durable = self._repository.get(receipt_ref) if receipt_ref else None
        durable_result = dict(durable.get("result") or {}) if durable else {}
        commit_result = dict(worker_commit.get("result") or {})
        passed = bool(
            durable
            and durable["command_id"] == command.command_id
            and durable["work_order_id"] == work_order.work_order_id
            and durable_result == commit_result
            and str(durable["result_status"]).upper() == "EXECUTED"
            and str(worker_commit.get("decision") or "").strip().upper() == "APPROVE"
        )
        reason = None if passed else "INTERNAL_AGENT_DURABLE_RESULT_INVALID"
        refs = list(evidence_refs)
        return {
            "schema_version": "Natsume.agent.verification_result.v1",
            "verification_id": _stable_id("verify", command.command_id),
            "work_order_id": work_order.work_order_id,
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "required": True,
            "status": "PASS" if passed else "FAIL",
            "reason_code": reason,
            "evidence_refs": refs,
            "checks": [
                {
                    "check_id": "durable-agent-result",
                    "status": "PASS" if passed else "FAIL",
                    "reason_code": reason,
                }
            ],
            "started_at_ms": command.created_at_ms,
            "finished_at_ms": command.created_at_ms,
        }


class _AgentResultAcceptance:
    def judge(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        evidence_refs: tuple[str, ...] | list[str],
        **_: Any,
    ) -> dict[str, Any]:
        result = dict(worker_commit.get("result") or {})
        inner_acceptance = (
            dict(result.get("acceptance") or {})
            if isinstance(result.get("acceptance"), Mapping)
            else {}
        )
        inner_decision = str(inner_acceptance.get("decision") or "").strip().upper()
        review_required = bool(result.get("review_required")) or inner_decision in {
            "REVIEW",
            "RETRY",
            "SPLIT",
        }
        inner_accepted = inner_decision in {"", "NOT_RUN", "ACCEPT"}
        accepted = (
            str(worker_commit.get("decision") or "").strip().upper() == "APPROVE"
            and str(result.get("status") or "").strip().upper() == "EXECUTED"
            and inner_accepted
            and not review_required
        )
        if accepted:
            reason = None
        elif review_required:
            reason = "INTERNAL_AGENT_REVIEW_REQUIRED"
        elif inner_decision and not inner_accepted:
            reason = f"INTERNAL_AGENT_ACCEPTANCE_{inner_decision}"
        else:
            reason = "INTERNAL_AGENT_ACCEPTANCE_FAILED"
        refs = list(evidence_refs)
        criteria_text = tuple(work_order.acceptance_criteria or ("result executed",))
        return {
            "schema_version": "Natsume.agent.acceptance_judgement.v1",
            "judgement_id": _stable_id("accept", command.command_id),
            "work_order_id": work_order.work_order_id,
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "decision": "ACCEPT" if accepted else "REVIEW",
            "reason_code": reason,
            "evidence_refs": refs,
            "criteria": [
                {
                    "criterion_id": _stable_id(
                        "criterion",
                        f"{command.command_id}:{index}:{criterion}",
                    ),
                    "criterion_text": criterion,
                    "verdict": "PASS" if accepted else "FAIL",
                    "evidence_refs": refs,
                    "deterministic": True,
                    "reason_code": reason,
                    "critic_verdict": None,
                }
                for index, criterion in enumerate(criteria_text, start=1)
            ],
            "critic_candidate": None,
            "started_at_ms": command.created_at_ms,
            "finished_at_ms": command.created_at_ms,
        }


class GovernedInternalRuntime:
    """Lifecycle-owned application facade; HTTP routes receive it by closure."""

    def __init__(
        self,
        *,
        application: GovernedCommandApplication,
        runtime_router: CanonicalRuntimeRouter,
        command_repository: Any,
        governor_grants: DurableGovernorGrantResolver,
        drain_timeout_s: float = _DEFAULT_DRAIN_TIMEOUT_S,
    ) -> None:
        self._application = application
        self._runtime_router = runtime_router
        self._commands = command_repository
        self._governor_grants = governor_grants
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
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_DRAIN_TIMEOUT,
                f"internal runtime still has {self._in_flight} in-flight command(s)",
            ) from exc

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
            candidate_command, candidate_order, candidate_graph = _build_contracts(
                work_order=legacy_order,
                request_id=request_id,
                session_id=session_id,
                ts_ms=ts_ms,
                governor_grant_ref=governor_grant_ref,
                command_authority=command_authority,
            )
            existing = self._commands.get_by_request(
                surface=candidate_command.surface.value,
                request_id=candidate_command.request_id,
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
                yield event
        finally:
            async with self._condition:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._condition.notify_all()

    def existing_command(self, *, request_id: str) -> Mapping[str, Any] | None:
        return self._commands.get_by_request(
            surface=HostCommandSurface.INTERNAL.value,
            request_id=str(request_id or "").strip(),
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
        result_ref = str(
            receipt.get("receipt_ref") or terminal.get("receipt_ref") or ""
        ).strip()
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
        if not result_ref:
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
            if waiting_review and str(review.get("review_id") or "").strip():
                # A pre-execution review has no worker receipt yet.  Leave the
                # ingress claim result unbound so the approved continuation can
                # atomically attach the eventual durable execution receipt.
                return terminal
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
                "terminal internal command has no durable result reference",
            )
        try:
            self._claim_results.complete_claim(
                claim_ref=claim_ref,
                result_ref=result_ref,
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
) -> GovernedInternalRuntime:
    review_store = getattr(task_runtime_container, "review_store", None)
    if review_store is None:
        raise TypeError("internal Agent Task Runtime review store is required")
    node_grant_service = NodeBoundGrantService(
        ledger=ledger,
        token_service=token_service,
    )
    node_grant_verifier = DurableNodeGrantVerifier(ledger=ledger)
    canonical_worker = _AgentWorker(
        executor=canonical_executor or AgentRuntimeExecutor(),
        result_repository=result_repository,
        executor_name="canonical",
        node_grant_verifier=node_grant_verifier,
    )
    coordinator = GovernedTaskCoordinator(
        task_store=task_runtime_container.task_store,
        outbox_publisher=task_runtime_container.outbox_publisher,
        outbox_factory=OutboxMessage,
        worker_resolver=_AgentWorkerResolver(canonical=canonical_worker),
        verifier=_AgentResultVerifier(result_repository),
        acceptance=_AgentResultAcceptance(),
        projection_reader=_AgentProjectionReader(result_repository),
        review_runtime=TaskNodeReviewRuntime(review_store),
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
    )
    return GovernedInternalRuntime(
        application=application,
        runtime_router=runtime_router,
        command_repository=task_runtime_container.command_repository,
        governor_grants=DurableGovernorGrantResolver(ledger),
    )


def _build_contracts(
    *,
    work_order: AgentWorkOrder,
    request_id: str,
    session_id: str,
    ts_ms: int,
    governor_grant_ref: str,
    command_authority: Mapping[str, Any],
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
        "type": "durable_agent_result",
        "required": ["decision", "result", "receipt_ref"],
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
    canonical_order = WorkOrderV3(
        work_order_id=work_order.work_order_id,
        domain=work_order.domain,
        run_mode=work_order.run_mode,
        objective=work_order.objective,
        workspace=deepcopy(dict(work_order.workspace or {})),
        input_pack=deepcopy(dict(work_order.input_pack or {})),
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


def _trusted_agent_budget_observations(value: Any) -> Mapping[str, Any]:
    result = value if isinstance(value, Mapping) else {}
    metadata = result.get("metadata") if isinstance(result, Mapping) else None
    if not isinstance(metadata, Mapping):
        return {}
    if str(metadata.get("execution_authority") or "").strip() != (
        "canonical_agent_executor"
    ):
        return {}
    return {
        "model_attempts": list(metadata.get("model_attempts") or []),
        "trace": dict(metadata.get("trace") or {})
        if isinstance(metadata.get("trace"), Mapping)
        else {},
    }


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


def _public_projection(durable: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(durable.get("result") or {})
    status = str(durable.get("result_status") or "").strip().upper()
    return {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE" if status == "EXECUTED" else "REJECT",
        "reason_code": None
        if status == "EXECUTED"
        else str(result.get("reason_code") or f"AGENT_RESULT_{status}"),
        "narrative": str(
            result.get("result_text")
            or result.get("summary")
            or result.get("detail")
            or status
        ).strip(),
        "route_type": INTERNAL_AGENT_WORKER_ROUTE,
        "target_id": "agent_runtime",
        "receipt_ref": str(durable.get("receipt_ref") or ""),
        "result": result,
        "audit_refs": {
            "path": "governed_task_runtime:durable_agent_projection",
            "result_receipt_ref": str(durable.get("receipt_ref") or ""),
        },
    }


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
