"""Explicit real-handler registry and governed scheduler worker adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Mapping, Sequence
import inspect
from typing import Any, Callable, Protocol

from natsume_contracts.task import HostCommand

from host_brain.scheduler.contracts import (
    SCHEDULER_WORKER_ROUTE,
    SchedulerDefinitionSnapshot,
    SchedulerOccurrence,
    occurrence_work_order_id,
)
from host_brain.task_runtime.worker_registry import (
    GovernedWorkerRegistryError,
    GovernedWorkerRoute,
)


SCHEDULER_HANDLER_NOT_REGISTERED = "SCHEDULER_HANDLER_NOT_REGISTERED"
SCHEDULER_HANDLER_RESULT_INVALID = "SCHEDULER_HANDLER_RESULT_INVALID"
SCHEDULER_DURABLE_RESULT_INVALID = "SCHEDULER_DURABLE_RESULT_INVALID"


class SchedulerWorkerError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or SCHEDULER_HANDLER_RESULT_INVALID)
        self.detail = str(detail or "scheduler worker failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class SchedulerHandler(Protocol):
    def __call__(
        self,
        *,
        occurrence: SchedulerOccurrence,
        definition: SchedulerDefinitionSnapshot,
        command: HostCommand,
        execution_context: Mapping[str, Any],
    ) -> Mapping[str, Any] | Awaitable[Mapping[str, Any]]: ...


class SchedulerOccurrenceRepositoryPort(Protocol):
    def get_occurrence(self, occurrence_id: str) -> Mapping[str, Any] | None: ...


class SchedulerDefinitionRepositoryPort(Protocol):
    def get_revision(
        self,
        task_id: str,
        revision: int,
    ) -> Mapping[str, Any] | None: ...


class SchedulerExecutionResultRepositoryPort(Protocol):
    def append(
        self,
        *,
        occurrence_id: str,
        command_id: str,
        work_order_id: str,
        result: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def get(self, receipt_ref: str) -> Mapping[str, Any] | None: ...

    def get_by_command(self, command_id: str) -> Mapping[str, Any] | None: ...


class SchedulerCommandRepositoryPort(Protocol):
    def get(self, command_id: str) -> Mapping[str, Any] | None: ...


class SchedulerWorkerRegistry:
    """Composition-owned exact worker-key map."""

    def __init__(self, *, default_handler: SchedulerHandler | None = None) -> None:
        self._handlers: dict[str, SchedulerHandler] = {}
        if default_handler is not None:
            self._handlers[SCHEDULER_WORKER_ROUTE] = _handler(default_handler)

    def register(self, worker_key: str, handler: SchedulerHandler) -> None:
        key = _worker_key(worker_key)
        if key in self._handlers:
            raise ValueError(f"scheduler worker key already registered: {key}")
        self._handlers[key] = _handler(handler)

    def register_default(self, handler: SchedulerHandler) -> None:
        self.register(SCHEDULER_WORKER_ROUTE, handler)

    @property
    def worker_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def resolve(self, *, worker_key: str, task_id: str) -> SchedulerHandler:
        key = _worker_key(worker_key)
        handler = self._handlers.get(key)
        if handler is None:
            raise SchedulerWorkerError(
                SCHEDULER_HANDLER_NOT_REGISTERED,
                f"no governed scheduler handler is registered for worker_key={key!r}",
            )
        return handler


class GovernedSchedulerWorker:
    """Run one registered handler and persist its result before COMMIT."""

    def __init__(
        self,
        *,
        handlers: SchedulerWorkerRegistry,
        definition_repository: SchedulerDefinitionRepositoryPort,
        occurrence_repository: SchedulerOccurrenceRepositoryPort,
        result_repository: SchedulerExecutionResultRepositoryPort,
        node_grant_verifier: Any,
    ) -> None:
        if not callable(getattr(node_grant_verifier, "verify", None)):
            raise TypeError("node_grant_verifier must expose verify")
        self._handlers = handlers
        self._definitions = definition_repository
        self._occurrences = occurrence_repository
        self._results = result_repository
        self._node_grants = node_grant_verifier

    async def execute_stream(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        execution_context: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        route = _canonical_delegate_route(execution_context.get("worker_route"))
        if route.target_id != str(command.target_id or "").strip():
            raise SchedulerWorkerError(
                SCHEDULER_DURABLE_RESULT_INVALID,
                "TaskNode delegate target differs from HostCommand target",
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
            attempt_id=str(execution_context.get("attempt_id") or "").strip(),
            lease_id=str(execution_context.get("lease_id") or "").strip(),
            lease_owner_id=str(
                execution_context.get("lease_owner_id") or ""
            ).strip(),
            fencing_token=execution_context.get("fencing_token"),
        )
        verified_payload = (
            verified_grant.to_dict()
            if callable(getattr(verified_grant, "to_dict", None))
            else dict(verified_grant)
            if isinstance(verified_grant, Mapping)
            else {}
        )
        worker_context = {
            **dict(execution_context),
            "verified_node_grant": verified_payload,
        }
        occurrence_id = _work_order_occurrence_id(work_order)
        occurrence_raw = self._occurrences.get_occurrence(occurrence_id)
        if inspect.isawaitable(occurrence_raw):
            occurrence_raw = await occurrence_raw
        if not isinstance(occurrence_raw, Mapping):
            raise SchedulerWorkerError(
                SCHEDULER_DURABLE_RESULT_INVALID,
                "admitted scheduler occurrence is missing",
            )
        occurrence = SchedulerOccurrence.from_mapping(occurrence_raw)
        work_order_id = str(getattr(work_order, "work_order_id", "") or "")
        if (
            occurrence.occurrence_id != command.input_ref
            or occurrence.request_id != command.request_id
            or occurrence_work_order_id(occurrence.occurrence_id) != work_order_id
        ):
            raise SchedulerWorkerError(
                SCHEDULER_DURABLE_RESULT_INVALID,
                "scheduler command, occurrence and WorkOrder identities differ",
            )
        definition_raw = self._definitions.get_revision(
            occurrence.task_id,
            occurrence.definition_revision,
        )
        if inspect.isawaitable(definition_raw):
            definition_raw = await definition_raw
        if not isinstance(definition_raw, Mapping):
            raise SchedulerWorkerError(
                SCHEDULER_DURABLE_RESULT_INVALID,
                "the occurrence definition revision is missing",
            )
        definition = SchedulerDefinitionSnapshot.from_mapping(definition_raw)
        if definition.task_id != occurrence.task_id:
            raise SchedulerWorkerError(
                SCHEDULER_DURABLE_RESULT_INVALID,
                "durable occurrence and definition identities differ",
            )
        if definition.worker_key != command.target_id:
            raise SchedulerWorkerError(
                SCHEDULER_DURABLE_RESULT_INVALID,
                "durable definition worker key differs from HostCommand target",
            )

        try:
            handler = self._handlers.resolve(
                worker_key=definition.worker_key,
                task_id=occurrence.task_id,
            )
            result_raw = handler(
                occurrence=occurrence,
                definition=definition,
                command=command,
                execution_context=worker_context,
            )
            if inspect.isawaitable(result_raw):
                result_raw = await result_raw
            if not isinstance(result_raw, Mapping):
                raise SchedulerWorkerError(
                    SCHEDULER_HANDLER_RESULT_INVALID,
                    "scheduler handler must return an object",
                )
            result = dict(result_raw)
        except Exception as exc:
            result = {
                "status": "FAILED",
                "reason_code": str(
                    getattr(exc, "reason_code", None)
                    or SCHEDULER_HANDLER_RESULT_INVALID
                ),
                "detail": "governed scheduler handler failed",
            }
        status = _durable_status(result.get("status"))
        result["status"] = status
        durable_raw = self._results.append(
            occurrence_id=occurrence.occurrence_id,
            command_id=command.command_id,
            work_order_id=work_order_id,
            result=result,
        )
        if inspect.isawaitable(durable_raw):
            durable_raw = await durable_raw
        if not isinstance(durable_raw, Mapping):
            raise SchedulerWorkerError(
                SCHEDULER_DURABLE_RESULT_INVALID,
                "scheduler result repository returned an invalid record",
            )
        durable = dict(durable_raw)
        receipt_ref = str(durable.get("receipt_ref") or "").strip()
        stored_result = durable.get("result")
        if not receipt_ref or not isinstance(stored_result, Mapping):
            raise SchedulerWorkerError(
                SCHEDULER_DURABLE_RESULT_INVALID,
                "scheduler result record lacks receipt/result",
            )
        stored_status = str(
            durable.get("status") or dict(stored_result).get("status") or status
        ).strip().upper()
        approved = stored_status not in {"FAILED", "BLOCKED"}
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE" if approved else "REJECT",
            "reason_code": None
            if approved
            else str(dict(stored_result).get("reason_code") or f"SCHEDULER_{stored_status}"),
            "request_id": command.request_id,
            "narrative": str(
                dict(stored_result).get("summary")
                or dict(stored_result).get("detail")
                or stored_status
            )[:500],
            "occurrence_id": occurrence.occurrence_id,
            "task_id": occurrence.task_id,
            "result": dict(stored_result),
            "receipt_ref": receipt_ref,
        }


class SchedulerWorkerResolver:
    """Task Runtime resolver for canonical ``delegate:<worker_key>`` routes."""

    def __init__(
        self,
        *,
        canonical_worker: GovernedSchedulerWorker,
    ) -> None:
        self._canonical = canonical_worker

    def resolve(self, *, executor: str, worker_route: str) -> GovernedSchedulerWorker:
        executor_name = str(executor or "").strip().lower()
        if executor_name != "canonical":
            raise SchedulerWorkerError(
                SCHEDULER_HANDLER_NOT_REGISTERED,
                "scheduler executor assignment is invalid",
            )
        try:
            route = GovernedWorkerRoute.parse(str(worker_route or ""))
        except GovernedWorkerRegistryError as exc:
            raise SchedulerWorkerError(
                SCHEDULER_HANDLER_NOT_REGISTERED,
                "scheduler worker route is not registered",
            ) from exc
        if route.kind != "delegate" or not route.target_id:
            raise SchedulerWorkerError(
                SCHEDULER_HANDLER_NOT_REGISTERED,
                "scheduler worker route must be canonical delegate:<worker_key>",
            )
        return self._canonical


class SchedulerProjectionReader:
    def __init__(
        self,
        repository: SchedulerExecutionResultRepositoryPort,
        *,
        occurrence_repository: SchedulerOccurrenceRepositoryPort,
        command_repository: SchedulerCommandRepositoryPort,
    ) -> None:
        self._repository = repository
        self._occurrences = occurrence_repository
        self._commands = command_repository

    def load(self, receipt_ref: str) -> Mapping[str, Any] | None:
        record = self._repository.get(receipt_ref)
        if not isinstance(record, Mapping):
            return None
        result = record.get("result")
        if not isinstance(result, Mapping):
            return None
        occurrence_raw = self._occurrences.get_occurrence(
            str(record.get("occurrence_id") or "")
        )
        command_raw = self._commands.get(str(record.get("command_id") or ""))
        if not isinstance(occurrence_raw, Mapping) or not isinstance(command_raw, Mapping):
            return None
        occurrence = SchedulerOccurrence.from_mapping(occurrence_raw)
        command = command_raw.get("command")
        if not isinstance(command, HostCommand):
            return None
        stored_status = str(record.get("status") or dict(result).get("status") or "OK").upper()
        approved = stored_status not in {"FAILED", "BLOCKED"}
        return {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE" if approved else "REJECT",
            "reason_code": None
            if approved
            else str(dict(result).get("reason_code") or f"SCHEDULER_{stored_status}"),
            "request_id": command.request_id,
            "narrative": str(
                dict(result).get("summary")
                or dict(result).get("detail")
                or stored_status
            )[:500],
            "occurrence_id": occurrence.occurrence_id,
            "task_id": occurrence.task_id,
            "result": dict(result),
            "receipt_ref": str(record.get("receipt_ref") or ""),
        }


class SchedulerResultVerifier:
    def __init__(self, repository: SchedulerExecutionResultRepositoryPort) -> None:
        self._repository = repository

    def verify(
        self,
        *,
        command: HostCommand,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        evidence_refs: Sequence[str],
        **_: Any,
    ) -> dict[str, Any]:
        record = self._repository.get_by_command(command.command_id)
        receipt_ref = str(worker_commit.get("receipt_ref") or "").strip()
        committed_result = worker_commit.get("result")
        passed = bool(
            isinstance(record, Mapping)
            and str(record.get("receipt_ref") or "").strip() == receipt_ref
            and str(record.get("command_id") or "").strip() == command.command_id
            and str(record.get("occurrence_id") or "").strip()
            == str(worker_commit.get("occurrence_id") or "").strip()
            and str(record.get("work_order_id") or "").strip()
            == str(execution_context.get("work_order_id") or "").strip()
            and bool(str(record.get("result_digest") or "").strip())
            and isinstance(record.get("result"), Mapping)
            and isinstance(committed_result, Mapping)
            and dict(record["result"]) == dict(committed_result)
        )
        reason = None if passed else SCHEDULER_DURABLE_RESULT_INVALID
        return {
            "schema_version": "Natsume.agent.verification_result.v1",
            "verification_id": _stable_id("verify", command.command_id),
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "required": True,
            "status": "PASS" if passed else "FAIL",
            "reason_code": reason,
            "evidence_refs": list(evidence_refs),
            "checks": [
                {
                    "check_id": "durable-scheduler-result",
                    "status": "PASS" if passed else "FAIL",
                    "reason_code": reason,
                }
            ],
            "started_at_ms": command.created_at_ms,
            "finished_at_ms": command.created_at_ms,
        }


class SchedulerResultAcceptance:
    def judge(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        evidence_refs: Sequence[str],
        **_: Any,
    ) -> dict[str, Any]:
        accepted = str(worker_commit.get("decision") or "").upper() == "APPROVE"
        reason = None if accepted else "SCHEDULER_EXECUTION_NOT_ACCEPTED"
        criteria = tuple(
            getattr(work_order, "acceptance_criteria", ())
            or ("scheduler handler result persisted",)
        )
        return {
            "schema_version": "Natsume.agent.acceptance_judgement.v1",
            "judgement_id": _stable_id("accept", command.command_id),
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "decision": "ACCEPT" if accepted else "REVIEW",
            "reason_code": reason,
            "evidence_refs": list(evidence_refs),
            "criteria": [
                {
                    "criterion_id": _stable_id(
                        "criterion",
                        f"{command.command_id}:{index}:{criterion}",
                    ),
                    "criterion_text": str(criterion),
                    "verdict": "PASS" if accepted else "FAIL",
                    "evidence_refs": list(evidence_refs),
                    "deterministic": True,
                    "reason_code": reason,
                    "critic_verdict": None,
                }
                for index, criterion in enumerate(criteria, start=1)
            ],
            "critic_candidate": None,
            "started_at_ms": command.created_at_ms,
            "finished_at_ms": command.created_at_ms,
        }


def _work_order_occurrence_id(work_order: Any) -> str:
    input_pack = getattr(work_order, "input_pack", None)
    occurrence_id = (
        str(dict(input_pack).get("occurrence_id") or "").strip()
        if isinstance(input_pack, Mapping)
        else ""
    )
    if not occurrence_id:
        raise SchedulerWorkerError(
            SCHEDULER_DURABLE_RESULT_INVALID,
            "scheduler WorkOrder lacks occurrence_id",
        )
    return occurrence_id


def _canonical_delegate_route(value: object) -> GovernedWorkerRoute:
    try:
        route = GovernedWorkerRoute.parse(str(value or ""))
    except GovernedWorkerRegistryError as exc:
        raise SchedulerWorkerError(
            SCHEDULER_DURABLE_RESULT_INVALID,
            "scheduler TaskNode route is invalid",
        ) from exc
    if route.kind != "delegate" or not route.target_id:
        raise SchedulerWorkerError(
            SCHEDULER_DURABLE_RESULT_INVALID,
            "scheduler TaskNode route must be canonical delegate:<worker_key>",
        )
    return route


def _stable_id(prefix: str, material: str) -> str:
    import hashlib

    return f"{prefix}-scheduler-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:40]}"


def _handler(handler: SchedulerHandler) -> SchedulerHandler:
    if not callable(handler):
        raise TypeError("scheduler handler must be callable")
    return handler


def _worker_key(value: object) -> str:
    import re

    key = str(value or "").strip().lower()
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key) is None:
        raise ValueError("worker_key must be a bounded lower-case identifier")
    return key


def _durable_status(value: object) -> str:
    raw = str(value or "OK").strip().upper()
    return {
        "OK": "SUCCEEDED",
        "SUCCESS": "SUCCEEDED",
        "ACCEPTED": "SUCCEEDED",
        "ERROR": "FAILED",
        "REJECTED": "BLOCKED",
        "DEFERRED": "SKIPPED",
    }.get(raw, raw)


__all__ = [
    "SCHEDULER_DURABLE_RESULT_INVALID",
    "SCHEDULER_HANDLER_NOT_REGISTERED",
    "SCHEDULER_HANDLER_RESULT_INVALID",
    "GovernedSchedulerWorker",
    "SchedulerDefinitionRepositoryPort",
    "SchedulerCommandRepositoryPort",
    "SchedulerExecutionResultRepositoryPort",
    "SchedulerHandler",
    "SchedulerOccurrenceRepositoryPort",
    "SchedulerProjectionReader",
    "SchedulerResultAcceptance",
    "SchedulerResultVerifier",
    "SchedulerWorkerError",
    "SchedulerWorkerRegistry",
    "SchedulerWorkerResolver",
]
