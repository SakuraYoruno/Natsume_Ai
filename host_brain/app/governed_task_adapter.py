"""Production adapter from Host surfaces to the governed Task Runtime.

Only the first frozen direct-skill vertical slice is cut over here.  Generic
chat remains on the legacy conversational path until its model/surface worker
is migrated, while every migrated skill command receives one immutable
HostCommand, one executor assignment, and one durable one-node graph.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from copy import deepcopy
import hashlib
import json
import re
import time
from typing import Any

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.agent_runtime.task_store import OutboxMessage
from host_brain.agent_runtime.work_order import AgentWorkOrder
from host_brain.orchestration import (
    DurableGovernorGrantResolver,
    GovernedCommandApplication,
    GovernorGrantError,
    governor_grant_ref,
)
from host_brain.task_runtime import GovernedTaskCoordinator
from natsume_contracts.task import HostCommand, HostCommandSurface, build_host_command


GOVERNED_HOST_RUNTIME_INACTIVE = "GOVERNED_HOST_RUNTIME_INACTIVE"
GOVERNED_HOST_ROUTE_UNSUPPORTED = "GOVERNED_HOST_ROUTE_UNSUPPORTED"
GOVERNED_HOST_AUTHORITY_INVALID = "GOVERNED_HOST_AUTHORITY_INVALID"
GOVERNED_HOST_GOVERNOR_GRANT_MISSING = "GOVERNED_HOST_GOVERNOR_GRANT_MISSING"
GOVERNED_HOST_DRAIN_TIMEOUT = "GOVERNED_HOST_DRAIN_TIMEOUT"
GOVERNED_HOST_WORKER_ROUTE = "host_skill"

_FORBIDDEN_DURABLE_KEYS = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|credential|password|raw_?body|"
    r"secret|signature|token)(?:$|_)",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_MAX_DURABLE_TEXT = 32_000
_ACCEPTANCE_CRITERION = "governed worker returned a durable accepted projection"
_DEFAULT_DRAIN_TIMEOUT_S = 120.0
_STABLE_REPLAY_COMMAND_FIELDS = (
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


class GovernedHostAdapterError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_HOST_AUTHORITY_INVALID)
        self.detail = str(detail or "governed Host adapter failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class _FrozenSkillWorker:
    def __init__(
        self,
        *,
        ledger: Any,
        skill_registry: Any,
        token_service: Any,
        explicit_dependencies: bool,
    ) -> None:
        self._ledger = ledger
        self._skill_registry = skill_registry
        self._token_service = token_service
        self._explicit_dependencies = bool(explicit_dependencies)

    async def execute_stream(
        self,
        *,
        command: HostCommand,
        work_order: AgentWorkOrder,
        execution_context: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        from host_brain.app.executive_runtime_core import (  # noqa: PLC0415
            _execute_skill,
            _log_skill_exec_row,
        )

        pack = dict(work_order.input_pack or {})
        intent_draft = _mapping(pack.get("intent_draft"))
        if str(intent_draft.get("route") or "").strip().lower() != "skill":
            raise GovernedHostAdapterError(
                GOVERNED_HOST_ROUTE_UNSUPPORTED,
                "frozen worker accepts only a direct skill intent",
            )
        target = str(intent_draft.get("target_id") or "").strip()
        if not target or target != command.target_id:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_ROUTE_UNSUPPORTED,
                "durable intent target differs from HostCommand.target_id",
            )
        governor_result = _load_governor_grant(
            ledger=self._ledger,
            request_id=command.request_id,
            expected_ref=command.governor_grant_ref,
        )
        # SQLiteLedger owns a thread-affine connection.  Execute the skill in
        # a worker thread, then persist its receipt on the owning event-loop
        # thread instead of silently losing the audit row.
        kwargs: dict[str, Any] = {"ledger": False}
        if self._explicit_dependencies:
            kwargs.update(
                {
                    "skill_registry": self._skill_registry,
                    "token_service_override": self._token_service,
                }
            )
        outcome = await asyncio.to_thread(
            _execute_skill,
            request_body={"args": dict(intent_draft.get("args") or {})},
            request_id=command.request_id,
            session_id=command.session_id,
            person_id=str(pack.get("person_id") or command.principal_id),
            intent_id=str(pack.get("intent_id") or "").strip() or None,
            skill_id=command.target_id,
            task_text=str(pack.get("task_text") or work_order.objective),
            user_message=str(pack.get("user_message") or work_order.objective),
            governor_result=governor_result,
            intent_draft=intent_draft,
            emotion_context=_mapping(pack.get("emotion_context")),
            render_target_lang=str(pack.get("render_target_lang") or "zh"),
            governed_execution_context=execution_context,
            **kwargs,
        )
        payload = _mapping(getattr(outcome, "payload", None))
        receipt_ref = str(payload.get("receipt_ref") or "").strip()
        narrative = str(getattr(outcome, "result_text", "") or "").strip()
        success = bool(getattr(outcome, "success", False))
        error_text = str(getattr(outcome, "error_text", "") or "").strip()
        tokens = governor_result.get("tokens")
        approval_ref = (
            str(tokens.get("approval_token_ref") or "").strip()
            if isinstance(tokens, Mapping)
            else ""
        )
        _log_skill_exec_row(
            request_id=command.request_id,
            intent_id=str(pack.get("intent_id") or "").strip()
            or f"intent::{command.request_id}::{command.target_id}",
            skill_name=command.target_id,
            result_status="SUCCESS" if success else "FAILED",
            result_code=None if success else (error_text or "GOVERNED_SKILL_EXECUTION_FAILED"),
            result_digest=narrative or error_text,
            approval_token_ref=approval_ref or None,
            ts_ms=int(time.time() * 1000),
            ledger=self._ledger,
            evidence_ref=receipt_ref,
        )
        durable_projection = self._ledger.get_event(receipt_ref)
        durable_status = (
            str(durable_projection.get("result_status") or "").strip().upper()
            if isinstance(durable_projection, Mapping)
            else ""
        )
        expected_status = "SUCCESS" if success else "FAILED"
        if durable_status != expected_status:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "skill result has no matching durable receipt projection",
            )
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE" if success else "REJECT",
            "reason_code": None if success else (
                error_text
                or "GOVERNED_SKILL_EXECUTION_FAILED"
            ),
            "request_id": command.request_id,
            "narrative": narrative,
            "route_type": "skill",
            "target_id": command.target_id,
            "receipt_ref": receipt_ref,
            "audit_refs": {
                "path": "governed_task_runtime:frozen_skill",
                "command_id": command.command_id,
                "attempt_id": str(execution_context.get("attempt_id") or ""),
                "skill_receipt_ref": receipt_ref,
            },
        }


class _FrozenWorkerResolver:
    def __init__(self, *, canonical: _FrozenSkillWorker, legacy: _FrozenSkillWorker) -> None:
        self._canonical = canonical
        self._legacy = legacy

    def resolve(self, *, executor: str, worker_route: str) -> _FrozenSkillWorker:
        if str(worker_route or "").strip() != GOVERNED_HOST_WORKER_ROUTE:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_ROUTE_UNSUPPORTED,
                f"unsupported frozen worker route: {worker_route}",
            )
        normalized = str(executor or "").strip().lower()
        if normalized == "canonical":
            return self._canonical
        if normalized == "legacy":
            return self._legacy
        raise GovernedHostAdapterError(
            GOVERNED_HOST_ROUTE_UNSUPPORTED,
            f"unsupported executor assignment: {executor}",
        )


class _DurableSkillProjectionReader:
    def __init__(self, ledger: Any) -> None:
        self._ledger = ledger

    def load(self, receipt_ref: str) -> Mapping[str, Any] | None:
        event = self._ledger.get_event(str(receipt_ref or "").strip())
        if not isinstance(event, Mapping):
            return None
        status = str(event.get("result_status") or "").strip().upper()
        if status not in {"SUCCESS", "IDEMPOTENT_REPLAY"}:
            return None
        return {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "request_id": str(event.get("request_id") or "").strip(),
            "narrative": str(event.get("result_digest") or "").strip(),
            "route_type": "skill",
            "target_id": str(event.get("skill_name") or "").strip(),
            "receipt_ref": str(event.get("evidence_ref") or receipt_ref).strip(),
            "audit_refs": {
                "path": "governed_task_runtime:durable_projection",
                "skill_receipt_ref": str(event.get("evidence_ref") or receipt_ref).strip(),
            },
        }


class _WorkerCommitVerifier:
    def verify(
        self,
        *,
        command: HostCommand,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        evidence_refs: tuple[str, ...] | list[str],
        **_: Any,
    ) -> dict[str, Any]:
        accepted = (
            str(worker_commit.get("decision") or "").strip().upper() == "APPROVE"
            and str(worker_commit.get("target_id") or "").strip() == command.target_id
            and bool(str(worker_commit.get("receipt_ref") or "").strip())
            and bool(str(worker_commit.get("narrative") or "").strip())
        )
        reason = None if accepted else "GOVERNED_WORKER_PROJECTION_INVALID"
        return {
            "schema_version": "Natsume.agent.verification_result.v1",
            "verification_id": _stable_id("verify", command.command_id),
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "required": True,
            "status": "PASS" if accepted else "FAIL",
            "reason_code": reason,
            "evidence_refs": list(evidence_refs),
            "checks": [
                {
                    "check_id": "durable-worker-projection",
                    "status": "PASS" if accepted else "FAIL",
                    "reason_code": reason,
                }
            ],
            "started_at_ms": command.created_at_ms,
            "finished_at_ms": command.created_at_ms,
        }


class _WorkerCommitAcceptance:
    def judge(
        self,
        *,
        command: HostCommand,
        execution_context: Mapping[str, Any],
        evidence_refs: tuple[str, ...] | list[str],
        **_: Any,
    ) -> dict[str, Any]:
        refs = list(evidence_refs)
        return {
            "schema_version": "Natsume.agent.acceptance_judgement.v1",
            "judgement_id": _stable_id("accept", command.command_id),
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "decision": "ACCEPT",
            "reason_code": None,
            "evidence_refs": refs,
            "criteria": [
                {
                    "criterion_id": "criterion-governed-durable-projection",
                    "criterion_text": _ACCEPTANCE_CRITERION,
                    "verdict": "PASS",
                    "evidence_refs": refs,
                    "deterministic": True,
                    "reason_code": None,
                    "critic_verdict": None,
                }
            ],
            "critic_candidate": None,
            "started_at_ms": command.created_at_ms,
            "finished_at_ms": command.created_at_ms,
        }


class GovernedHostRuntime:
    """Lifecycle-aware application facade published by the ASGI composition."""

    def __init__(
        self,
        *,
        application: GovernedCommandApplication,
        runtime_router: CanonicalRuntimeRouter,
        command_repository: Any,
        drain_timeout_s: float = _DEFAULT_DRAIN_TIMEOUT_S,
    ) -> None:
        self._application = application
        self._runtime_router = runtime_router
        self._command_repository = command_repository
        self._drain_timeout_s = max(0.1, float(drain_timeout_s))
        self._lifecycle = asyncio.Condition()
        self._in_flight = 0
        self._active = False
        self._accepting = False

    async def activate(self) -> None:
        async with self._lifecycle:
            self._active = True
            self._accepting = True

    async def close_admission(self) -> None:
        async with self._lifecycle:
            self._accepting = False

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

    async def aclose(self) -> None:
        await self.close_admission()
        await self.drain()
        async with self._lifecycle:
            self._active = False

    @staticmethod
    def supports(intent_draft: Mapping[str, Any] | None) -> bool:
        draft = dict(intent_draft or {})
        return (
            str(draft.get("route") or "").strip().lower() == "skill"
            and bool(str(draft.get("target_id") or draft.get("skill_id") or "").strip())
        )

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
            if not self.supports(intent_draft):
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_ROUTE_UNSUPPORTED,
                    "only a frozen direct-skill route is migrated",
                )
            if str(governor_result.get("decision") or "").strip().upper() != "APPROVE":
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_GOVERNOR_GRANT_MISSING,
                    "an approved official Governor decision is required",
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
            )
            existing = self._command_repository.get_by_request(
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
                if work_order is None or assignment is None or graph is None:
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
                yield event
        finally:
            async with self._lifecycle:
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._lifecycle.notify_all()


def build_governed_host_runtime(
    *,
    task_runtime_container: Any,
    ledger: Any,
    skill_registry: Any,
    token_service: Any,
    runtime_router: CanonicalRuntimeRouter,
    owner_id: str,
) -> GovernedHostRuntime:
    canonical = _FrozenSkillWorker(
        ledger=ledger,
        skill_registry=skill_registry,
        token_service=token_service,
        explicit_dependencies=True,
    )
    legacy = _FrozenSkillWorker(
        ledger=ledger,
        skill_registry=skill_registry,
        token_service=token_service,
        explicit_dependencies=False,
    )
    coordinator = GovernedTaskCoordinator(
        task_store=task_runtime_container.task_store,
        outbox_publisher=task_runtime_container.outbox_publisher,
        outbox_factory=OutboxMessage,
        worker_resolver=_FrozenWorkerResolver(canonical=canonical, legacy=legacy),
        verifier=_WorkerCommitVerifier(),
        acceptance=_WorkerCommitAcceptance(),
        projection_reader=_DurableSkillProjectionReader(ledger),
        owner_id=owner_id,
    )
    application = GovernedCommandApplication(
        command_repository=task_runtime_container.command_repository,
        coordinator=coordinator,
    )
    return GovernedHostRuntime(
        application=application,
        runtime_router=runtime_router,
        command_repository=task_runtime_container.command_repository,
    )


def _build_contracts(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: Mapping[str, Any],
    governor_result: Mapping[str, Any],
    person_id: str,
    sanitized_message: str,
    emotion_context: Mapping[str, Any] | None,
    render_target_lang: str,
    command_authority: Mapping[str, Any] | None,
) -> tuple[HostCommand, AgentWorkOrder, TaskGraph]:
    authority = dict(command_authority or {})
    has_ingress = bool(authority.get("ingress_claim_ref") or authority.get("claim_ref"))
    surface = HostCommandSurface.HOST_INPUT if has_ingress else HostCommandSurface.CHAT
    request_ref = _required_identifier(request_id, field_name="request_id")
    session_ref = _identifier_or_digest(session_id, prefix="session")
    target = str(intent_draft.get("target_id") or intent_draft.get("skill_id") or "").strip()
    target_ref = _required_identifier(target, field_name="target_id")
    identity_digest = _sha256_text(f"{surface.value}\n{request_ref}")
    suffix = identity_digest.split(":", 1)[1][:40]
    work_order_id = f"wo-host-{suffix}"
    graph_id = f"graph-host-{suffix}"
    node_id = f"node-host-{suffix}"

    input_sha256 = str(authority.get("input_sha256") or authority.get("body_sha256") or "").strip()
    if not input_sha256:
        input_sha256 = _sha256_text(str(sanitized_message or ""))
    elif not input_sha256.startswith("sha256:"):
        input_sha256 = f"sha256:{input_sha256.lower()}"
    input_ref = f"input://sha256/{input_sha256.split(':', 1)[1]}"
    principal_raw = str(authority.get("principal_id") or "").strip()
    principal_id = (
        _required_identifier(principal_raw, field_name="principal_id")
        if has_ingress
        else _identifier_or_digest(person_id or principal_raw or "anonymous", prefix="principal")
    )
    ingress_claim_ref = str(
        authority.get("ingress_claim_ref") or authority.get("claim_ref") or ""
    ).strip() or None
    ingress_audit_ref = str(
        authority.get("ingress_audit_ref") or authority.get("audit_ref") or ""
    ).strip() or None
    if bool(ingress_claim_ref) != bool(ingress_audit_ref):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "Secure Ingress claim and audit references must be supplied together",
        )
    grant_ref = governor_grant_ref(request_ref, governor_result)
    safe_intent = _safe_durable_value(dict(intent_draft or {}), path="intent_draft")
    if not isinstance(safe_intent, dict):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "intent_draft is not a durable mapping",
        )
    safe_intent["route"] = "skill"
    safe_intent["target_id"] = target_ref
    objective = str(sanitized_message or intent_draft.get("task") or target_ref).strip()
    objective = objective[:_MAX_DURABLE_TEXT] or f"execute skill {target_ref}"
    operation_fingerprint = _sha256_json(
        {
            "input_sha256": input_sha256,
            "principal_id": principal_id,
            "route_type": "skill",
            "target_id": target_ref,
            "args": safe_intent.get("args") if isinstance(safe_intent.get("args"), dict) else {},
        }
    )
    work_order = AgentWorkOrder(
        work_order_id=work_order_id,
        domain="skill",
        run_mode="worker",
        objective=objective,
        input_pack={
            "input_ref": input_ref,
            "user_message": objective,
            "task_text": str(intent_draft.get("task") or objective)[:_MAX_DURABLE_TEXT],
            "person_id": str(person_id or principal_id)[:255],
            "intent_id": str(governor_result.get("intent_id") or intent_draft.get("id") or "")[:255],
            "intent_draft": safe_intent,
            "emotion_context": _safe_durable_value(dict(emotion_context or {}), path="emotion_context"),
            "render_target_lang": str(render_target_lang or "zh")[:16],
        },
        allowed_actions=[target_ref],
        forbidden_actions=["reroute", "replan", "cross_executor_resume"],
        acceptance_criteria=[_ACCEPTANCE_CRITERION],
        verification_plan={"required": True, "policy": "durable_worker_projection"},
        approval_policy={"governor_grant_ref": grant_ref},
        metadata={
            "surface": surface.value,
            "graph_id": graph_id,
            "graph_revision": 1,
            "operation_fingerprint": operation_fingerprint,
        },
    )
    node = TaskNode(
        node_id=node_id,
        dependencies=(),
        route=GOVERNED_HOST_WORKER_ROUTE,
        input_refs=(input_ref, grant_ref),
        output_contract={
            "type": "host_skill_commit",
            "required": ["decision", "narrative", "receipt_ref"],
        },
        retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
        risk_level=1,
        acceptance_criteria=(_ACCEPTANCE_CRITERION,),
        status=TaskNodeStatus.READY,
        status_version=0,
    )
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id=work_order_id,
        revision=1,
        planner_ref=f"planner://governed-host/{suffix}",
        status=TaskGraphStatus.CREATED,
        created_at=int(ts_ms),
        nodes=(node,),
    )
    command = build_host_command(
        surface=surface,
        request_id=request_ref,
        session_id=session_ref,
        principal_id=principal_id,
        ingress_claim_ref=ingress_claim_ref,
        ingress_audit_ref=ingress_audit_ref,
        governor_grant_ref=grant_ref,
        input_ref=input_ref,
        input_sha256=input_sha256,
        root_work_order_id=work_order_id,
        graph_id=graph_id,
        graph_revision=1,
        route_type="skill",
        target_id=target_ref,
        business_scope=f"host-skill:{target_ref}",
        operation_fingerprint=operation_fingerprint,
        created_at_ms=int(ts_ms),
    )
    return command, work_order, graph


def _load_governor_grant(
    *,
    ledger: Any,
    request_id: str,
    expected_ref: str | None,
) -> dict[str, Any]:
    if not expected_ref:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_GOVERNOR_GRANT_MISSING,
            "HostCommand has no durable Governor grant reference",
        )
    try:
        return DurableGovernorGrantResolver(ledger).require(
            request_id=request_id,
            expected_ref=expected_ref,
        )
    except GovernorGrantError as exc:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_GOVERNOR_GRANT_MISSING,
            exc.detail,
        ) from exc


def _assert_stable_command_replay(candidate: HostCommand, durable: HostCommand) -> None:
    candidate_payload = candidate.to_dict()
    durable_payload = durable.to_dict()
    drift = [
        field_name
        for field_name in _STABLE_REPLAY_COMMAND_FIELDS
        if candidate_payload.get(field_name) != durable_payload.get(field_name)
    ]
    if drift:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "replayed Host command changed stable semantics: " + ", ".join(drift),
        )


def _safe_durable_value(value: Any, *, path: str) -> Any:
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip()
            if not key or _FORBIDDEN_DURABLE_KEYS.search(key):
                continue
            safe[key] = _safe_durable_value(raw_value, path=f"{path}.{key}")
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_durable_value(item, path=f"{path}[]") for item in value[:256]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_DURABLE_TEXT]
    return str(value)[:1_000]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _required_identifier(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            f"{field_name} is not a bounded identifier",
        )
    return text


def _identifier_or_digest(value: Any, *, prefix: str) -> str:
    text = str(value or "").strip()
    if _IDENTIFIER.fullmatch(text):
        return text
    return f"{prefix}-{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:40]}"


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _sha256_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        deepcopy(dict(value)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return _sha256_text(payload)


__all__ = [
    "GOVERNED_HOST_AUTHORITY_INVALID",
    "GOVERNED_HOST_GOVERNOR_GRANT_MISSING",
    "GOVERNED_HOST_ROUTE_UNSUPPORTED",
    "GOVERNED_HOST_RUNTIME_INACTIVE",
    "GovernedHostAdapterError",
    "GovernedHostRuntime",
    "build_governed_host_runtime",
]
