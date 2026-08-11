"""Authority-free Scheduler operation planning for governed Task Runtime.

The Scheduler worker is deliberately split in two.  This module owns the
pre-Acceptance half: it reopens the immutable occurrence and definition, then
emits one canonical ``scheduler.operation.execute`` descriptor.  It never
resolves or invokes a :class:`SchedulerHandler` and it never writes a
``scheduler_execution_result``.  The real handler therefore remains an
Acceptance-effect concern.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Protocol

from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.contracts.agent_runtime.control_route_execution_policy import (
    ControlRouteExecutionPolicyError,
    require_control_route_execution_policy,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
)
from host_brain.scheduler.contracts import (
    SchedulerContractError,
    SchedulerDefinitionSnapshot,
    SchedulerOccurrence,
    occurrence_work_order_id,
)
from host_brain.task_runtime.governed_workers import FrozenNodeInput
from natsume_contracts.task import HostCommand, HostCommandSurface


SCHEDULER_OPERATION_EFFECT_TYPE = "scheduler.operation.execute"
SCHEDULER_OPERATION_PLAN_SCHEMA_VERSION = "Natsume.scheduler.operation_plan.v1"

SCHEDULER_OPERATION_PLAN_INVALID = "SCHEDULER_OPERATION_PLAN_INVALID"
SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID = (
    "SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID"
)
SCHEDULER_OPERATION_PLAN_DEFINITION_DRIFT = (
    "SCHEDULER_OPERATION_PLAN_DEFINITION_DRIFT"
)

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SCHEDULER_SCOPE_PREFIX = "scheduler:"
_OPERATOR_SCOPE_PREFIX = "operator:scheduler.task.trigger:"


class SchedulerOperationPlanError(RuntimeError):
    """A durable Scheduler plan identity failed closed."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or SCHEDULER_OPERATION_PLAN_INVALID)
        self.detail = str(detail or "Scheduler operation plan is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


class SchedulerOccurrenceReaderPort(Protocol):
    def get_occurrence(self, occurrence_id: str) -> Mapping[str, Any] | None: ...


class SchedulerDefinitionReaderPort(Protocol):
    def get_revision(
        self,
        task_id: str,
        revision: int,
    ) -> Mapping[str, Any] | None: ...


class WorkerProjectionReaderPort(Protocol):
    def load(self, receipt_ref: str) -> Mapping[str, Any] | None: ...


@dataclass(frozen=True, slots=True)
class _DurablePlanAuthority:
    occurrence: SchedulerOccurrence
    definition: SchedulerDefinitionSnapshot
    definition_ref: str
    occurrence_digest: str
    definition_digest: str
    operation_fingerprint: str

    @property
    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEDULER_OPERATION_PLAN_SCHEMA_VERSION,
            "occurrence_id": self.occurrence.occurrence_id,
            "definition_ref": self.definition_ref,
            "definition_revision": self.definition.definition_revision,
            "worker_key": self.definition.worker_key,
            "operation_fingerprint": self.operation_fingerprint,
        }

    @property
    def descriptor(self) -> dict[str, Any]:
        return {
            "effect_type": SCHEDULER_OPERATION_EFFECT_TYPE,
            "commit_scope": "NODE",
            "payload": self.payload,
        }


class SchedulerOperationPlanExecutor:
    """Produce one immutable Scheduler operation descriptor and nothing else."""

    def __init__(
        self,
        *,
        occurrence_repository: SchedulerOccurrenceReaderPort,
        definition_repository: SchedulerDefinitionReaderPort,
    ) -> None:
        if not callable(getattr(occurrence_repository, "get_occurrence", None)):
            raise TypeError("occurrence_repository.get_occurrence is required")
        if not callable(getattr(definition_repository, "get_revision", None)):
            raise TypeError("definition_repository.get_revision is required")
        self._occurrences = occurrence_repository
        self._definitions = definition_repository

    async def execute_stream(
        self,
        frozen: FrozenNodeInput,
    ) -> AsyncIterator[Mapping[str, Any]]:
        if not isinstance(frozen, FrozenNodeInput):
            raise SchedulerOperationPlanError(
                SCHEDULER_OPERATION_PLAN_INVALID,
                "Scheduler plan executor requires FrozenNodeInput",
            )
        authority = _authority_from_frozen(
            frozen=frozen,
            occurrences=self._occurrences,
            definitions=self._definitions,
        )
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "reason_code": None,
            "request_id": frozen.request_id,
            "narrative": (
                "已冻结定时任务执行计划："
                f"{authority.occurrence.task_id}@"
                f"{authority.definition.definition_revision}"
            ),
            "route_type": frozen.route,
            "target_id": frozen.target_id,
            "acceptance_effect_intents": [authority.descriptor],
            "audit_refs": {
                "path": "scheduler_operation_plan:durable_authority",
                "scheduler_occurrence_ref": authority.occurrence.occurrence_id,
                "scheduler_occurrence_digest": authority.occurrence_digest,
                "scheduler_definition_ref": authority.definition_ref,
                "scheduler_definition_digest": authority.definition_digest,
                "scheduler_operation_fingerprint": authority.operation_fingerprint,
            },
        }


class SchedulerOperationPlanVerifier:
    """Independently reopen and verify the canonical worker projection."""

    def __init__(
        self,
        *,
        projection_store: WorkerProjectionReaderPort,
        occurrence_repository: SchedulerOccurrenceReaderPort,
        definition_repository: SchedulerDefinitionReaderPort,
    ) -> None:
        self._authority = _SchedulerOperationPlanProjectionAuthority(
            projection_store=projection_store,
            occurrence_repository=occurrence_repository,
            definition_repository=definition_repository,
        )

    def verify(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        evidence_refs: Sequence[str],
        **_: Any,
    ) -> dict[str, Any]:
        reason: str | None = None
        receipt_ref = str(dict(worker_commit or {}).get("receipt_ref") or "").strip()
        try:
            self._authority.require(
                command=command,
                work_order=work_order,
                execution_context=execution_context,
                worker_commit=worker_commit,
            )
        except SchedulerOperationPlanError as exc:
            reason = exc.reason_code
        except (SchedulerContractError, WorkerProjectionStoreError, ValueError, TypeError):
            reason = SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID

        passed = reason is None
        refs = _evidence_refs(evidence_refs, receipt_ref)
        context = dict(execution_context or {})
        return {
            "schema_version": "Natsume.agent.verification_result.v1",
            "verification_id": _stable_id("verify", command.command_id),
            "work_order_id": str(context.get("work_order_id") or ""),
            "task_graph_id": str(context.get("graph_id") or ""),
            "task_node_id": str(context.get("node_id") or ""),
            "attempt_id": str(context.get("attempt_id") or ""),
            "required": True,
            "status": "PASS" if passed else "FAIL",
            "reason_code": reason,
            "evidence_refs": refs,
            "checks": [
                {
                    "check_id": "durable-scheduler-operation-plan",
                    "status": "PASS" if passed else "FAIL",
                    "reason_code": reason,
                }
            ],
            "started_at_ms": command.created_at_ms,
            "finished_at_ms": command.created_at_ms,
        }


class SchedulerOperationPlanAcceptance:
    """Accept only a verified canonical plan, never a handler/domain result."""

    def __init__(
        self,
        *,
        projection_store: WorkerProjectionReaderPort,
        occurrence_repository: SchedulerOccurrenceReaderPort,
        definition_repository: SchedulerDefinitionReaderPort,
    ) -> None:
        self._authority = _SchedulerOperationPlanProjectionAuthority(
            projection_store=projection_store,
            occurrence_repository=occurrence_repository,
            definition_repository=definition_repository,
        )

    def judge(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        verification_result: Mapping[str, Any],
        evidence_refs: Sequence[str],
        **_: Any,
    ) -> dict[str, Any]:
        reason: str | None = None
        verification = dict(verification_result or {})
        try:
            self._authority.require(
                command=command,
                work_order=work_order,
                execution_context=execution_context,
                worker_commit=worker_commit,
            )
            _require_verification_binding(
                verification=verification,
                command=command,
                execution_context=execution_context,
            )
        except SchedulerOperationPlanError as exc:
            reason = exc.reason_code
        except (SchedulerContractError, WorkerProjectionStoreError, ValueError, TypeError):
            reason = SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID

        accepted = reason is None
        context = dict(execution_context or {})
        receipt_ref = str(dict(worker_commit or {}).get("receipt_ref") or "").strip()
        refs = _evidence_refs(evidence_refs, receipt_ref)
        raw_criteria = tuple(work_order.acceptance_criteria or ())
        criteria = raw_criteria or (
            "Scheduler operation plan is canonical and authority-free",
        )
        return {
            "schema_version": "Natsume.agent.acceptance_judgement.v1",
            "judgement_id": _stable_id("accept", command.command_id),
            "work_order_id": str(context.get("work_order_id") or ""),
            "task_graph_id": str(context.get("graph_id") or ""),
            "task_node_id": str(context.get("node_id") or ""),
            "attempt_id": str(context.get("attempt_id") or ""),
            "decision": "ACCEPT" if accepted else "REVIEW",
            "reason_code": reason,
            "evidence_refs": refs,
            "criteria": [
                {
                    "criterion_id": _stable_id(
                        "criterion",
                        f"{command.command_id}:{index}:{criterion}",
                    ),
                    "criterion_text": str(criterion),
                    "verdict": "PASS" if accepted else "FAIL",
                    "evidence_refs": refs,
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


class _SchedulerOperationPlanProjectionAuthority:
    def __init__(
        self,
        *,
        projection_store: WorkerProjectionReaderPort,
        occurrence_repository: SchedulerOccurrenceReaderPort,
        definition_repository: SchedulerDefinitionReaderPort,
    ) -> None:
        if not callable(getattr(projection_store, "load", None)):
            raise TypeError("projection_store.load is required")
        if not callable(getattr(occurrence_repository, "get_occurrence", None)):
            raise TypeError("occurrence_repository.get_occurrence is required")
        if not callable(getattr(definition_repository, "get_revision", None)):
            raise TypeError("definition_repository.get_revision is required")
        self._projections = projection_store
        self._occurrences = occurrence_repository
        self._definitions = definition_repository

    def require(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
    ) -> _DurablePlanAuthority:
        if not isinstance(command, HostCommand) or not isinstance(work_order, WorkOrderV3):
            raise SchedulerOperationPlanError(
                SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID,
                "HostCommand and WorkOrderV3 are required",
            )
        work_order.validate()
        try:
            require_control_route_execution_policy(
                command=command,
                work_order=work_order,
            )
        except ControlRouteExecutionPolicyError as exc:
            raise SchedulerOperationPlanError(
                SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID,
                exc.detail,
            ) from exc
        commit = dict(worker_commit or {})
        receipt_ref = str(commit.get("receipt_ref") or "").strip()
        if not receipt_ref.startswith("worker-projection://"):
            raise SchedulerOperationPlanError(
                SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID,
                "worker commit lacks a durable projection receipt",
            )
        raw_projection = self._projections.load(receipt_ref)
        if not isinstance(raw_projection, Mapping):
            raise SchedulerOperationPlanError(
                SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID,
                "canonical worker projection is missing",
            )
        projection = validate_worker_projection_payload(
            raw_projection,
            expected_projection_id=receipt_ref,
        )
        _require_public_commit_binding(commit=commit, projection=projection)

        pack = dict(work_order.input_pack or {})
        occurrence_id = str(pack.get("occurrence_id") or "").strip()
        definition_ref = str(pack.get("definition_ref") or "").strip()
        operator = command.surface is HostCommandSurface.OPERATOR
        authority = _load_durable_authority(
            occurrence_id=occurrence_id,
            definition_ref=definition_ref,
            worker_key=command.target_id,
            operation_fingerprint=command.operation_fingerprint,
            operator=operator,
            occurrences=self._occurrences,
            definitions=self._definitions,
        )
        _require_contract_binding(
            command=command,
            work_order=work_order,
            execution_context=execution_context,
            projection=projection,
            authority=authority,
        )
        if projection.get("acceptance_effect_intents") != [authority.descriptor]:
            raise SchedulerOperationPlanError(
                SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID,
                "Scheduler operation descriptor differs from durable authority",
            )
        audit = dict(projection.get("audit_refs") or {})
        expected_audit = {
            "scheduler_occurrence_ref": authority.occurrence.occurrence_id,
            "scheduler_occurrence_digest": authority.occurrence_digest,
            "scheduler_definition_ref": authority.definition_ref,
            "scheduler_definition_digest": authority.definition_digest,
            "scheduler_operation_fingerprint": authority.operation_fingerprint,
        }
        if any(audit.get(key) != value for key, value in expected_audit.items()):
            raise SchedulerOperationPlanError(
                SCHEDULER_OPERATION_PLAN_DEFINITION_DRIFT,
                "Scheduler plan audit binding differs from reopened durable state",
            )
        return authority


def _authority_from_frozen(
    *,
    frozen: FrozenNodeInput,
    occurrences: SchedulerOccurrenceReaderPort,
    definitions: SchedulerDefinitionReaderPort,
) -> _DurablePlanAuthority:
    if frozen.route != "delegate" or not frozen.target_id:
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_INVALID,
            "Scheduler operation plan requires one frozen delegate target",
        )
    pack = dict(frozen.input_pack or {})
    occurrence_id = str(pack.get("occurrence_id") or "").strip()
    definition_ref = str(pack.get("definition_ref") or "").strip()
    operator = _operator_scope(frozen.business_scope)
    authority = _load_durable_authority(
        occurrence_id=occurrence_id,
        definition_ref=definition_ref,
        worker_key=frozen.target_id,
        operation_fingerprint=frozen.operation_fingerprint,
        operator=operator,
        occurrences=occurrences,
        definitions=definitions,
    )
    if (
        authority.occurrence.request_id != frozen.request_id
        or authority.occurrence.principal_id != frozen.principal_id
        or occurrence_work_order_id(authority.occurrence.occurrence_id)
        != frozen.work_order_id
        or frozen.root_work_order_id != frozen.work_order_id
        or occurrence_id not in frozen.input_refs
        or definition_ref not in frozen.input_refs
    ):
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_INVALID,
            "frozen node differs from Scheduler occurrence authority",
        )
    return authority


def _load_durable_authority(
    *,
    occurrence_id: str,
    definition_ref: str,
    worker_key: str,
    operation_fingerprint: str,
    operator: bool,
    occurrences: SchedulerOccurrenceReaderPort,
    definitions: SchedulerDefinitionReaderPort,
) -> _DurablePlanAuthority:
    if not occurrence_id or not definition_ref:
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_INVALID,
            "Scheduler WorkOrder lacks occurrence or definition reference",
        )
    raw_occurrence = occurrences.get_occurrence(occurrence_id)
    if not isinstance(raw_occurrence, Mapping):
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_INVALID,
            "durable Scheduler occurrence is missing",
        )
    occurrence = SchedulerOccurrence.from_mapping(raw_occurrence)
    occurrence_payload_digest = _canonical_digest(occurrence.payload)
    if raw_occurrence.get("payload_digest") != occurrence_payload_digest:
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_INVALID,
            "durable Scheduler occurrence payload digest drifted",
        )
    raw_definition = definitions.get_revision(
        occurrence.task_id,
        occurrence.definition_revision,
    )
    if not isinstance(raw_definition, Mapping):
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_DEFINITION_DRIFT,
            "durable Scheduler definition revision is missing",
        )
    definition = SchedulerDefinitionSnapshot.from_mapping(raw_definition)
    definition_digest = str(raw_definition.get("definition_digest") or "").strip()
    if (
        not _SHA256_RE.fullmatch(definition_digest)
        or definition_digest != _canonical_digest(definition.definition)
    ):
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_DEFINITION_DRIFT,
            "durable Scheduler definition digest drifted",
        )
    expected_ref = scheduler_definition_ref(definition)
    expected_fingerprint = scheduler_operation_fingerprint(
        occurrence=occurrence,
        definition=definition,
        operator=operator,
    )
    if (
        occurrence.occurrence_id != occurrence_id
        or definition.task_id != occurrence.task_id
        or definition.definition_revision != occurrence.definition_revision
        or expected_ref != definition_ref
        or definition.worker_key != str(worker_key or "").strip().lower()
        or expected_fingerprint != str(operation_fingerprint or "").strip()
    ):
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_DEFINITION_DRIFT,
            "Scheduler occurrence, definition, target or fingerprint drifted",
        )
    return _DurablePlanAuthority(
        occurrence=occurrence,
        definition=definition,
        definition_ref=expected_ref,
        occurrence_digest=occurrence.payload_sha256,
        definition_digest=definition_digest,
        operation_fingerprint=expected_fingerprint,
    )


def _require_contract_binding(
    *,
    command: HostCommand,
    work_order: WorkOrderV3,
    execution_context: Mapping[str, Any],
    projection: Mapping[str, Any],
    authority: _DurablePlanAuthority,
) -> None:
    context = dict(execution_context or {})
    occurrence = authority.occurrence
    definition = authority.definition
    pack = dict(work_order.input_pack or {})
    metadata = dict(work_order.metadata or {})
    lineage = dict(metadata.get("planning_lineage") or {})
    raw_bindings = lineage.get("node_bindings")
    bindings = (
        [dict(value) for value in raw_bindings if isinstance(value, Mapping)]
        if isinstance(raw_bindings, list)
        else []
    )
    node_id = str(context.get("node_id") or "").strip()
    matching_bindings = [
        value for value in bindings if str(value.get("node_id") or "") == node_id
    ]
    expected_surface = (
        HostCommandSurface.OPERATOR.value
        if command.surface is HostCommandSurface.OPERATOR
        else HostCommandSurface.SCHEDULER.value
    )
    identity_pairs = (
        (command.route_type, "delegate"),
        (command.target_id, definition.worker_key),
        (command.input_ref, occurrence.occurrence_id),
        (command.input_sha256, occurrence.body_sha256),
        (command.request_id, occurrence.request_id),
        (command.principal_id, occurrence.principal_id),
        (command.ingress_claim_ref, occurrence.claim_ref),
        (command.ingress_audit_ref, occurrence.audit_ref),
        (command.operation_fingerprint, authority.operation_fingerprint),
        (work_order.work_order_id, occurrence_work_order_id(occurrence.occurrence_id)),
        (work_order.root_work_order_id, work_order.work_order_id),
        (work_order.task_graph_id, command.graph_id),
        (work_order.task_node_id, node_id),
        (work_order.operation_fingerprint, authority.operation_fingerprint),
        (work_order.business_idempotency_scope, command.business_scope),
        (pack.get("occurrence_id"), occurrence.occurrence_id),
        (pack.get("definition_ref"), authority.definition_ref),
        (str(metadata.get("worker_key") or "").lower(), definition.worker_key),
        (metadata.get("definition_revision"), definition.definition_revision),
        (metadata.get("command_surface"), expected_surface),
        (context.get("command_id"), command.command_id),
        (context.get("request_id"), command.request_id),
        (context.get("principal_id"), command.principal_id),
        (context.get("work_order_id"), work_order.work_order_id),
        (context.get("graph_id"), command.graph_id),
        (context.get("graph_revision"), command.graph_revision),
        (context.get("worker_route"), f"delegate:{definition.worker_key}"),
        (context.get("target_id"), definition.worker_key),
        (context.get("business_scope"), command.business_scope),
        (context.get("operation_fingerprint"), authority.operation_fingerprint),
        (projection.get("command_id"), command.command_id),
        (projection.get("request_id"), command.request_id),
        (projection.get("route_type"), "delegate"),
        (projection.get("target_id"), definition.worker_key),
        (projection.get("graph_id"), command.graph_id),
        (projection.get("graph_revision"), command.graph_revision),
        (projection.get("node_id"), node_id),
        (projection.get("attempt_id"), context.get("attempt_id")),
        (projection.get("node_grant_ref"), context.get("node_grant_ref")),
    )
    if any(actual != expected for actual, expected in identity_pairs):
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID,
            "Scheduler command, WorkOrder, execution and projection identities differ",
        )
    if (
        command.surface
        not in {HostCommandSurface.SCHEDULER, HostCommandSurface.OPERATOR}
        or work_order.graph_revision != command.graph_revision
        or command.graph_revision < 1
        or tuple(work_order.allowed_actions)
        != (f"delegate:{definition.worker_key}",)
        or occurrence.occurrence_id not in tuple(work_order.input_refs)
        or authority.definition_ref not in tuple(work_order.input_refs)
        or len(matching_bindings) != 1
        or str(matching_bindings[0].get("worker_route") or "").lower()
        != "delegate"
        or str(matching_bindings[0].get("target_id") or "")
        != definition.worker_key
        or str(lineage.get("graph_id") or "") != command.graph_id
        or int(lineage.get("revision") or 0) != command.graph_revision
        or str(lineage.get("root_work_order_id") or "")
        != work_order.root_work_order_id
        or (
            command.surface is HostCommandSurface.OPERATOR
            and str(metadata.get("operator_command_id") or "")
            != "scheduler.task.trigger"
        )
    ):
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID,
            "Scheduler plan lineage or action boundary differs",
        )


def _require_public_commit_binding(
    *,
    commit: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> None:
    allowed = {
        "phase",
        "done",
        "decision",
        "reason_code",
        "command_id",
        "request_id",
        "narrative",
        "route_type",
        "target_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "node_grant_ref",
        "audit_refs",
        "acceptance_effect_intents",
        "receipt_ref",
    }
    if "budget_usage" in commit:
        allowed.add("budget_usage")
    if set(commit) != allowed:
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID,
            "public Scheduler plan commit contains missing or domain-result fields",
        )
    if commit.get("receipt_ref") != projection.get("projection_id"):
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID,
            "worker commit receipt differs from canonical projection",
        )
    keys = (
        "phase",
        "done",
        "decision",
        "reason_code",
        "command_id",
        "request_id",
        "narrative",
        "route_type",
        "target_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "node_grant_ref",
        "acceptance_effect_intents",
        "budget_usage",
    )
    for key in keys:
        if key in projection or key in commit:
            if commit.get(key) != projection.get(key):
                raise SchedulerOperationPlanError(
                    SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID,
                    f"public worker commit drifted from projection field {key}",
                )
    if projection.get("decision") != "APPROVE":
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID,
            "Scheduler operation plan projection is not approved",
        )


def _require_verification_binding(
    *,
    verification: Mapping[str, Any],
    command: HostCommand,
    execution_context: Mapping[str, Any],
) -> None:
    context = dict(execution_context or {})
    expected = {
        "status": "PASS",
        "work_order_id": str(context.get("work_order_id") or ""),
        "task_graph_id": str(context.get("graph_id") or ""),
        "task_node_id": str(context.get("node_id") or ""),
        "attempt_id": str(context.get("attempt_id") or ""),
    }
    if any(verification.get(key) != value for key, value in expected.items()) or (
        verification.get("verification_id") != _stable_id("verify", command.command_id)
    ):
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID,
            "Acceptance received no exact passing Scheduler plan verification",
        )


def scheduler_definition_ref(definition: SchedulerDefinitionSnapshot) -> str:
    task_id = re.sub(
        r"[^A-Za-z0-9._~:@/-]+",
        "-",
        str(definition.task_id or "").strip(),
    ).strip("-")
    return (
        f"scheduler-definition://{task_id or 'scheduler'}/"
        f"{definition.definition_revision}"
    )


def scheduler_operation_fingerprint(
    *,
    occurrence: SchedulerOccurrence,
    definition: SchedulerDefinitionSnapshot,
    operator: bool,
) -> str:
    material: dict[str, Any] = {
        "definition_revision": definition.definition_revision,
        "occurrence_id": occurrence.occurrence_id,
        "payload_sha256": occurrence.payload_sha256,
        "worker_key": definition.worker_key,
    }
    if operator:
        material.update(
            {
                "command_surface": HostCommandSurface.OPERATOR.value,
                "operator_command_id": "scheduler.task.trigger",
            }
        )
    return _canonical_digest(material)


def _operator_scope(business_scope: str) -> bool:
    scope = str(business_scope or "").strip()
    if scope.startswith(_OPERATOR_SCOPE_PREFIX):
        return True
    if scope.startswith(_SCHEDULER_SCOPE_PREFIX):
        return False
    raise SchedulerOperationPlanError(
        SCHEDULER_OPERATION_PLAN_INVALID,
        "Scheduler plan business scope is invalid",
    )


def _canonical_digest(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise SchedulerOperationPlanError(
            SCHEDULER_OPERATION_PLAN_INVALID,
            "Scheduler durable payload is not canonical JSON",
        ) from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _stable_id(prefix: str, material: str) -> str:
    digest = hashlib.sha256(str(material or "").encode("utf-8")).hexdigest()[:40]
    return f"{prefix}-scheduler-operation-plan-{digest}"


def _evidence_refs(values: Sequence[str], receipt_ref: str) -> list[str]:
    return list(
        dict.fromkeys(
            str(value or "").strip()
            for value in (*tuple(values or ()), receipt_ref)
            if str(value or "").strip()
        )
    )


__all__ = [
    "SCHEDULER_OPERATION_EFFECT_TYPE",
    "SCHEDULER_OPERATION_PLAN_DEFINITION_DRIFT",
    "SCHEDULER_OPERATION_PLAN_INVALID",
    "SCHEDULER_OPERATION_PLAN_PROJECTION_INVALID",
    "SCHEDULER_OPERATION_PLAN_SCHEMA_VERSION",
    "SchedulerDefinitionReaderPort",
    "SchedulerOccurrenceReaderPort",
    "SchedulerOperationPlanAcceptance",
    "SchedulerOperationPlanError",
    "SchedulerOperationPlanExecutor",
    "SchedulerOperationPlanVerifier",
    "WorkerProjectionReaderPort",
    "scheduler_definition_ref",
    "scheduler_operation_fingerprint",
]
