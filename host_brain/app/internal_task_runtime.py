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
from host_brain.persistence.task_repository import OutboxMessage
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.orchestration import (
    DurableGovernorGrantResolver,
    GovernedCommandApplication,
    GovernorGrantError,
    governor_grant_ref,
)
from host_brain.persistence.agent_execution_repository import (
    AgentExecutionResultRepository,
)
from host_brain.task_runtime import GovernedTaskCoordinator
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

_DEFAULT_DRAIN_TIMEOUT_S = 120.0
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_FORBIDDEN_DURABLE_KEY = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|credential|password|raw_?body|secret|signature|token)(?:$|_)",
    re.IGNORECASE,
)
_CALLER_AUTHORITY_KEYS = frozenset(
    {
        "runtime_assignment",
        "runtime_owner_confirmation",
        "task_runtime_owner_confirmation",
        "confirmed_owner",
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
    ) -> None:
        self._executor = executor
        self._results = result_repository
        self._executor_name = str(executor_name)

    async def execute_stream(
        self,
        *,
        command: HostCommand,
        work_order: AgentWorkOrder,
        execution_context: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        result = await asyncio.to_thread(self._executor.execute, work_order)
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
        yield {
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
                "result_receipt_ref": durable["receipt_ref"],
            },
        }


class _AgentWorkerResolver:
    def __init__(self, *, canonical: _AgentWorker) -> None:
        self._canonical = canonical

    def resolve(self, *, executor: str, worker_route: str) -> _AgentWorker:
        if str(worker_route or "").strip() != INTERNAL_AGENT_WORKER_ROUTE:
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
        work_order: AgentWorkOrder,
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
        work_order: AgentWorkOrder,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        evidence_refs: tuple[str, ...] | list[str],
        **_: Any,
    ) -> dict[str, Any]:
        accepted = (
            str(worker_commit.get("decision") or "").strip().upper() == "APPROVE"
            and str(dict(worker_commit.get("result") or {}).get("status") or "")
            .strip()
            .upper()
            == "EXECUTED"
        )
        reason = None if accepted else "INTERNAL_AGENT_ACCEPTANCE_FAILED"
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
            candidate_order = _sanitize_work_order(work_order)
            candidate_command, candidate_graph = _build_contracts(
                work_order=candidate_order,
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
            intent = _build_governor_intent(
                work_order=sanitized,
                request_id=request_ref,
                session_id=session_ref,
                ts_ms=int(ts_ms),
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
        if not claim_ref or not result_ref:
            raise GovernedInternalRuntimeError(
                INTERNAL_RUNTIME_INGRESS_COMPLETION_FAILED,
                "terminal internal command has no durable claim/result reference",
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
    result_repository: AgentExecutionResultRepository,
    runtime_router: CanonicalRuntimeRouter,
    owner_id: str,
    canonical_executor: AgentRuntimeExecutor | None = None,
) -> GovernedInternalRuntime:
    canonical_worker = _AgentWorker(
        executor=canonical_executor or AgentRuntimeExecutor(),
        result_repository=result_repository,
        executor_name="canonical",
    )
    coordinator = GovernedTaskCoordinator(
        task_store=task_runtime_container.task_store,
        outbox_publisher=task_runtime_container.outbox_publisher,
        outbox_factory=OutboxMessage,
        worker_resolver=_AgentWorkerResolver(canonical=canonical_worker),
        verifier=_AgentResultVerifier(result_repository),
        acceptance=_AgentResultAcceptance(),
        projection_reader=_AgentProjectionReader(result_repository),
        owner_id=owner_id,
    )
    application = GovernedCommandApplication(
        command_repository=task_runtime_container.command_repository,
        coordinator=coordinator,
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
) -> tuple[HostCommand, TaskGraph]:
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
    operation_fingerprint = _sha256_json(
        {
            "principal_id": principal_id,
            "input_sha256": canonical_input_sha,
            "work_order": work_order.to_dict(),
        }
    )
    node = TaskNode(
        node_id=node_id,
        dependencies=(),
        route=INTERNAL_AGENT_WORKER_ROUTE,
        input_refs=(input_ref, grant_ref),
        output_contract={
            "type": "durable_agent_result",
            "required": ["decision", "result", "receipt_ref"],
        },
        retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
        risk_level=max(1, int(dict(work_order.metadata or {}).get("risk_level") or 1)),
        acceptance_criteria=tuple(work_order.acceptance_criteria or ("result executed",)),
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
        business_scope=f"internal-agent:{_scope_token(work_order.domain)}",
        operation_fingerprint=operation_fingerprint,
        created_at_ms=int(ts_ms),
    )
    return command, graph


def _build_governor_intent(
    *,
    work_order: AgentWorkOrder,
    request_id: str,
    session_id: str,
    ts_ms: int,
) -> dict[str, Any]:
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
    safe = _safe_value(payload, path="work_order")
    if not isinstance(safe, dict):
        raise GovernedInternalRuntimeError(
            INTERNAL_RUNTIME_AUTHORITY_INVALID,
            "work_order is not a durable mapping",
        )
    sanitized = AgentWorkOrder.from_mapping(safe)
    sanitized.validate()
    return sanitized


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
