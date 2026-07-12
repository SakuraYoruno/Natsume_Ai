"""Governed application boundary for durable scheduler occurrences.

This module deliberately reuses ``GovernedCommandApplication`` and
``GovernedTaskCoordinator``.  It owns scheduler-specific policy, contract
construction and replay, but none of the Task Runtime state machine.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import inspect
import json
import re
from typing import Any, Protocol

from natsume_contracts.task import HostCommand, HostCommandSurface, build_host_command

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.persistence.task_repository import OutboxMessage
from host_brain.agent_runtime.work_order import AgentWorkOrder
from host_brain.orchestration import (
    DurableGovernorGrantResolver,
    GovernedCommandApplication,
    GovernorGrantError,
    governor_grant_ref,
)
from host_brain.scheduler.contracts import (
    SCHEDULER_DEFINITION_INVALID,
    SCHEDULER_WORKER_ROUTE,
    SchedulerDefinitionSnapshot,
    SchedulerOccurrence,
    occurrence_work_order_id,
    validate_definition_payload,
)
from host_brain.scheduler.worker_registry import (
    GovernedSchedulerWorker,
    SchedulerProjectionReader,
    SchedulerResultAcceptance,
    SchedulerResultVerifier,
    SchedulerWorkerRegistry,
    SchedulerWorkerResolver,
)
from host_brain.secure_ingress import SecureIngressAuthority
from host_brain.task_runtime import GovernedTaskCoordinator


SCHEDULER_RUNTIME_INACTIVE = "SCHEDULER_RUNTIME_INACTIVE"
SCHEDULER_RUNTIME_AUTHORITY_INVALID = "SCHEDULER_RUNTIME_AUTHORITY_INVALID"
SCHEDULER_RUNTIME_DEFINITION_MISSING = "SCHEDULER_RUNTIME_DEFINITION_MISSING"
SCHEDULER_RUNTIME_DEFINITION_DRIFT = "SCHEDULER_RUNTIME_DEFINITION_DRIFT"
SCHEDULER_RUNTIME_TASK_DISABLED = "SCHEDULER_RUNTIME_TASK_DISABLED"
SCHEDULER_RUNTIME_MODE_BLOCKED = "SCHEDULER_RUNTIME_MODE_BLOCKED"
SCHEDULER_RUNTIME_RESOURCE_BLOCKED = "SCHEDULER_RUNTIME_RESOURCE_BLOCKED"
SCHEDULER_RUNTIME_GOVERNOR_REJECTED = "SCHEDULER_RUNTIME_GOVERNOR_REJECTED"
SCHEDULER_RUNTIME_GOVERNOR_GRANT_MISSING = (
    "SCHEDULER_RUNTIME_GOVERNOR_GRANT_MISSING"
)
SCHEDULER_RUNTIME_INGRESS_COMPLETION_FAILED = (
    "SCHEDULER_RUNTIME_INGRESS_COMPLETION_FAILED"
)
SCHEDULER_RUNTIME_DRAIN_TIMEOUT = "SCHEDULER_RUNTIME_DRAIN_TIMEOUT"
SCHEDULER_COMMAND_REPLAY = "SCHEDULER_COMMAND_REPLAY"

_DEFAULT_DRAIN_TIMEOUT_S = 120.0
_SHA256_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
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


class GovernedSchedulerRuntimeError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or SCHEDULER_RUNTIME_AUTHORITY_INVALID)
        self.detail = str(detail or "governed scheduler runtime failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class SchedulerDefinitionRepositoryPort(Protocol):
    def get(self, task_id: str) -> Mapping[str, Any] | None: ...

    def get_revision(
        self,
        task_id: str,
        revision: int,
    ) -> Mapping[str, Any] | None: ...

    def list_definitions(
        self,
        *,
        limit: int = 100,
        include_retired: bool = False,
    ) -> list[Mapping[str, Any]]: ...

    def publish_definition(
        self,
        *,
        task_id: str,
        definition: Mapping[str, Any],
        expected_head_version: int,
        next_scheduled_at_ms: int | None,
    ) -> Mapping[str, Any]: ...

    def set_enabled(
        self,
        *,
        task_id: str,
        enabled: bool,
        expected_head_version: int,
    ) -> Mapping[str, Any]: ...

    def delete_definition(
        self,
        *,
        task_id: str,
        expected_head_version: int,
    ) -> Mapping[str, Any]: ...

    def admit_manual_occurrence(
        self,
        *,
        task_id: str,
        definition_revision: int,
        request_id: str,
        scheduled_at_ms: int,
        payload: Mapping[str, Any],
        authority: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def get_occurrence(self, occurrence_id: str) -> Mapping[str, Any] | None: ...

    def get_occurrence_by_request(
        self,
        request_id: str,
        *,
        source: str | None = None,
    ) -> Mapping[str, Any] | None: ...

    def get_terminal_outcome(
        self,
        occurrence_id: str,
    ) -> Mapping[str, Any] | None: ...

    def append_terminal_outcome(
        self,
        *,
        occurrence_id: str,
        outcome_kind: str,
        reason_code: str,
        outcome: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def resolve_admin_operation(
        self,
        *,
        operation_kind: str,
        task_id: str,
        operation_request: Mapping[str, Any],
        authority: Mapping[str, Any],
    ) -> Mapping[str, Any] | None: ...

    def publish_definition_operation(
        self,
        *,
        task_id: str,
        definition: Mapping[str, Any],
        expected_head_version: int,
        next_scheduled_at_ms: int | None,
        authority: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def set_enabled_operation(
        self,
        *,
        task_id: str,
        enabled: bool,
        expected_head_version: int,
        authority: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def delete_definition_operation(
        self,
        *,
        task_id: str,
        expected_head_version: int,
        authority: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class ClaimResultStorePort(Protocol):
    def complete_claim(
        self,
        *,
        claim_ref: str,
        result_ref: str,
        at_ms: int | None = None,
    ) -> None: ...


@dataclass(slots=True)
class _RequestLockEntry:
    lock: asyncio.Lock
    users: int = 0


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
    ) -> AsyncIterator[dict[str, Any]]:
        async with self._condition:
            if not self._active or not self._accepting:
                raise GovernedSchedulerRuntimeError(
                    SCHEDULER_RUNTIME_INACTIVE,
                    "scheduler runtime is not accepting commands",
                )
            self._in_flight += 1
        try:
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
            candidate_command, candidate_order, candidate_graph = _build_contracts(
                occurrence=occurrence,
                definition=definition,
                authority=authority,
                governor_grant=governor_grant,
                received_at_ms=received_at_ms,
            )
            existing = self._commands.get_by_request(
                surface=HostCommandSurface.SCHEDULER.value,
                request_id=request_id,
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
                if work_order is None or assignment is None or graph is None:
                    raise GovernedSchedulerRuntimeError(
                        SCHEDULER_RUNTIME_AUTHORITY_INVALID,
                        "durable scheduler replay is incomplete",
                    )
            async for event in self._application.execute_stream(
                command=command,
                work_order=work_order,
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
            surface=HostCommandSurface.SCHEDULER.value,
            request_id=str(request_id or "").strip(),
        )

    def health(self) -> dict[str, Any]:
        return {
            "active": self._active,
            "accepting": self._accepting,
            "in_flight": self._in_flight,
        }


class SchedulerCommandService:
    """The only route/clock API for scheduler administration and execution."""

    def __init__(
        self,
        *,
        runtime: GovernedSchedulerRuntime,
        intent_engine: Any,
        claim_result_store: ClaimResultStorePort,
        definition_repository: SchedulerDefinitionRepositoryPort,
        policy_evaluator: SchedulerDefinitionPolicyEvaluator,
        worker_registry: SchedulerWorkerRegistry,
    ) -> None:
        if not callable(getattr(intent_engine, "evaluate_intent", None)):
            raise TypeError("intent_engine must expose evaluate_intent")
        if not callable(getattr(claim_result_store, "complete_claim", None)):
            raise TypeError("claim_result_store must expose complete_claim")
        if not isinstance(worker_registry, SchedulerWorkerRegistry):
            raise TypeError("worker_registry must be SchedulerWorkerRegistry")
        self._runtime = runtime
        self._intent_engine = intent_engine
        self._claims = claim_result_store
        self._definitions = definition_repository
        self._policy = policy_evaluator
        self._workers = worker_registry
        self._request_locks: dict[str, _RequestLockEntry] = {}
        self._request_lock_guard = asyncio.Lock()

    def list_definitions(self) -> list[dict[str, Any]]:
        rows = self._definitions.list_definitions(limit=1_000, include_retired=False)
        return [SchedulerDefinitionSnapshot.from_mapping(row).to_dict() for row in rows]

    def get_definition(self, task_id: str) -> dict[str, Any] | None:
        raw = self._definitions.get(str(task_id or "").strip())
        return (
            SchedulerDefinitionSnapshot.from_mapping(raw).to_dict()
            if isinstance(raw, Mapping)
            else None
        )

    def preflight_definition(
        self,
        definition: SchedulerDefinitionSnapshot | Mapping[str, Any],
    ) -> str:
        snapshot = (
            definition
            if isinstance(definition, SchedulerDefinitionSnapshot)
            else SchedulerDefinitionSnapshot.from_mapping(definition)
        )
        self._require_registered_worker(snapshot)
        return self._policy.require_allowed(snapshot)

    async def publish_definition(
        self,
        *,
        task_id: str,
        definition: Mapping[str, Any],
        authority: SecureIngressAuthority | Mapping[str, Any],
        expected_head_version: int,
        next_scheduled_at_ms: int | None,
        received_at_ms: int,
    ) -> dict[str, Any]:
        auth = _require_authority(authority)
        payload = validate_definition_payload(task_id=task_id, definition=definition)
        normalized_operation_definition = {
            **payload,
            "task_id": str(task_id or "").strip(),
        }
        operation_request = {
            "task_id": str(task_id or "").strip(),
            "definition": normalized_operation_definition,
            "expected_head_version": int(expected_head_version),
            "next_scheduled_at_ms": next_scheduled_at_ms,
        }
        replay = self._admin_replay(
            auth,
            task_id=task_id,
            operation_kind="PUBLISH",
            operation_request=operation_request,
        )
        if replay is not None:
            await self._complete_claim(
                auth,
                result_ref=str(replay["receipt_ref"]),
                received_at_ms=received_at_ms,
            )
            return SchedulerDefinitionSnapshot.from_mapping(replay["result"]).to_dict()
        self._require_registered_worker(
            SchedulerDefinitionSnapshot(
                task_id=str(task_id or "").strip(),
                definition_revision=1,
                head_version=1,
                enabled=bool(payload.get("enabled", True)),
                definition=payload,
            )
        )
        operation = self._definitions.publish_definition_operation(
            task_id=str(task_id),
            definition=payload,
            expected_head_version=int(expected_head_version),
            next_scheduled_at_ms=next_scheduled_at_ms,
            authority=auth,
        )
        snapshot = SchedulerDefinitionSnapshot.from_mapping(operation["result"])
        await self._complete_claim(
            auth,
            result_ref=str(operation["receipt_ref"]),
            received_at_ms=received_at_ms,
        )
        return snapshot.to_dict()

    async def set_enabled(
        self,
        *,
        task_id: str,
        enabled: bool,
        authority: SecureIngressAuthority | Mapping[str, Any],
        expected_head_version: int,
        received_at_ms: int,
    ) -> dict[str, Any]:
        auth = _require_authority(authority)
        if type(enabled) is not bool:
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_AUTHORITY_INVALID,
                "enabled must be boolean",
            )
        operation_request = {
            "task_id": str(task_id or "").strip(),
            "enabled": enabled,
            "expected_head_version": int(expected_head_version),
        }
        replay = self._admin_replay(
            auth,
            task_id=task_id,
            operation_kind="SET_ENABLED",
            operation_request=operation_request,
        )
        if replay is not None:
            await self._complete_claim(
                auth,
                result_ref=str(replay["receipt_ref"]),
                received_at_ms=received_at_ms,
            )
            return SchedulerDefinitionSnapshot.from_mapping(replay["result"]).to_dict()
        operation = self._definitions.set_enabled_operation(
            task_id=str(task_id),
            enabled=enabled,
            expected_head_version=int(expected_head_version),
            authority=auth,
        )
        snapshot = SchedulerDefinitionSnapshot.from_mapping(operation["result"])
        await self._complete_claim(
            auth,
            result_ref=str(operation["receipt_ref"]),
            received_at_ms=received_at_ms,
        )
        return snapshot.to_dict()

    async def delete_definition(
        self,
        *,
        task_id: str,
        authority: SecureIngressAuthority | Mapping[str, Any],
        expected_head_version: int,
        received_at_ms: int,
    ) -> dict[str, Any]:
        auth = _require_authority(authority)
        operation_request = {
            "task_id": str(task_id or "").strip(),
            "expected_head_version": int(expected_head_version),
        }
        replay = self._admin_replay(
            auth,
            task_id=task_id,
            operation_kind="DELETE",
            operation_request=operation_request,
        )
        if replay is not None:
            await self._complete_claim(
                auth,
                result_ref=str(replay["receipt_ref"]),
                received_at_ms=received_at_ms,
            )
            return dict(replay["result"])
        operation = self._definitions.delete_definition_operation(
            task_id=str(task_id),
            expected_head_version=int(expected_head_version),
            authority=auth,
        )
        result = dict(operation["result"])
        await self._complete_claim(
            auth,
            result_ref=str(operation["receipt_ref"]),
            received_at_ms=received_at_ms,
        )
        return result

    async def trigger_manual(
        self,
        *,
        task_id: str,
        authority: SecureIngressAuthority | Mapping[str, Any],
        payload: Mapping[str, Any],
        received_at_ms: int,
    ) -> dict[str, Any]:
        auth = _require_authority(authority)
        if bool(auth.get("duplicate")):
            replay_raw = self._definitions.get_occurrence_by_request(
                str(auth["request_id"]),
                source="MANUAL",
            )
            if not isinstance(replay_raw, Mapping):
                raise GovernedSchedulerRuntimeError(
                    SCHEDULER_COMMAND_REPLAY,
                    "duplicate manual trigger has no durable occurrence",
                )
            return await self.execute_occurrence(
                occurrence=SchedulerOccurrence.from_mapping(replay_raw),
                authority=auth,
                received_at_ms=received_at_ms,
            )
        definition = self._current_definition(task_id)
        self.preflight_definition(definition)
        occurrence_raw = self._definitions.admit_manual_occurrence(
            task_id=definition.task_id,
            definition_revision=definition.definition_revision,
            request_id=str(auth["request_id"]),
            scheduled_at_ms=int(received_at_ms),
            payload=dict(payload or {}),
            authority=auth,
        )
        occurrence = SchedulerOccurrence.from_mapping(occurrence_raw)
        return await self.execute_occurrence(
            occurrence=occurrence,
            authority=auth,
            received_at_ms=received_at_ms,
        )

    async def execute_occurrence(
        self,
        *,
        occurrence: SchedulerOccurrence | Mapping[str, Any],
        authority: SecureIngressAuthority | Mapping[str, Any],
        received_at_ms: int,
    ) -> dict[str, Any]:
        auth = _require_authority(authority)
        admitted = (
            occurrence
            if isinstance(occurrence, SchedulerOccurrence)
            else SchedulerOccurrence.from_mapping(occurrence)
        )
        durable_raw = self._definitions.get_occurrence(admitted.occurrence_id)
        if not isinstance(durable_raw, Mapping):
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_DEFINITION_MISSING,
                "scheduler occurrence was not durably admitted",
            )
        durable = SchedulerOccurrence.from_mapping(durable_raw)
        if durable.to_dict() != admitted.to_dict():
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_DEFINITION_DRIFT,
                "scheduler occurrence differs from its durable admission",
            )
        _assert_occurrence_authority(durable, auth)
        request_id = str(auth["request_id"])
        async with self._request_lock(request_id):
            durable_outcome = self._definitions.get_terminal_outcome(
                durable.occurrence_id
            )
            if isinstance(durable_outcome, Mapping):
                terminal = _terminal_outcome_response(durable, durable_outcome)
                await self._complete_claim(
                    auth,
                    result_ref=str(durable_outcome["receipt_ref"]),
                    received_at_ms=received_at_ms,
                )
                return terminal
            existing = self._runtime.existing_command(request_id=request_id)
            if existing is not None:
                command = existing.get("command")
                if not isinstance(command, HostCommand) or not command.governor_grant_ref:
                    raise GovernedSchedulerRuntimeError(
                        SCHEDULER_RUNTIME_GOVERNOR_GRANT_MISSING,
                        "durable scheduler command has no Governor grant",
                    )
                grant = command.governor_grant_ref
                definition = self._definition_revision(durable)
            else:
                # Occurrence admission freezes the exact definition revision.
                # A later head publication must not invalidate already-admitted
                # work; cancellation requires an explicit durable occurrence
                # state, not an implicit head drift check.
                definition = self._definition_revision(durable)
                mode = self._policy.require_allowed(definition)
                decision = dict(
                    self._intent_engine.evaluate_intent(
                        raw_intent=_build_governor_intent(
                            occurrence=durable,
                            definition=definition,
                            request_id=request_id,
                            session_id=_scheduler_session_id(durable.occurrence_id),
                            ts_ms=int(received_at_ms),
                            mode=mode,
                        ),
                        request_id=request_id,
                        current_ms=int(received_at_ms),
                        proposer_node_id="scheduler_secure_ingress",
                        model_snapshot="server-owned-scheduler-command",
                        prompt_profile_id="scheduler-command-v1",
                    )
                    or {}
                )
                if str(decision.get("decision") or "").strip().upper() != "APPROVE":
                    reason_code = str(
                        decision.get("reason_code")
                        or SCHEDULER_RUNTIME_GOVERNOR_REJECTED
                    ).strip()[:160]
                    detail = str(
                        decision.get("detail")
                        or decision.get("reason")
                        or "Governor rejected scheduler command"
                    ).strip()[:2_000]
                    outcome = self._definitions.append_terminal_outcome(
                        occurrence_id=durable.occurrence_id,
                        outcome_kind="GOVERNOR_REJECTED",
                        reason_code=reason_code,
                        outcome={
                            "decision": "REJECT",
                            "reason_code": reason_code,
                            "detail": detail,
                            "task_id": durable.task_id,
                        },
                    )
                    terminal = _terminal_outcome_response(durable, outcome)
                    await self._complete_claim(
                        auth,
                        result_ref=str(outcome["receipt_ref"]),
                        received_at_ms=received_at_ms,
                    )
                    return terminal
                grant = governor_grant_ref(request_id, decision)

            terminal: dict[str, Any] | None = None
            async for event in self._runtime.execute_stream(
                occurrence=durable,
                definition=definition,
                authority=auth,
                governor_grant=grant,
                received_at_ms=int(received_at_ms),
            ):
                if bool(event.get("done")):
                    terminal = dict(event)
            if terminal is None:
                raise GovernedSchedulerRuntimeError(
                    SCHEDULER_RUNTIME_AUTHORITY_INVALID,
                    "scheduler runtime returned no terminal result",
                )
            receipt = dict(terminal.get("execution_receipt") or {})
            result_ref = str(receipt.get("receipt_ref") or "").strip()
            if not result_ref:
                raise GovernedSchedulerRuntimeError(
                    SCHEDULER_RUNTIME_INGRESS_COMPLETION_FAILED,
                    "terminal scheduler result has no durable receipt",
                )
            await self._complete_claim(
                auth,
                result_ref=result_ref,
                received_at_ms=received_at_ms,
            )
            return terminal

    @asynccontextmanager
    async def _request_lock(self, request_id: str) -> AsyncIterator[None]:
        async with self._request_lock_guard:
            entry = self._request_locks.get(request_id)
            if entry is None:
                entry = _RequestLockEntry(lock=asyncio.Lock())
                self._request_locks[request_id] = entry
            entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            async with self._request_lock_guard:
                entry.users -= 1
                if entry.users == 0 and self._request_locks.get(request_id) is entry:
                    self._request_locks.pop(request_id, None)

    def health(self) -> dict[str, Any]:
        runtime = self._runtime.health()
        return {
            "status": "OK" if runtime["active"] else "INACTIVE",
            "runtime": runtime,
            "definition_count": len(self.list_definitions()),
        }

    def _current_definition(self, task_id: str) -> SchedulerDefinitionSnapshot:
        raw = self._definitions.get(str(task_id or "").strip())
        if not isinstance(raw, Mapping):
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_DEFINITION_MISSING,
                f"scheduler definition not found: {task_id}",
            )
        return SchedulerDefinitionSnapshot.from_mapping(raw)

    def _definition_revision(
        self,
        occurrence: SchedulerOccurrence,
    ) -> SchedulerDefinitionSnapshot:
        raw = self._definitions.get_revision(
            occurrence.task_id,
            occurrence.definition_revision,
        )
        if not isinstance(raw, Mapping):
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_DEFINITION_MISSING,
                "scheduler definition revision not found",
            )
        return SchedulerDefinitionSnapshot.from_mapping(raw)

    def _require_registered_worker(
        self,
        definition: SchedulerDefinitionSnapshot,
    ) -> None:
        worker_key = definition.worker_key
        if worker_key not in self._workers.worker_keys:
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_DEFINITION_INVALID,
                f"definition worker_key is not registered: {worker_key}",
            )

    def _admin_replay(
        self,
        authority: Mapping[str, Any],
        *,
        task_id: str,
        operation_kind: str,
        operation_request: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        resolver = getattr(self._definitions, "resolve_admin_operation", None)
        if callable(resolver):
            resolved = resolver(
                operation_kind=operation_kind,
                task_id=str(task_id or "").strip(),
                operation_request=operation_request,
                authority=authority,
            )
            if isinstance(resolved, Mapping):
                return dict(resolved)
        if bool(authority.get("duplicate")):
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_COMMAND_REPLAY,
                "duplicate scheduler admin command has no matching immutable operation result",
            )
        return None

    async def _complete_claim(
        self,
        authority: Mapping[str, Any],
        *,
        result_ref: str,
        received_at_ms: int,
    ) -> None:
        claim_ref = str(authority.get("claim_ref") or "").strip()
        if not claim_ref or not result_ref:
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_INGRESS_COMPLETION_FAILED,
                "secure ingress claim/result reference is missing",
            )
        try:
            completed = self._claims.complete_claim(
                claim_ref=claim_ref,
                result_ref=result_ref,
                at_ms=int(received_at_ms),
            )
            if inspect.isawaitable(completed):
                await completed
        except Exception as exc:
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_INGRESS_COMPLETION_FAILED,
                "secure ingress claim result could not be completed",
            ) from exc


def build_governed_scheduler_runtime(
    *,
    task_runtime_container: Any,
    ledger: Any,
    result_repository: Any,
    runtime_router: CanonicalRuntimeRouter,
    worker_registry: SchedulerWorkerRegistry,
    owner_id: str,
    definition_repository: SchedulerDefinitionRepositoryPort | None = None,
    legacy_worker_registry: SchedulerWorkerRegistry | None = None,
) -> GovernedSchedulerRuntime:
    definitions = definition_repository or getattr(
        task_runtime_container,
        "scheduler_definition_repository",
        None,
    )
    if definitions is None:
        raise TypeError("scheduler definition repository is required")
    canonical_worker = GovernedSchedulerWorker(
        handlers=worker_registry,
        definition_repository=definitions,
        occurrence_repository=definitions,
        result_repository=result_repository,
    )
    legacy_worker = (
        GovernedSchedulerWorker(
            handlers=legacy_worker_registry,
            definition_repository=definitions,
            occurrence_repository=definitions,
            result_repository=result_repository,
        )
        if legacy_worker_registry is not None
        else None
    )
    command_repository = task_runtime_container.command_repository
    coordinator = GovernedTaskCoordinator(
        task_store=task_runtime_container.task_store,
        outbox_publisher=task_runtime_container.outbox_publisher,
        outbox_factory=OutboxMessage,
        worker_resolver=SchedulerWorkerResolver(
            canonical_worker=canonical_worker,
            legacy_worker=legacy_worker,
        ),
        verifier=SchedulerResultVerifier(result_repository),
        acceptance=SchedulerResultAcceptance(),
        projection_reader=SchedulerProjectionReader(
            result_repository,
            occurrence_repository=definitions,
            command_repository=command_repository,
        ),
        owner_id=owner_id,
    )
    application = GovernedCommandApplication(
        command_repository=command_repository,
        coordinator=coordinator,
    )
    return GovernedSchedulerRuntime(
        application=application,
        runtime_router=runtime_router,
        command_repository=command_repository,
        governor_grants=DurableGovernorGrantResolver(ledger),
    )


def _build_contracts(
    *,
    occurrence: SchedulerOccurrence,
    definition: SchedulerDefinitionSnapshot,
    authority: Mapping[str, Any],
    governor_grant: str,
    received_at_ms: int,
) -> tuple[HostCommand, AgentWorkOrder, TaskGraph]:
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
    work_order = AgentWorkOrder(
        work_order_id=work_order_id,
        domain="organ_ops",
        run_mode="worker",
        objective=f"Execute durable scheduled task {occurrence.task_id}",
        input_pack={
            "occurrence_id": occurrence.occurrence_id,
            "definition_ref": definition_ref,
        },
        allowed_actions=["execute_scheduler_task"],
        forbidden_actions=["change_scheduler_definition", "bypass_governor"],
        acceptance_criteria=["scheduler handler result persisted"],
        verification_plan={"required": True, "type": "durable_scheduler_result"},
        metadata={
            "definition_revision": definition.definition_revision,
            "priority": definition.priority,
            "worker_key": definition.worker_key,
            "resource_profile": definition.resource_profile,
            "retry_policy": retry_policy,
            "risk_level": risk_level,
        },
    )
    work_order.validate()
    node = TaskNode(
        node_id=node_id,
        dependencies=(),
        route=SCHEDULER_WORKER_ROUTE,
        input_refs=(occurrence.occurrence_id, definition_ref, governor_grant),
        output_contract={
            "type": "durable_scheduler_result",
            "required": ["occurrence_id", "result", "receipt_ref"],
        },
        retry_policy=retry_policy,
        risk_level=risk_level,
        acceptance_criteria=("scheduler handler result persisted",),
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
        nodes=(node,),
    )
    operation_fingerprint = _sha256_json(
        {
            "definition_revision": definition.definition_revision,
            "occurrence_id": occurrence.occurrence_id,
            "payload_sha256": occurrence.payload_sha256,
            "worker_key": definition.worker_key,
        }
    )
    command = build_host_command(
        surface=HostCommandSurface.SCHEDULER,
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
        route_type=SCHEDULER_WORKER_ROUTE,
        target_id=definition.worker_key,
        business_scope=f"scheduler:{_scope_token(occurrence.task_id)}",
        operation_fingerprint=operation_fingerprint,
        created_at_ms=int(received_at_ms),
    )
    return command, work_order, graph


def _build_governor_intent(
    *,
    occurrence: SchedulerOccurrence,
    definition: SchedulerDefinitionSnapshot,
    request_id: str,
    session_id: str,
    ts_ms: int,
    mode: str,
) -> dict[str, Any]:
    request_digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-scheduler-{request_digest[:40]}",
        "ts_ms": int(ts_ms),
        "session_id": session_id,
        "seq": int(ts_ms),
        "type": "control",
        "action": "exec_intent",
        "target": "system",
        "args": {
            "text": f"Execute scheduled task {occurrence.task_id}",
            "route": "delegate",
            "target_id": definition.worker_key,
            "occurrence_ref": occurrence.occurrence_id,
            "sys_mode": mode,
        },
        "nonce": hashlib.sha256(
            f"scheduler-governor\n{request_id}".encode("utf-8")
        ).hexdigest(),
        "route": "delegate",
        "target_id": definition.worker_key,
    }


def _require_authority(
    authority: SecureIngressAuthority | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(authority, SecureIngressAuthority):
        data = authority.to_dict()
    elif isinstance(authority, Mapping):
        data = dict(authority)
    else:
        data = {}
    admissible = bool(data.get("allowed") or data.get("duplicate"))
    required = ("request_id", "principal_id", "claim_ref", "audit_ref", "body_sha256")
    if not admissible or any(not str(data.get(key) or "").strip() for key in required):
        raise GovernedSchedulerRuntimeError(
            SCHEDULER_RUNTIME_AUTHORITY_INVALID,
            "admissible secret-free scheduler authority is required",
        )
    body_sha = str(data["body_sha256"]).strip().lower()
    if _SHA256_RE.fullmatch(body_sha) is None:
        raise GovernedSchedulerRuntimeError(
            SCHEDULER_RUNTIME_AUTHORITY_INVALID,
            "secure ingress body_sha256 is invalid",
        )
    data["body_sha256"] = body_sha.removeprefix("sha256:")
    return data


def _assert_stable_replay(candidate: HostCommand, durable: HostCommand) -> None:
    left = candidate.to_dict()
    right = durable.to_dict()
    drift = [field for field in _STABLE_COMMAND_FIELDS if left.get(field) != right.get(field)]
    if drift:
        raise GovernedSchedulerRuntimeError(
            SCHEDULER_RUNTIME_DEFINITION_DRIFT,
            "scheduler replay changed stable semantics: " + ", ".join(drift),
        )


def _assert_occurrence_authority(
    occurrence: SchedulerOccurrence,
    authority: Mapping[str, Any],
) -> None:
    actual = {
        "request_id": str(authority.get("request_id") or "").strip(),
        "principal_id": str(authority.get("principal_id") or "").strip(),
        "claim_ref": str(authority.get("claim_ref") or "").strip(),
        "audit_ref": str(authority.get("audit_ref") or "").strip(),
        "body_sha256": "sha256:"
        + str(authority.get("body_sha256") or "").removeprefix("sha256:"),
    }
    expected = {
        "request_id": occurrence.request_id,
        "principal_id": occurrence.principal_id,
        "claim_ref": occurrence.claim_ref,
        "audit_ref": occurrence.audit_ref,
        "body_sha256": occurrence.body_sha256,
    }
    if actual != expected:
        raise GovernedSchedulerRuntimeError(
            SCHEDULER_RUNTIME_AUTHORITY_INVALID,
            "scheduler command authority differs from durable occurrence authority",
        )


def _terminal_outcome_response(
    occurrence: SchedulerOccurrence,
    durable_outcome: Mapping[str, Any],
) -> dict[str, Any]:
    outcome = dict(durable_outcome.get("outcome") or {})
    reason_code = str(durable_outcome.get("reason_code") or "").strip()
    receipt_ref = str(durable_outcome.get("receipt_ref") or "").strip()
    outcome_kind = str(durable_outcome.get("outcome_kind") or "").strip()
    if (
        outcome_kind != "GOVERNOR_REJECTED"
        or str(outcome.get("decision") or "").strip().upper() != "REJECT"
        or str(outcome.get("reason_code") or "").strip() != reason_code
        or str(outcome.get("task_id") or "").strip() != occurrence.task_id
        or not receipt_ref
    ):
        raise GovernedSchedulerRuntimeError(
            SCHEDULER_RUNTIME_AUTHORITY_INVALID,
            "durable scheduler terminal outcome binding is invalid",
        )
    detail = str(outcome.get("detail") or reason_code)
    return {
        "done": True,
        "decision": "REJECT",
        "reason_code": reason_code,
        "detail": detail,
        "occurrence_id": occurrence.occurrence_id,
        "task_id": occurrence.task_id,
        "result": {
            "status": "BLOCKED",
            "reason_code": reason_code,
            "detail": detail,
        },
        "execution_receipt": {
            "receipt_ref": receipt_ref,
            "occurrence_id": occurrence.occurrence_id,
            "result_status": "BLOCKED",
            "terminal_kind": outcome_kind,
        },
    }


def _definition_ref(definition: SchedulerDefinitionSnapshot) -> str:
    return (
        f"scheduler-definition://{_ref_token(definition.task_id)}/"
        f"{definition.definition_revision}"
    )


def _scheduler_session_id(occurrence_id: str) -> str:
    digest = hashlib.sha256(str(occurrence_id).encode("utf-8")).hexdigest()[:40]
    return f"scheduler-session-{digest}"


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
