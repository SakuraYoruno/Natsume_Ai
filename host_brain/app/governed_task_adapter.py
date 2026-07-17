"""Production adapter from Host surfaces to the universal governed runtime.

Direct chat/skill/delegate routes and searched multi-node plans all enter the
same immutable HostCommand/WorkOrderV3/TaskGraph admission and coordinator.
Route workers receive only frozen node input; planning never runs inside a
worker or during graph execution.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from copy import deepcopy
import hashlib
import inspect
import json
import logging
import re
import time
from typing import Any

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
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
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
from host_brain.orchestration.host_route_contracts import (
    build_direct_host_route_contracts,
)
from host_brain.orchestration.failure_replan_intent import (
    failure_replan_reference_from_intent,
)
from host_brain.orchestration.plan_intent_search import build_plan_intent_search
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
from host_brain.app.governed_task_authority import (
    GOVERNED_HOST_AUTHORITY_INVALID,
    GovernedHostAdapterError,
    assert_stable_command_replay as _assert_stable_command_replay,
    assert_stable_work_order_replay as _assert_stable_work_order_replay,
    assert_work_order_command_binding as _assert_work_order_command_binding,
    mapping as _mapping,
    planning_node_binding as _planning_node_binding,
    require_fail_closed_direct_skill_work_order as _require_fail_closed_direct_skill_work_order,
    require_fail_closed_governed_work_order as _require_fail_closed_governed_work_order,
)
from host_brain.app.governed_skill_execution_ports import (
    GovernedSkillExecutionPorts,
    build_governed_skill_execution_ports,
    execute_frozen_governed_skill,
    record_governed_skill_execution,
)
from host_brain.task_runtime import GovernedTaskCoordinator
from host_brain.task_runtime.governed_workers import GovernedFrozenNodeWorker
from host_brain.task_runtime.review_runtime import TaskNodeReviewRuntime
from host_brain.task_runtime.worker_registry import GovernedWorkerRegistry
from host_brain.task_runtime.worker_budget_usage import (
    build_worker_budget_usage,
    stable_worker_usage_id,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
)
from natsume_contracts.task import HostCommand, HostCommandSurface, build_host_command


_logger = logging.getLogger(__name__)

GOVERNED_HOST_RUNTIME_INACTIVE = "GOVERNED_HOST_RUNTIME_INACTIVE"
GOVERNED_HOST_ROUTE_UNSUPPORTED = "GOVERNED_HOST_ROUTE_UNSUPPORTED"
GOVERNED_HOST_GOVERNOR_GRANT_MISSING = "GOVERNED_HOST_GOVERNOR_GRANT_MISSING"
GOVERNED_HOST_DRAIN_TIMEOUT = "GOVERNED_HOST_DRAIN_TIMEOUT"
GOVERNED_HOST_ACCEPTANCE_EFFECTS_UNAVAILABLE = (
    "GOVERNED_HOST_ACCEPTANCE_EFFECTS_UNAVAILABLE"
)
GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE = (
    "GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE"
)
GOVERNED_HOST_WORKER_ROUTE = "skill"

_MEMORY_SEARCH_SCOPES = frozenset(
    {"recent_episode", "long_term", "line", "hybrid"}
)
_MEMORY_SEARCH_SCOPE_ALIASES = {
    "recent": "recent_episode",
    "recent_episodic": "recent_episode",
    "episode": "recent_episode",
    "episodic": "recent_episode",
    "semantic": "long_term",
    "longterm": "long_term",
    "long_term_semantic": "long_term",
    "quote": "line",
    "quotes": "line",
    "dialogue": "line",
    "lines": "line",
    "line_semantic": "line",
    "mixed": "hybrid",
    "auto": "",
}
_MEMORY_RECENT_EPISODE_TAGS = frozenset(
    {"conversation", "event", "task", "reminder", "dream", "diary"}
)
_MEMORY_LINE_SEMANTIC_TAGS = frozenset(
    {"quote", "line", "dialogue", "style"}
)
_MEMORY_LONG_TERM_TAGS = frozenset(
    {"fact", "preference", "bond", "emotion"}
)

_FORBIDDEN_DURABLE_KEYS = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|credential|password|raw_?body|"
    r"secret|signature|token)(?:$|_)",
    re.IGNORECASE,
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
_MAX_DURABLE_TEXT = 32_000
_ACCEPTANCE_CRITERION = "governed worker returned a durable accepted projection"
_DEFAULT_DRAIN_TIMEOUT_S = 120.0
_ACCEPTANCE_EFFECT_POLL_SECONDS = 0.25
_DIRECT_SKILL_BUDGET_LIMITS = BudgetAmount(
    token_count=16_384,
    tool_call_count=8,
    time_ms=120_000,
    concurrency=1,
    retry_count=1,
    model_cost_micros=500_000,
)
class _FrozenSkillWorker:
    def __init__(
        self,
        *,
        ledger: Any,
        execution_ports: GovernedSkillExecutionPorts,
        node_grant_verifier: DurableNodeGrantVerifier,
        projections: Any | None = None,
    ) -> None:
        self._ledger = ledger
        self._execution_ports = execution_ports
        self._node_grant_verifier = node_grant_verifier
        self._projections = projections

    async def execute_stream(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        if str(command.route_type or "").strip().lower() == "skill":
            _require_fail_closed_direct_skill_work_order(work_order)
            _assert_work_order_command_binding(work_order, command)
        else:
            _require_fail_closed_governed_work_order(work_order, command=command)
        pack = dict(work_order.input_pack or {})
        node_id = str(execution_context.get("node_id") or "").strip()
        binding = _planning_node_binding(work_order, node_id=node_id)
        target = str(binding.get("target_id") or "").strip()
        route = str(binding.get("worker_route") or "").strip().lower()
        direct_intent = _mapping(pack.get("intent_draft"))
        if route == "skill" and str(direct_intent.get("route") or "").strip().lower() == "skill":
            intent_draft = direct_intent
        else:
            approved_inputs = _mapping(pack.get("approved_candidate_inputs"))
            candidate_id = str(binding.get("candidate_id") or "").strip()
            candidate_input = _mapping(approved_inputs.get(candidate_id))
            intent_draft = {
                "route": route,
                "target_id": target,
                "task": str(candidate_input.get("objective") or binding.get("objective") or "").strip(),
                "args": _mapping(candidate_input.get("args")),
            }
        if str(intent_draft.get("route") or "").strip().lower() != "skill":
            raise GovernedHostAdapterError(
                GOVERNED_HOST_ROUTE_UNSUPPORTED,
                "frozen worker accepts only a direct skill intent",
            )
        if not target or str(intent_draft.get("target_id") or "").strip() != target:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_ROUTE_UNSUPPORTED,
                "durable skill intent target differs from its frozen TaskNode binding",
            )
        if target not in set(work_order.capability_requirements):
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "direct skill target is absent from WorkOrderV3 capability lineage",
            )
        execution_args = _mapping(intent_draft.get("args"))
        node_grant_ref = str(execution_context.get("node_grant_ref") or "").strip()
        if not node_grant_ref:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "frozen skill execution has no node-bound child grant",
            )
        verified_grant = self._node_grant_verifier.verify(
            grant_ref=node_grant_ref,
            parent_grant_ref=str(command.governor_grant_ref or "").strip(),
            request_id=command.request_id,
            session_id=command.session_id,
            principal_id=command.principal_id,
            graph_id=str(execution_context.get("graph_id") or ""),
            graph_revision=int(execution_context.get("graph_revision") or 0),
            root_work_order_id=command.root_work_order_id,
            node_id=node_id,
            route="skill",
            target_id=target,
            attempt_id=str(execution_context.get("attempt_id") or "").strip(),
            lease_id=str(execution_context.get("lease_id") or "").strip(),
            lease_owner_id=str(
                execution_context.get("lease_owner_id") or ""
            ).strip(),
            fencing_token=execution_context.get("fencing_token"),
        )
        if verified_grant.grant_ref != node_grant_ref:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "durable verifier returned a different node grant",
            )
        governor_result = _load_governor_grant(
            ledger=self._ledger,
            request_id=command.request_id,
            expected_ref=node_grant_ref,
        )
        child_intent = _mapping(_mapping(governor_result.get("snapshot_refs")).get("node_intent"))
        child_intent_id = str(child_intent.get("id") or "").strip()
        if child_intent_id != verified_grant.intent_id:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "node grant child intent differs from its durable verifier projection",
            )
        _require_frozen_skill_dependencies(
            ports=self._execution_ports,
            target=target,
            args=execution_args,
        )
        # SQLiteLedger owns a thread-affine connection.  Execute the skill in
        # a worker thread, then persist its receipt on the owning event-loop
        # thread instead of silently losing the audit row.
        outcome = await asyncio.to_thread(
            execute_frozen_governed_skill,
            ports=self._execution_ports,
            durable_authority_ledger=self._ledger,
            request_body={"args": execution_args},
            request_id=command.request_id,
            session_id=command.session_id,
            person_id=str(pack.get("person_id") or command.principal_id),
            intent_id=child_intent_id,
            skill_id=target,
            task_text=str(intent_draft.get("task") or binding.get("objective") or work_order.objective),
            user_message=str(pack.get("user_message") or work_order.objective),
            governor_result=governor_result,
            intent_draft=child_intent,
            emotion_context=_mapping(pack.get("emotion_context")),
            render_target_lang=str(pack.get("render_target_lang") or "zh"),
            governed_execution_context=execution_context,
            execution_args_override=execution_args,
            verified_node_grant=verified_grant.to_dict(),
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
        record_governed_skill_execution(
            ledger=self._ledger,
            request_id=command.request_id,
            intent_id=child_intent_id,
            skill_name=target,
            result_status="SUCCESS" if success else "FAILED",
            result_code=None if success else (error_text or "GOVERNED_SKILL_EXECUTION_FAILED"),
            result_digest=narrative or error_text,
            approval_token_ref=approval_ref or None,
            ts_ms=int(time.time() * 1000),
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
        commit = {
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
            "target_id": target,
            "receipt_ref": receipt_ref,
            "audit_refs": {
                "path": "governed_task_runtime:frozen_skill",
                "command_id": command.command_id,
                "attempt_id": str(execution_context.get("attempt_id") or ""),
                "skill_receipt_ref": receipt_ref,
            },
        }
        worker_budget = build_worker_budget_usage(
            usage_id=stable_worker_usage_id(
                graph_id=str(execution_context.get("graph_id") or ""),
                graph_revision=int(execution_context.get("graph_revision") or 0),
                node_id=node_id,
                attempt_id=str(execution_context.get("attempt_id") or ""),
                route="skill",
            ),
            observations=_trusted_skill_budget_observations(payload.get("result")),
            include_agent_tool_calls=True,
            evidence_refs=((receipt_ref,) if receipt_ref else ()),
        )
        if worker_budget is not None:
            commit["budget_usage"] = worker_budget.to_payload()
        if self._projections is not None:
            projection_body = {
                **commit,
                "command_id": command.command_id,
                "graph_id": str(execution_context.get("graph_id") or ""),
                "graph_revision": int(execution_context.get("graph_revision") or 0),
                "node_id": node_id,
                "attempt_id": str(execution_context.get("attempt_id") or ""),
                "node_grant_ref": node_grant_ref,
            }
            projection_body.pop("receipt_ref", None)
            payload_digest = _sha256_json(projection_body)
            projection_id = (
                "worker-projection://skill/"
                f"{payload_digest.split(':', 1)[1]}"
            )
            durable = self._projections.stage(
                projection_id=projection_id,
                projection={
                    **projection_body,
                    "projection_id": projection_id,
                    "payload_digest": payload_digest,
                    "schema_version": "Natsume.task_runtime.worker_projection.v1",
                },
            )
            if (
                not isinstance(durable, Mapping)
                or str(durable.get("projection_id") or "") != projection_id
                or str(durable.get("payload_digest") or "") != payload_digest
            ):
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_AUTHORITY_INVALID,
                    "skill result could not be frozen in the canonical worker projection journal",
                )
            commit["receipt_ref"] = projection_id
        yield commit


def _require_frozen_skill_dependencies(
    *,
    ports: GovernedSkillExecutionPorts,
    target: str,
    args: Mapping[str, Any],
) -> None:
    """Reject a frozen Skill whose required startup snapshot is unavailable."""

    skill_id = str(target or "").strip().lower()
    missing_dependency = ""
    if skill_id == "resource_manager_status":
        if ports.resource_manager is None:
            missing_dependency = "resource_manager"
    elif skill_id == "memory_store":
        if ports.episodic_store is None:
            missing_dependency = "episodic_store"
    elif skill_id == "memory_forget":
        if not bool(args.get("dry_run", False)) and ports.episodic_store is None:
            missing_dependency = "episodic_store"
    elif skill_id == "memory_search":
        scope = _frozen_memory_search_scope(args)
        if scope == "recent_episode" and ports.episodic_store is None:
            missing_dependency = "episodic_store"
        elif scope in {"long_term", "line"} and ports.semantic_recaller is None:
            missing_dependency = "semantic_recaller"
        elif (
            scope == "hybrid"
            and ports.episodic_store is None
            and ports.semantic_recaller is None
        ):
            missing_dependency = "episodic_store_or_semantic_recaller"
    if missing_dependency:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE,
            f"frozen Skill {skill_id} requires composed {missing_dependency}",
        )


def _frozen_memory_search_scope(args: Mapping[str, Any]) -> str:
    """Mirror the declarative memory normalizer without executing the Skill."""

    raw_scope = str(args.get("search_scope") or args.get("scope") or "").strip().lower()
    scope = _MEMORY_SEARCH_SCOPE_ALIASES.get(raw_scope, raw_scope)
    if scope in _MEMORY_SEARCH_SCOPES:
        return scope
    memory_type = str(args.get("memory_type") or "all").strip().lower()
    if memory_type not in {"episodic", "semantic", "working", "all"}:
        memory_type = "all"
    tags_raw = args.get("tags")
    tags = (
        {
            str(tag or "").strip().lower()
            for tag in tags_raw
            if str(tag or "").strip()
        }
        if isinstance(tags_raw, list)
        else set()
    )
    if memory_type == "semantic":
        return "long_term"
    if memory_type in {"episodic", "working"}:
        return "recent_episode"
    if tags & _MEMORY_LINE_SEMANTIC_TAGS:
        return "line"
    if tags & _MEMORY_RECENT_EPISODE_TAGS:
        return "recent_episode"
    if tags & _MEMORY_LONG_TERM_TAGS:
        return "long_term"
    return "recent_episode"


def _trusted_skill_budget_observations(value: Any) -> Mapping[str, Any]:
    """Expose only the canonical AgentExecutor's server-owned usage metadata."""

    result = value if isinstance(value, Mapping) else {}
    metadata = result.get("metadata") if isinstance(result, Mapping) else None
    if not isinstance(metadata, Mapping):
        return {}
    if str(metadata.get("execution_authority") or "").strip() != "canonical_agent_executor":
        return {}
    return {
        "model_attempts": list(metadata.get("model_attempts") or []),
        "trace": dict(metadata.get("trace") or {})
        if isinstance(metadata.get("trace"), Mapping)
        else {},
    }


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


class _CompositeProjectionReader:
    def __init__(self, *, skill: _DurableSkillProjectionReader, workers: Any | None) -> None:
        self._skill = skill
        self._workers = workers

    def load(self, receipt_ref: str) -> Mapping[str, Any] | None:
        ref = str(receipt_ref or "").strip()
        if ref.startswith("worker-projection://") and self._workers is not None:
            value = self._workers.load(ref)
            return dict(value) if isinstance(value, Mapping) else None
        return self._skill.load(ref)


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
            and str(worker_commit.get("target_id") or "").strip()
            == str(execution_context.get("target_id") or command.target_id).strip()
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
        raw_criteria = execution_context.get("node_acceptance_criteria")
        criteria_texts = tuple(
            str(value or "").strip()
            for value in (
                raw_criteria
                if isinstance(raw_criteria, (list, tuple))
                else (_ACCEPTANCE_CRITERION,)
            )
            if str(value or "").strip()
        ) or (_ACCEPTANCE_CRITERION,)
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
                    "criterion_id": f"criterion-governed-durable-projection-{index}",
                    "criterion_text": criterion_text,
                    "verdict": "PASS",
                    "evidence_refs": refs,
                    "deterministic": True,
                    "reason_code": None,
                    "critic_verdict": None,
                }
                for index, criterion_text in enumerate(criteria_texts, start=1)
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
        task_store: Any | None = None,
        supported_routes: frozenset[str] = frozenset({"skill"}),
        planning_enabled: bool = False,
        acceptance_effect_dispatcher: AcceptanceEffectDispatcher | None = None,
        drain_timeout_s: float = _DEFAULT_DRAIN_TIMEOUT_S,
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
        self._acceptance_effect_stop = asyncio.Event()
        self._acceptance_effect_task: asyncio.Task[None] | None = None
        self._drain_timeout_s = max(0.1, float(drain_timeout_s))
        self._lifecycle = asyncio.Condition()
        self._in_flight = 0
        self._active = False
        self._accepting = False

    async def activate(self) -> None:
        dispatcher = self._acceptance_effect_dispatcher
        if dispatcher is not None:
            # Recover durable effects left PENDING (or behind an expired
            # claim) by an earlier process before this runtime accepts new
            # Host commands.  The dispatcher is fence-protected, so replaying
            # a COMMITTED effect cannot invoke its handler again.
            await dispatcher.drain_ready(limit=1_000)
        async with self._lifecycle:
            self._active = True
            self._accepting = True
        if dispatcher is not None and self._acceptance_effect_task is None:
            self._acceptance_effect_stop.clear()
            self._acceptance_effect_task = asyncio.create_task(
                self._run_acceptance_effect_recovery(),
                name="governed-acceptance-effect-recovery",
            )

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
        self._acceptance_effect_stop.set()
        effect_task = self._acceptance_effect_task
        self._acceptance_effect_task = None
        if effect_task is not None:
            try:
                await asyncio.wait_for(
                    effect_task,
                    timeout=min(5.0, self._drain_timeout_s),
                )
            except TimeoutError:
                effect_task.cancel()
                with suppress(asyncio.CancelledError):
                    await effect_task
        async with self._lifecycle:
            self._active = False

    async def _run_acceptance_effect_recovery(self) -> None:
        dispatcher = self._acceptance_effect_dispatcher
        if dispatcher is None:
            return
        while not self._acceptance_effect_stop.is_set():
            try:
                await asyncio.wait_for(
                    self._acceptance_effect_stop.wait(),
                    timeout=_ACCEPTANCE_EFFECT_POLL_SECONDS,
                )
                continue
            except TimeoutError:
                pass
            try:
                await dispatcher.drain_ready(limit=100)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Durable claims remain recoverable through their lease/fence;
                # a transient store or transport error must not kill the
                # process-owned recovery loop.
                _logger.warning(
                    "acceptance effect recovery pass failed: %s: %s",
                    type(exc).__name__,
                    str(exc)[:500],
                )

    def supports(self, intent_draft: Mapping[str, Any] | None) -> bool:
        draft = dict(intent_draft or {})
        route = str(draft.get("route") or "").strip().lower()
        if route == "plan":
            steps = draft.get("planned_steps")
            if not isinstance(steps, list):
                steps = draft.get("steps")
            return self._planning_enabled and isinstance(steps, list) and bool(steps)
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
                    HostCommandSurface.GATEWAY,
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
            route = str(intent_draft.get("route") or "").strip().lower()
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
            if route == "plan":
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
                )
            else:
                authority = dict(command_authority or {})
                surface = _resolve_host_command_surface(
                    request_body=request_body,
                    authority=authority,
                )
                ingress_claim_ref, ingress_audit_ref = _ingress_references(authority)
                contracts = build_direct_host_route_contracts(
                    surface=surface,
                    request_id=request_id,
                    session_id=session_id,
                    principal_id=_resolve_host_command_principal(
                        person_id=person_id,
                        authority=authority,
                    ),
                    ts_ms=ts_ms,
                    intent_draft=intent_draft,
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
                    frozen_context=frozen_context,
                )
                candidate_command = contracts.command
                candidate_work_order = contracts.work_order
                candidate_graph = contracts.graph
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
                    HostCommandSurface.GATEWAY,
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
        adapter = build_plan_intent_search(
            intent_draft,
            governor_result=governor_result,
            request_id=request_id,
            input_refs=(input_ref,),
            frozen_context=frozen_context,
        )
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
        )
        replan = failure_replan_reference_from_intent(intent_draft)
        if replan is None:
            prepared = await service.prepare_initial(
                adapter.request,
                authority=authority,
                created_at=int(ts_ms),
            )
        else:
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
        audit_refs = dict(projected.get("audit_refs") or {})
        audit_refs["acceptance_effects"] = {
            "count": len(results),
            "committed": statuses.count("COMMITTED"),
            "failed": statuses.count("FAILED"),
            "pending": sum(status not in {"COMMITTED", "FAILED"} for status in statuses),
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
    cancellation_authority_verifier: Any | None = None,
    runtime_router: CanonicalRuntimeRouter,
    owner_id: str,
    allow_uncomposed_acceptance_effects: bool = False,
) -> GovernedHostRuntime:
    from host_brain.app.route_execution_ports import (  # noqa: PLC0415
        build_governed_route_execution_ports,
        dispatch_governed_delegate,
        stream_governed_chat_with_context,
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
    skill_execution_ports = build_governed_skill_execution_ports(
        skill_registry=skill_registry,
        capability_broker=capability_broker,
        model_attempt_service=model_attempt_service,
        production_authority_verifier=production_authority_verifier,
        resource_manager=resource_manager,
        episodic_store=episodic_store,
        semantic_recaller=semantic_recaller,
    )
    projection_store = getattr(task_runtime_container, "worker_projection_store", None)
    canonical = _FrozenSkillWorker(
        ledger=ledger,
        execution_ports=skill_execution_ports,
        node_grant_verifier=node_grant_verifier,
        projections=projection_store,
    )
    chat_worker = None
    delegate_worker = None
    if projection_store is not None:
        route_execution_ports = build_governed_route_execution_ports()
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
        )
    worker_registry = GovernedWorkerRegistry(
        chat=chat_worker,
        skill=canonical,
        delegate=delegate_worker,
    )
    coordinator = GovernedTaskCoordinator(
        task_store=task_runtime_container.task_store,
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
    effect_dispatcher = None
    try:
        from host_brain.app.acceptance_effect_handlers import (  # noqa: PLC0415
            build_production_acceptance_effect_handlers,
        )
        from host_brain.composition.life import PROCESS_LIFE_PROVIDER  # noqa: PLC0415

        PROCESS_LIFE_PROVIDER.require()
        effect_dispatcher = AcceptanceEffectDispatcher(
            task_store=task_runtime_container.task_store,
            handlers=build_production_acceptance_effect_handlers(),
            owner_id=f"{owner_id}:acceptance-effects",
        )
    except RuntimeError as exc:
        if str(exc) != "PROCESS_LIFE_AUTHORITY_NOT_COMPOSED":
            raise
        if not allow_uncomposed_acceptance_effects:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_ACCEPTANCE_EFFECTS_UNAVAILABLE,
                "production governed runtime requires the composed Life authority "
                "and Acceptance Effect dispatcher",
            ) from exc
    return GovernedHostRuntime(
        application=application,
        runtime_router=runtime_router,
        command_repository=task_runtime_container.command_repository,
        task_store=task_runtime_container.task_store,
        supported_routes=frozenset(
            {"skill"}
            | ({"chat", "delegate"} if projection_store is not None else set())
        ),
        planning_enabled=projection_store is not None,
        acceptance_effect_dispatcher=effect_dispatcher,
    )


def _build_frozen_context(
    *,
    request_body: Any,
    surface_user_message: str,
    emotion_context: Mapping[str, Any] | None,
    emotion_snapshot: Mapping[str, Any] | None,
    bond_summary: Mapping[str, Any] | None,
    retrieval_evidence_items: Sequence[Mapping[str, Any]],
    retrieval_summary: Mapping[str, Any] | None,
    llm_runtime: Mapping[str, Any] | None,
    pii_ingress_stats: Mapping[str, Any] | None,
    motive_state: Mapping[str, Any] | None,
    render_target_lang: str,
) -> dict[str, Any]:
    attachments = [
        dict(item)
        for item in tuple(getattr(request_body, "attachments", ()) or ())
        if isinstance(item, Mapping)
    ]
    frozen = {
        "surface_user_message": str(surface_user_message or "")[:_MAX_DURABLE_TEXT],
        "prepared_emotion_context": dict(emotion_context or {}),
        "emotion_snapshot": dict(emotion_snapshot or {}),
        "bond_summary": dict(bond_summary or {}),
        "retrieval_evidence_items": [
            dict(item) for item in tuple(retrieval_evidence_items or ())
        ],
        "retrieval_summary": dict(retrieval_summary or {}),
        "llm_runtime": dict(llm_runtime or {}),
        "pii_ingress_stats": dict(pii_ingress_stats or {}),
        "motive_state": dict(motive_state or {}),
        "prepared_render_target_lang": str(render_target_lang or "zh")[:16],
        "request_body": {
            "input_source": str(getattr(request_body, "input_source", "") or "").strip(),
            "response_form": str(getattr(request_body, "response_form", "") or "").strip(),
            "render_policy": dict(getattr(request_body, "render_policy", {}) or {}),
            "attachments": attachments,
            "multimodal_observations": dict(
                getattr(request_body, "multimodal_observations", {}) or {}
            ),
            "attachment_summary": str(
                getattr(request_body, "attachment_summary", "") or ""
            ).strip(),
        },
        # Delivery is never exposed to the speculative worker.  The acceptance
        # layer may use this frozen, authority-free destination projection when
        # it builds a graph-scoped delivery intent.
        "delivery_context": {
            "reply_channel": str(getattr(request_body, "reply_channel", "") or "").strip(),
            "reply_target": str(getattr(request_body, "reply_target", "") or "").strip(),
        },
    }
    safe = _safe_durable_value(frozen, path="frozen_context")
    if not isinstance(safe, dict):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "frozen Host context is not a durable mapping",
        )
    return safe


def _ingress_references(
    authority: Mapping[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Normalize the only two durable references exported by Secure Ingress.

    The Host runtime accepts both the legacy generic names used by the input
    adapter and the explicit names used by other surfaces, but never permits a
    half-bound ingress identity.  Keeping this normalization here makes every
    direct route and a compiled plan freeze the same authority tuple.
    """

    source = dict(authority or {})
    claim_ref = str(
        source.get("ingress_claim_ref") or source.get("claim_ref") or ""
    ).strip() or None
    audit_ref = str(
        source.get("ingress_audit_ref") or source.get("audit_ref") or ""
    ).strip() or None
    if bool(claim_ref) != bool(audit_ref):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "Secure Ingress claim and audit references must be supplied together",
        )
    return claim_ref, audit_ref


def _resolve_host_command_surface(
    *,
    request_body: Any,
    authority: Mapping[str, Any] | None,
) -> HostCommandSurface:
    """Derive the durable Host surface from the authenticated ingress path.

    Caller-provided ``input_source`` alone is not an authority.  A Gateway
    surface is recorded only when it carries the exact Secure Ingress claim and
    audit refs; otherwise a normal Chat request remains Chat.
    """

    claim_ref, _audit_ref = _ingress_references(authority)
    if not claim_ref:
        return HostCommandSurface.CHAT
    if isinstance(request_body, Mapping):
        raw_source = request_body.get("input_source") or request_body.get("source")
    else:
        raw_source = getattr(request_body, "input_source", None)
    source = str(raw_source or "").strip().upper()
    return (
        HostCommandSurface.GATEWAY
        if source == "GATEWAY"
        else HostCommandSurface.HOST_INPUT
    )


def _resolve_host_command_principal(
    *,
    person_id: str,
    authority: Mapping[str, Any] | None,
) -> str:
    """Use the authenticated ingress principal when one owns the command."""

    claim_ref, _audit_ref = _ingress_references(authority)
    if claim_ref:
        return _required_identifier(
            dict(authority or {}).get("principal_id"),
            field_name="principal_id",
        )
    return _identifier_or_digest(person_id or "anonymous", prefix="principal")


def _resolve_host_input_sha256(
    *,
    sanitized_message: str,
    authority: Mapping[str, Any] | None,
) -> str:
    """Prefer the authenticated body digest and reject malformed substitutions."""

    raw = str(
        dict(authority or {}).get("input_sha256")
        or dict(authority or {}).get("body_sha256")
        or ""
    ).strip().lower()
    if not raw:
        return _sha256_text(sanitized_message)
    digest = raw.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "Secure Ingress body digest must be a sha256 hex digest",
        )
    return f"sha256:{digest}"


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
    frozen_context: Mapping[str, Any] | None = None,
) -> tuple[HostCommand, WorkOrderV3, TaskGraph]:
    authority = dict(command_authority or {})
    ingress_claim_ref, ingress_audit_ref = _ingress_references(authority)
    surface = _resolve_host_command_surface(
        request_body=request_body,
        authority=authority,
    )
    request_ref = _required_identifier(request_id, field_name="request_id")
    session_ref = _identifier_or_digest(session_id, prefix="session")
    target = str(intent_draft.get("target_id") or intent_draft.get("skill_id") or "").strip()
    target_ref = _required_identifier(target, field_name="target_id")
    identity_digest = _sha256_text(f"{surface.value}\n{request_ref}")
    suffix = identity_digest.split(":", 1)[1][:40]
    work_order_id = f"wo-host-{suffix}"
    graph_id = f"graph-host-{suffix}"
    node_id = f"node-host-{suffix}"

    input_sha256 = _resolve_host_input_sha256(
        sanitized_message=str(sanitized_message or ""),
        authority=authority,
    )
    input_ref = f"input://sha256/{input_sha256.split(':', 1)[1]}"
    principal_id = _resolve_host_command_principal(
        person_id=person_id,
        authority=authority,
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
            "request_id": request_ref,
            "input_sha256": input_sha256,
            "principal_id": principal_id,
            "route_type": "skill",
            "target_id": target_ref,
            "args": safe_intent.get("args") if isinstance(safe_intent.get("args"), dict) else {},
        }
    )
    business_scope = f"host-skill:{target_ref}"
    review_ref = f"review-waiver://governor-grant/{suffix}"
    output_contract = {
        "type": "host_skill_commit",
        "required": ["decision", "narrative", "receipt_ref"],
    }
    capability_requirements = (target_ref,)
    budget_policy = BudgetPolicy(
        scope=f"work-order:{work_order_id}",
        limits=_DIRECT_SKILL_BUDGET_LIMITS,
        on_exceeded=BudgetExceededAction.BLOCK,
        metadata={
            "policy": "governed_direct_skill_default",
            "policy_version": "governed_direct_skill_budget.v1",
            "route_type": "skill",
            "fail_closed": True,
            "enforcement_scope": "direct_skill_worker_preflight",
        },
    )
    budget_policy.validate()
    safe_frozen_context = _safe_durable_value(
        dict(frozen_context or {}),
        path="frozen_context",
    )
    if not isinstance(safe_frozen_context, dict):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "frozen_context is not a durable mapping",
        )
    work_order = WorkOrderV3(
        work_order_id=work_order_id,
        domain="skill",
        run_mode="worker",
        objective=objective,
        input_pack={
            **safe_frozen_context,
            "input_ref": input_ref,
            "user_message": objective,
            "task_text": str(intent_draft.get("task") or objective)[:_MAX_DURABLE_TEXT],
            "person_id": str(person_id or principal_id)[:255],
            "intent_id": str(governor_result.get("intent_id") or intent_draft.get("id") or "")[:255],
            "intent_draft": safe_intent,
            "emotion_context": _safe_durable_value(dict(emotion_context or {}), path="emotion_context"),
            "render_target_lang": str(render_target_lang or "zh")[:16],
        },
        allowed_actions=(target_ref,),
        forbidden_actions=("reroute", "replan", "cross_executor_resume"),
        acceptance_criteria=(_ACCEPTANCE_CRITERION,),
        verification_plan={"required": True, "policy": "durable_worker_projection"},
        approval_policy={
            "required": True,
            "governor_grant_ref": grant_ref,
            "on_missing": "BLOCK",
        },
        review_policy={
            "review_required": False,
            "mode": "governor_grant_review_waiver",
            "reason": "separate_review_waived_by_approved_governor_grant",
            "waiver_authority": "governor_grant",
            "review_ref": review_ref,
        },
        metadata={
            "surface": surface.value,
            "graph_id": graph_id,
            "graph_revision": 1,
            "operation_fingerprint": operation_fingerprint,
            "planning_lineage": {
                "graph_id": graph_id,
                "root_work_order_id": work_order_id,
                "revision": 1,
                "parent_revision": None,
                "approval_ref": grant_ref,
                "review_ref": review_ref,
                "node_bindings": [
                    {
                        "node_id": node_id,
                        "objective": objective,
                        "worker_route": "skill",
                        "target_id": target_ref,
                    }
                ],
            },
            "lineage": {
                "root_work_order_id": work_order_id,
                "task_graph_id": graph_id,
                "task_node_id": node_id,
                "graph_revision": 1,
                "attempt": 0,
                "capability_requirements": list(capability_requirements),
                "approval_ref": grant_ref,
                "review_ref": review_ref,
                "business_idempotency_scope": business_scope,
                "operation_fingerprint": operation_fingerprint,
            },
        },
        root_work_order_id=work_order_id,
        parent_work_order_id=None,
        task_graph_id=graph_id,
        task_node_id=node_id,
        graph_revision=1,
        attempt=0,
        capability_requirements=capability_requirements,
        risk_level=1,
        input_refs=(input_ref, grant_ref),
        output_contract=output_contract,
        budget_policy=budget_policy,
        business_idempotency_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
        status_version=0,
        approval_ref=grant_ref,
        review_ref=review_ref,
    )
    work_order.validate()
    _require_fail_closed_direct_skill_work_order(work_order)
    node = TaskNode(
        node_id=node_id,
        dependencies=(),
        route=f"{GOVERNED_HOST_WORKER_ROUTE}:{target_ref}",
        input_refs=(input_ref, grant_ref),
        output_contract=output_contract,
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
        result_node_id=node_id,
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
        business_scope=business_scope,
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
    "GOVERNED_HOST_GOVERNOR_GRANT_MISSING",
    "GOVERNED_HOST_ROUTE_UNSUPPORTED",
    "GOVERNED_HOST_RUNTIME_INACTIVE",
    "GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE",
    "GovernedHostAdapterError",
    "GovernedHostRuntime",
    "build_governed_host_runtime",
]
