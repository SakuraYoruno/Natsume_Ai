"""Execute one Scheduler operation only after governed node Acceptance.

The pre-Acceptance worker is allowed to describe an operation, but it never
calls the registered Scheduler handler.  This app-local handler reopens every
durable identity carried by the accepted effect, passes only frozen input and
the Dispatcher's private Action Preview context to the real handler, and then
attributes the result to the immutable Scheduler result ledger.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from natsume_contracts.task import HostCommand, HostCommandSurface
from natsume_contracts.task.v1 import ExecutorAssignment

from host_brain.core.runtime_helpers import maybe_await as _resolve
from host_brain.contracts.agent_runtime.task_graph import TaskGraph, TaskNode
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.scheduler.contracts import (
    SchedulerDefinitionSnapshot,
    SchedulerOccurrence,
    occurrence_work_order_id,
)
from host_brain.scheduler.worker_registry import (
    SCHEDULER_DURABLE_RESULT_INVALID,
    SchedulerWorkerError,
    SchedulerWorkerRegistry,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)


SCHEDULER_ACCEPTED_OPERATION_SCHEMA = "Natsume.scheduler.accepted_operation.v1"
SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA = (
    "Natsume.scheduler.action_preview_context.v2"
)

_CANONICAL_FIELDS = frozenset(
    {
        "schema_version",
        "occurrence_id",
        "definition_ref",
        "definition_revision",
        "worker_key",
        "operation_fingerprint",
        "command_id",
        "request_id",
        "work_order_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
    }
)
_PRIVATE_PREVIEW_FIELD = "_action_preview_context"
_PRIVATE_TERMINAL_PROOF_FIELD = "_action_preview_terminal_proof"
_PREVIEW_FIELDS = frozenset(
    {
        "schema_version",
        "origin_authority_kind",
        "origin_event_id",
        "origin_receipt_ref",
        "origin_boundary",
    }
)
_BOUNDARY_FIELDS = frozenset(
    {
        "action_kind",
        "action_scope",
        "risk_level",
        "action_args",
        "context_pack_digest",
        "scene_identity",
        "task_graph_id",
        "graph_revision",
        "task_node_id",
        "parent_governor_grant_ref",
        "node_grant_ref",
        "node_grant",
        "authority_context",
    }
)
_AUTHORITY_FIELDS = frozenset(
    {"schema_version", "effect_id", "lease_owner_id", "fencing_token"}
)
_NODE_GRANT_FIELDS = frozenset({"grant_ref", "binding_digest"})
_DURABLE_REF = re.compile(r"^[a-z][a-z0-9+.-]{1,31}://\S+$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_WORKER_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


@dataclass(frozen=True, slots=True)
class _ReopenedOperation:
    occurrence: SchedulerOccurrence
    definition: SchedulerDefinitionSnapshot
    command: HostCommand
    work_order: WorkOrderV3
    graph: TaskGraph
    node: TaskNode
    assignment: ExecutorAssignment


class SchedulerAcceptedOperationHandler:
    """Invoke one exact Scheduler handler from a claimed Acceptance Effect."""

    def __init__(
        self,
        *,
        worker_registry: SchedulerWorkerRegistry,
        definition_repository: Any,
        occurrence_repository: Any,
        command_repository: Any,
        result_repository: Any,
    ) -> None:
        if not isinstance(worker_registry, SchedulerWorkerRegistry):
            raise TypeError("worker_registry must be SchedulerWorkerRegistry")
        _require_method(definition_repository, "get_revision")
        _require_method(occurrence_repository, "get_occurrence")
        _require_method(command_repository, "get")
        _require_method(result_repository, "get_by_command")
        _require_method(result_repository, "append")
        self._workers = worker_registry
        self._definitions = definition_repository
        self._occurrences = occurrence_repository
        self._commands = command_repository
        self._results = result_repository

    async def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        canonical, preview_context = _accepted_payload(payload)
        reopened = await self._reopen(canonical, preview_context)

        existing = await _resolve(
            self._results.get_by_command(canonical["command_id"])
        )
        if existing is not None:
            record = _exact_result_record(existing, canonical=canonical)
            return _handler_outcome(record, replayed=True)

        handler = self._workers.resolve(
            worker_key=reopened.definition.worker_key,
            task_id=reopened.occurrence.task_id,
        )
        execution_context = _scheduler_execution_context(
            canonical=canonical,
            reopened=reopened,
            preview_context=preview_context,
        )

        try:
            raw_result = handler(
                occurrence=reopened.occurrence,
                definition=reopened.definition,
                command=reopened.command,
                execution_context=execution_context,
            )
            raw_result = await _resolve(raw_result)
            normalized = _scheduler_handler_result(raw_result)
            if _contains_private_runtime_authority(normalized):
                raise SchedulerWorkerError(
                    SCHEDULER_DURABLE_RESULT_INVALID,
                    "scheduler handler result contains private runtime authority",
                )
            _canonical_json(normalized)
        except AcceptanceEffectOutcomeUnknown:
            raise
        except Exception as exc:
            replay = await self._reconcile_after_invocation(canonical)
            if replay is not None:
                return _handler_outcome(replay, replayed=True)
            raise AcceptanceEffectOutcomeUnknown(
                "Scheduler handler invocation has no exact durable result "
                f"for command {canonical['command_id']}: "
                f"{type(exc).__name__}: {str(exc)[:1200]}"
            ) from exc

        try:
            await _resolve(
                self._results.append(
                    occurrence_id=canonical["occurrence_id"],
                    command_id=canonical["command_id"],
                    work_order_id=canonical["work_order_id"],
                    result=normalized,
                )
            )
            durable = await _resolve(
                self._results.get_by_command(canonical["command_id"])
            )
            record = _exact_result_record(
                durable,
                canonical=canonical,
                expected_result=normalized,
            )
        except AcceptanceEffectOutcomeUnknown:
            raise
        except Exception as exc:
            # ``append`` may have committed before its response was lost.  A
            # fresh exact read is the only safe attribution; absence or drift
            # is an unknown outcome and must not become an ordinary retry.
            replay = await self._reconcile_after_invocation(
                canonical,
                expected_result=normalized,
            )
            if replay is not None:
                return _handler_outcome(replay, replayed=True)
            raise AcceptanceEffectOutcomeUnknown(
                "Scheduler result append has no exact durable attribution "
                f"for command {canonical['command_id']}: "
                f"{type(exc).__name__}: {str(exc)[:1200]}"
            ) from exc
        return _handler_outcome(record, replayed=False)

    async def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Reopen an unknown operation result without re-invoking its worker.

        The Dispatcher may call this path only after an invocation crossed the
        external-effect boundary and lost attribution.  Reconciliation is
        therefore read-only: it first checks the Scheduler receipt, then an
        explicitly registered worker-owned receipt resolver.  It can never
        fall through to the worker's effectful handler.
        """

        canonical, preview_context = _accepted_payload(payload)
        reopened = await self._reopen(canonical, preview_context)
        try:
            existing = await _resolve(
                self._results.get_by_command(canonical["command_id"])
            )
            if existing is not None:
                record = _exact_result_record(existing, canonical=canonical)
            else:
                reconciler = self._workers.resolve_reconciler(
                    worker_key=reopened.definition.worker_key,
                    task_id=reopened.occurrence.task_id,
                )
                if reconciler is None:
                    raise LookupError("exact Scheduler result is absent")
                raw_result = reconciler(
                    occurrence=reopened.occurrence,
                    definition=reopened.definition,
                    command=reopened.command,
                    execution_context=_scheduler_execution_context(
                        canonical=canonical,
                        reopened=reopened,
                        preview_context=preview_context,
                    ),
                )
                normalized = _scheduler_handler_result(await _resolve(raw_result))
                if _contains_private_runtime_authority(normalized):
                    raise SchedulerWorkerError(
                        SCHEDULER_DURABLE_RESULT_INVALID,
                        "scheduler reconciler result contains private runtime authority",
                    )
                _canonical_json(normalized)
                await _resolve(
                    self._results.append(
                        occurrence_id=canonical["occurrence_id"],
                        command_id=canonical["command_id"],
                        work_order_id=canonical["work_order_id"],
                        result=normalized,
                    )
                )
                durable = await _resolve(
                    self._results.get_by_command(canonical["command_id"])
                )
                record = _exact_result_record(
                    durable,
                    canonical=canonical,
                    expected_result=normalized,
                )
        except AcceptanceEffectOutcomeUnknown:
            raise
        except Exception as exc:
            raise AcceptanceEffectOutcomeUnknown(
                "Scheduler operation reconciliation found no exact durable "
                f"result for command {canonical['command_id']}: "
                f"{type(exc).__name__}: {str(exc)[:1200]}"
            ) from exc
        return _handler_outcome(record, replayed=True)

    async def _reopen(
        self,
        canonical: Mapping[str, Any],
        preview_context: Mapping[str, Any],
    ) -> _ReopenedOperation:
        command_record = await _resolve(
            self._commands.get(canonical["command_id"])
        )
        if not isinstance(command_record, Mapping):
            _invalid("accepted Scheduler command is missing")
        command = command_record.get("command")
        work_order = command_record.get("work_order")
        graph = command_record.get("graph")
        assignment = command_record.get("assignment")
        if (
            not isinstance(command, HostCommand)
            or not isinstance(work_order, WorkOrderV3)
            or not isinstance(graph, TaskGraph)
            or not isinstance(assignment, ExecutorAssignment)
        ):
            _invalid("accepted Scheduler admission is incomplete or untyped")

        occurrence_raw = await _resolve(
            self._occurrences.get_occurrence(canonical["occurrence_id"])
        )
        if not isinstance(occurrence_raw, Mapping):
            _invalid("accepted Scheduler occurrence is missing")
        occurrence = SchedulerOccurrence.from_mapping(occurrence_raw)

        definition_raw = await _resolve(
            self._definitions.get_revision(
                occurrence.task_id,
                canonical["definition_revision"],
            )
        )
        if not isinstance(definition_raw, Mapping):
            _invalid("accepted Scheduler definition revision is missing")
        definition = SchedulerDefinitionSnapshot.from_mapping(definition_raw)

        node = next(
            (candidate for candidate in graph.nodes if candidate.node_id == canonical["node_id"]),
            None,
        )
        if node is None:
            _invalid("accepted Scheduler TaskNode is missing")

        _validate_reopened_identity(
            canonical=canonical,
            preview_context=preview_context,
            occurrence=occurrence,
            definition=definition,
            command=command,
            work_order=work_order,
            graph=graph,
            node=node,
            assignment=assignment,
        )
        return _ReopenedOperation(
            occurrence=occurrence,
            definition=definition,
            command=command,
            work_order=work_order,
            graph=graph,
            node=node,
            assignment=assignment,
        )

    async def _reconcile_after_invocation(
        self,
        canonical: Mapping[str, Any],
        *,
        expected_result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        try:
            durable = await _resolve(
                self._results.get_by_command(canonical["command_id"])
            )
            if durable is None:
                return None
            return _exact_result_record(
                durable,
                canonical=canonical,
                expected_result=expected_result,
            )
        except Exception:
            return None


def _scheduler_execution_context(
    *,
    canonical: Mapping[str, Any],
    reopened: _ReopenedOperation,
    preview_context: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "command_id": canonical["command_id"],
        "request_id": canonical["request_id"],
        "work_order_id": canonical["work_order_id"],
        "graph_id": canonical["graph_id"],
        "graph_revision": canonical["graph_revision"],
        "node_id": canonical["node_id"],
        "attempt_id": canonical["attempt_id"],
        "worker_route": f"delegate:{canonical['worker_key']}",
        "target_id": canonical["worker_key"],
        "frozen_input_pack": deepcopy(reopened.work_order.input_pack),
        # This is an in-process proof context issued by the Dispatcher for the
        # current Acceptance Effect claim. It is not a live grant and must
        # never be copied into an immutable Scheduler result.
        _PRIVATE_PREVIEW_FIELD: deepcopy(preview_context),
    }


def _accepted_payload(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, Mapping):
        _invalid("accepted Scheduler operation payload must be a mapping")
    value = dict(payload)
    preview_raw = value.pop(_PRIVATE_PREVIEW_FIELD, None)
    terminal_raw = value.pop(_PRIVATE_TERMINAL_PROOF_FIELD, None)
    if frozenset(value) != _CANONICAL_FIELDS:
        _invalid("accepted Scheduler operation payload fields drifted")
    if value.get("schema_version") != SCHEDULER_ACCEPTED_OPERATION_SCHEMA:
        _invalid("accepted Scheduler operation schema drifted")

    canonical: dict[str, Any] = {}
    for field in _CANONICAL_FIELDS - {"definition_revision", "graph_revision"}:
        text = str(value.get(field) or "").strip()
        if not text:
            _invalid(f"accepted Scheduler operation {field} is required")
        canonical[field] = text
    for field in ("definition_revision", "graph_revision"):
        raw = value.get(field)
        if isinstance(raw, bool) or type(raw) is not int or raw < 1:
            _invalid(f"accepted Scheduler operation {field} is invalid")
        canonical[field] = raw
    if not _DURABLE_REF.fullmatch(canonical["occurrence_id"]):
        _invalid("accepted Scheduler occurrence_id is not a durable reference")
    if not _DURABLE_REF.fullmatch(canonical["definition_ref"]):
        _invalid("accepted Scheduler definition_ref is not a durable reference")
    if not _SHA256.fullmatch(canonical["operation_fingerprint"]):
        _invalid("accepted Scheduler operation_fingerprint is invalid")
    if not _WORKER_KEY.fullmatch(canonical["worker_key"]):
        _invalid("accepted Scheduler worker_key is invalid")

    preview = _private_preview_context(
        preview_raw,
        canonical=canonical,
        terminal_proof=terminal_raw,
    )
    return canonical, preview


def _private_preview_context(
    value: Any,
    *,
    canonical: Mapping[str, Any],
    terminal_proof: Any,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _invalid("Scheduler operation lacks Dispatcher private Preview context")
    context = dict(value)
    if frozenset(context) != _PREVIEW_FIELDS or context.get("schema_version") != (
        SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA
    ):
        _invalid("Scheduler private Preview context fields drifted")
    if str(context.get("origin_authority_kind") or "").strip().upper() != (
        "ACCEPTANCE_EFFECT"
    ):
        _invalid("Scheduler private Preview origin is not an Acceptance Effect")
    for field in ("origin_event_id", "origin_receipt_ref"):
        if not _DURABLE_REF.fullmatch(str(context.get(field) or "").strip()):
            _invalid(f"Scheduler private Preview {field} is invalid")
    boundary_raw = context.get("origin_boundary")
    if not isinstance(boundary_raw, Mapping):
        _invalid("Scheduler private Preview origin boundary is missing")
    boundary = dict(boundary_raw)
    if frozenset(boundary) != _BOUNDARY_FIELDS:
        _invalid("Scheduler private Preview origin boundary fields drifted")
    if (
        boundary.get("action_kind")
        != "acceptance-effect.scheduler.operation.execute"
        or boundary.get("action_args") != dict(canonical)
        or str(boundary.get("task_graph_id") or "").strip()
        != canonical["graph_id"]
        or boundary.get("graph_revision") != canonical["graph_revision"]
        or str(boundary.get("task_node_id") or "").strip()
        != canonical["node_id"]
    ):
        _invalid("Scheduler private Preview origin boundary drifted")
    risk = boundary.get("risk_level")
    if isinstance(risk, bool) or type(risk) is not int or not 1 <= risk <= 5:
        _invalid("Scheduler private Preview risk is invalid")
    if not _SHA256.fullmatch(str(boundary.get("context_pack_digest") or "")):
        _invalid("Scheduler private Preview context digest is invalid")
    if not isinstance(boundary.get("scene_identity"), Mapping):
        _invalid("Scheduler private Preview Scene identity is invalid")

    authority_raw = boundary.get("authority_context")
    node_grant_raw = boundary.get("node_grant")
    if not isinstance(authority_raw, Mapping) or not isinstance(node_grant_raw, Mapping):
        _invalid("Scheduler private Preview authority binding is invalid")
    authority = dict(authority_raw)
    node_grant = dict(node_grant_raw)
    if frozenset(authority) != _AUTHORITY_FIELDS or authority.get(
        "schema_version"
    ) != "Natsume.action_preview.acceptance_effect_authority_request.v1":
        _invalid("Scheduler private Preview effect authority fields drifted")
    fence = authority.get("fencing_token")
    if (
        str(authority.get("effect_id") or "").strip()
        != str(boundary.get("action_scope") or "").strip()
        or not _DURABLE_REF.fullmatch(str(authority.get("effect_id") or "").strip())
        or not str(authority.get("lease_owner_id") or "").strip()
        or isinstance(fence, bool)
        or type(fence) is not int
        or fence < 1
    ):
        _invalid("Scheduler private Preview effect authority drifted")
    if (
        frozenset(node_grant) != _NODE_GRANT_FIELDS
        or str(node_grant.get("grant_ref") or "").strip()
        != str(boundary.get("node_grant_ref") or "").strip()
        or not _DURABLE_REF.fullmatch(str(node_grant.get("grant_ref") or "").strip())
        or not _SHA256.fullmatch(str(node_grant.get("binding_digest") or ""))
        or not _DURABLE_REF.fullmatch(
            str(boundary.get("parent_governor_grant_ref") or "").strip()
        )
    ):
        _invalid("Scheduler private Preview node authority drifted")

    if terminal_proof is not None:
        if not isinstance(terminal_proof, Mapping):
            _invalid("Scheduler terminal Preview proof is invalid")
        proof = dict(terminal_proof)
        if frozenset(proof) != {"service", "event_id", "boundary"} or (
            str(proof.get("event_id") or "").strip()
            != str(context["origin_event_id"])
            or proof.get("boundary") != boundary
        ):
            _invalid("Scheduler terminal Preview proof differs from private context")

    return {
        **context,
        "origin_event_id": str(context["origin_event_id"]).strip(),
        "origin_receipt_ref": str(context["origin_receipt_ref"]).strip(),
        "origin_boundary": deepcopy(boundary),
    }


def _validate_reopened_identity(
    *,
    canonical: Mapping[str, Any],
    preview_context: Mapping[str, Any],
    occurrence: SchedulerOccurrence,
    definition: SchedulerDefinitionSnapshot,
    command: HostCommand,
    work_order: WorkOrderV3,
    graph: TaskGraph,
    node: TaskNode,
    assignment: ExecutorAssignment,
) -> None:
    worker_key = canonical["worker_key"]
    expected_definition_ref = _definition_ref(definition)
    expected_work_order_id = occurrence_work_order_id(occurrence.occurrence_id)
    if (
        command.command_id != canonical["command_id"]
        or command.request_id != canonical["request_id"]
        or command.surface
        not in {HostCommandSurface.SCHEDULER, HostCommandSurface.OPERATOR}
        or command.input_ref != canonical["occurrence_id"]
        or command.root_work_order_id != canonical["work_order_id"]
        or command.graph_id != canonical["graph_id"]
        or command.graph_revision != canonical["graph_revision"]
        or command.route_type != "delegate"
        or str(command.target_id or "").strip().lower() != worker_key
        or command.operation_fingerprint != canonical["operation_fingerprint"]
    ):
        _invalid("accepted Scheduler HostCommand identity drifted")
    if (
        occurrence.occurrence_id != canonical["occurrence_id"]
        or occurrence.request_id != canonical["request_id"]
        or occurrence.definition_revision != canonical["definition_revision"]
        or definition.task_id != occurrence.task_id
        or definition.definition_revision != occurrence.definition_revision
        or definition.worker_key != worker_key
        or canonical["definition_ref"] != expected_definition_ref
    ):
        _invalid("accepted Scheduler occurrence/definition identity drifted")
    pack = work_order.input_pack
    metadata = work_order.metadata
    if (
        canonical["work_order_id"] != expected_work_order_id
        or work_order.work_order_id != canonical["work_order_id"]
        or work_order.root_work_order_id != canonical["work_order_id"]
        or work_order.task_graph_id != canonical["graph_id"]
        or work_order.graph_revision != canonical["graph_revision"]
        or work_order.task_node_id != canonical["node_id"]
        or work_order.operation_fingerprint != canonical["operation_fingerprint"]
        or pack.get("occurrence_id") != canonical["occurrence_id"]
        or pack.get("definition_ref") != canonical["definition_ref"]
        or str(metadata.get("worker_key") or "").strip().lower() != worker_key
        or metadata.get("definition_revision") != canonical["definition_revision"]
        or assignment.work_order_id != canonical["work_order_id"]
    ):
        _invalid("accepted Scheduler WorkOrder identity drifted")
    if command.surface is HostCommandSurface.OPERATOR and (
        str(metadata.get("operator_command_id") or "").strip()
        != "scheduler.task.trigger"
        or str(metadata.get("command_surface") or "").strip()
        != HostCommandSurface.OPERATOR.value
    ):
        _invalid("accepted Operator Scheduler authority drifted")
    if (
        graph.graph_id != canonical["graph_id"]
        or graph.revision != canonical["graph_revision"]
        or graph.root_work_order_id != canonical["work_order_id"]
        or node.node_id != canonical["node_id"]
        or node.route != f"delegate:{worker_key}"
        or occurrence.occurrence_id not in node.input_refs
        or canonical["definition_ref"] not in node.input_refs
    ):
        _invalid("accepted Scheduler TaskGraph/TaskNode identity drifted")

    expected_fingerprint = _operation_fingerprint(
        occurrence=occurrence,
        definition=definition,
        surface=command.surface,
    )
    if expected_fingerprint != canonical["operation_fingerprint"]:
        _invalid("accepted Scheduler operation fingerprint drifted")

    attempt_ids = {
        _attempt_id(
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
            node_id=node.node_id,
            assignment_id=assignment.assignment_id,
            attempt_no=attempt_no,
        )
        for attempt_no in range(1, int(node.retry_policy.get("max_attempts") or 1) + 1)
    }
    boundary = dict(preview_context["origin_boundary"])
    if (
        canonical["attempt_id"] not in attempt_ids
        or boundary.get("action_args") != dict(canonical)
        or boundary.get("risk_level") != node.risk_level
        or boundary.get("parent_governor_grant_ref")
        != command.governor_grant_ref
    ):
        _invalid("accepted Scheduler attempt/Preview identity drifted")


def _exact_result_record(
    value: Any,
    *,
    canonical: Mapping[str, Any],
    expected_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _invalid("Scheduler durable result record is missing")
    record = dict(value)
    result = record.get("result")
    if not isinstance(result, Mapping):
        _invalid("Scheduler durable result has no canonical result object")
    normalized_result = dict(result)
    result_digest = "sha256:" + hashlib.sha256(
        _canonical_json(normalized_result).encode("utf-8")
    ).hexdigest()
    receipt_digest = hashlib.sha256(
        (
            f"{canonical['occurrence_id']}\n{canonical['command_id']}\n"
            f"{result_digest}"
        ).encode("utf-8")
    ).hexdigest()
    expected_receipt = f"schedulerresult://sha256/{receipt_digest}"
    if (
        str(record.get("occurrence_id") or "").strip()
        != canonical["occurrence_id"]
        or str(record.get("command_id") or "").strip()
        != canonical["command_id"]
        or str(record.get("work_order_id") or "").strip()
        != canonical["work_order_id"]
        or str(record.get("result_digest") or "").strip() != result_digest
        or str(record.get("receipt_ref") or "").strip() != expected_receipt
        or str(record.get("result_status") or "").strip().upper()
        != str(normalized_result.get("status") or "").strip().upper()
        or _contains_private_runtime_authority(normalized_result)
        or (
            expected_result is not None
            and normalized_result != dict(expected_result)
        )
    ):
        _invalid("Scheduler immutable result attribution drifted")
    return {**record, "result": normalized_result}


def _handler_outcome(
    record: Mapping[str, Any],
    *,
    replayed: bool,
) -> dict[str, Any]:
    return {
        "receipt_ref": str(record["receipt_ref"]),
        "status": "REPLAYED" if replayed else "APPLIED",
        "scheduler_result_status": str(record["result_status"]),
        "replayed": bool(replayed),
    }


def _operation_fingerprint(
    *,
    occurrence: SchedulerOccurrence,
    definition: SchedulerDefinitionSnapshot,
    surface: HostCommandSurface,
) -> str:
    material: dict[str, Any] = {
        "definition_revision": definition.definition_revision,
        "occurrence_id": occurrence.occurrence_id,
        "payload_sha256": occurrence.payload_sha256,
        "worker_key": definition.worker_key,
    }
    if surface is HostCommandSurface.OPERATOR:
        material.update(
            {
                "command_surface": surface.value,
                "operator_command_id": "scheduler.task.trigger",
            }
        )
    return "sha256:" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()


def _definition_ref(definition: SchedulerDefinitionSnapshot) -> str:
    task_token = re.sub(
        r"[^A-Za-z0-9._~:@/-]+",
        "-",
        str(definition.task_id or "").strip(),
    ).strip("-")
    return (
        f"scheduler-definition://{task_token[:180] or 'scheduler'}/"
        f"{definition.definition_revision}"
    )


def _scheduler_handler_result(value: Any) -> dict[str, Any]:
    """Validate and normalize one explicit Scheduler handler result.

    The real handler may already have crossed its effect boundary before this
    value is inspected.  Missing, empty, ambiguous, or non-serializable
    results therefore cannot be repaired into a synthetic success; the caller
    will move the immutable Acceptance Effect into reconciliation instead.
    """

    if isinstance(value, Mapping):
        result = dict(value)
    else:
        result: dict[str, Any] | None = None
        for method_name in ("as_dict", "to_dict", "model_dump"):
            method = getattr(value, method_name, None)
            if callable(method):
                candidate = method()
                if isinstance(candidate, Mapping):
                    result = dict(candidate)
                    break
    if not result:
        raise SchedulerWorkerError(
            SCHEDULER_DURABLE_RESULT_INVALID,
            "scheduler handler must return a non-empty mapping or serializable object",
        )
    if "status" not in result:
        raise SchedulerWorkerError(
            SCHEDULER_DURABLE_RESULT_INVALID,
            "scheduler handler result requires an explicit status",
        )
    raw = str(result.get("status") or "").strip().upper()
    normalized_status = {
        "OK": "SUCCEEDED",
        "SUCCESS": "SUCCEEDED",
        "DONE": "SUCCEEDED",
        "ACCEPTED": "SUCCEEDED",
        "APPLIED": "SUCCEEDED",
        "COMMITTED": "SUCCEEDED",
        "REPLAYED": "SUCCEEDED",
        "ERROR": "FAILED",
        "REJECTED": "BLOCKED",
        "CONFLICT": "BLOCKED",
        "DEFERRED": "DEGRADED",
    }.get(raw, raw)
    if normalized_status not in {
        "EXECUTED",
        "SUCCEEDED",
        "FAILED",
        "BLOCKED",
        "DEGRADED",
        "SKIPPED",
    }:
        raise SchedulerWorkerError(
            SCHEDULER_DURABLE_RESULT_INVALID,
            f"scheduler handler result status is invalid: {raw or '<empty>'}",
        )
    result["status"] = normalized_status
    if normalized_status == "SKIPPED":
        reason_code = str(result.get("reason_code") or "").strip().upper()
        if not reason_code:
            raise SchedulerWorkerError(
                SCHEDULER_DURABLE_RESULT_INVALID,
                "SKIPPED scheduler handler result requires reason_code",
            )
        result["reason_code"] = reason_code
    try:
        _canonical_json(result)
    except (TypeError, ValueError) as exc:
        raise SchedulerWorkerError(
            SCHEDULER_DURABLE_RESULT_INVALID,
            "scheduler handler result must be canonical JSON serializable",
        ) from exc
    return result


def _attempt_id(
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    assignment_id: str,
    attempt_no: int,
) -> str:
    material = (
        f"{graph_id}@{graph_revision}/{node_id}/{assignment_id}/{attempt_no}"
    )
    return "attempt::governed::" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _contains_private_runtime_authority(value: Any) -> bool:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            if str(raw_key) in {
                _PRIVATE_PREVIEW_FIELD,
                _PRIVATE_TERMINAL_PROOF_FIELD,
                "verified_node_grant",
            }:
                return True
            if _contains_private_runtime_authority(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_private_runtime_authority(child) for child in value)
    return False


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SchedulerWorkerError(
            SCHEDULER_DURABLE_RESULT_INVALID,
            "Scheduler value is not canonical JSON",
        ) from exc


def _require_method(value: Any, name: str) -> None:
    if not callable(getattr(value, name, None)):
        raise TypeError(f"{name} repository method is required")


def _invalid(detail: str) -> None:
    raise SchedulerWorkerError(SCHEDULER_DURABLE_RESULT_INVALID, detail)


__all__ = [
    "SCHEDULER_ACCEPTED_OPERATION_SCHEMA",
    "SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA",
    "SchedulerAcceptedOperationHandler",
]
